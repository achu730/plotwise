"""Cross-checks what is actually drawn in the figure against the uploaded data.

The figure's data are read back from the rendered matplotlib objects (not from
the AI's code), so a hallucinated, dropped, rescaled or altered point shows up
here no matter how the code was written.
"""
import re

import numpy as np
import pandas as pd

from .pwstyle import fit_model, split_header, unit_factor
from .tables import excel_row, where as where_name
from .texttools import readable

SCALES = [1.0, 100.0, 1000.0, 0.01, 0.001, 10.0, 0.1, 1e6, 1e-6, 1e9, 1e-9, 60.0, 1 / 60, 3600.0, 1 / 3600]
RTOL = 1e-6


def _close(a, b):
    # tolerance is relative; the absolute floor follows the plotted value, not the column
    # (a floor scaled by a huge column would let tiny plotted values "match" a zero)
    return np.isclose(a, b, rtol=RTOL, atol=1e-12 * max(1.0, abs(float(b))))


def _err_ok(e, v, f):
    """An error bar's half-length equals the column's uncertainty (sign ignored: HEPData stores 'stat -' as negative)."""
    return abs(e - abs(v) * f) <= 1e-12 * max(1.0, abs(e)) + RTOL * abs(v * f)


def _err_direction(col):
    """'+' for an upper-uncertainty column ('stat +', 'err_up'), '-' for a lower one, else None."""
    c = str(col or "")
    if re.search(r"\+\s*$|[\s_]up\b|plus|\bhigh(er)?\b", c, re.I):
        return "+"
    if re.search(r"[-−]\s*$|[\s_]down\b|[\s_]dn\b|minus|\blow(er)?\b", c, re.I):
        return "-"
    return None


def _err_match(pairs, cand, trials, ys):
    """(fraction, column, factor, why): each error bar must equal the uncertainty in ITS OWN row, at the values'
    unit factor, or scaled by a declared transform / constant (uncertainties take the factor but no offset)."""
    best = None
    for c, v in cand.items():
        is_err = bool(re.search(r"err|unc|sigma|σ|±|stat|syst", str(c), re.I))
        for f, why in trials:
            fr = sum(_err_ok(e, v[r], f) for e, r in pairs) / len(pairs) if pairs else 0.0
            score = (fr, f == ys, is_err)
            if best is None or score > best[0]:
                best = (score, c, f, why)
    return (best[0][0], best[1], best[2], best[3]) if best else (0.0, None, None, None)


def numeric_columns(df):
    out = {}
    for c in df.columns:
        v = pd.to_numeric(df[c], errors="coerce").to_numpy(dtype=float)
        if np.isfinite(v).sum() >= max(1, 0.5 * df[c].notna().sum()) and np.isfinite(v).any():
            out[c] = v
    return out


def _match_frac(vals, col):
    vals = np.asarray([v for v in vals if v is not None], dtype=float)
    if not len(vals):
        return 0.0
    colf = col[np.isfinite(col)]
    if not len(colf):
        return 0.0
    atol = 1e-12 * np.maximum(1.0, np.abs(vals))[:, None]
    hits = (np.abs(vals[:, None] - colf[None, :]) <= atol + RTOL * np.abs(colf[None, :])).any(axis=1)
    return float(hits.mean())


def best_column(vals, cols, extra=()):
    """(column, scale, fraction matched, offset, reason) for the column*scale+offset that explains vals best.
    Plain unit factors are always tried; `extra` adds (factor, offset, reason) from declared transforms
    in the code or from constants in the file."""
    best = (None, None, 0.0, 0.0, None)
    trials = [(f, 0.0, None) for f in SCALES] + list(extra)
    for c, col in cols.items():
        for f, o, why in trials:
            fr = _match_frac(vals, col * f + o)
            if fr > best[2] + 1e-9 or (abs(fr - best[2]) < 1e-9 and fr > 0 and f == 1.0 and o == 0 and best[1] != 1.0):
                best = (c, f, fr, o, why)
    return best


def known_transforms(data, conversions):
    """Factors the checker may accept besides unit changes: transforms the code declared, and constants in the file."""
    out = []
    for cv in conversions or []:
        if cv.get("kind") == "transform":
            out.append((float(cv["factor"]), 0.0, f"declared in code: {cv.get('why') or 'transform'}"))
    for name, df in data.items():
        if name.endswith("constants") and "Value" in df and "Quantity" in df:
            for q, v in zip(df["Quantity"], pd.to_numeric(df["Value"], errors="coerce")):
                if np.isfinite(v) and v != 0:
                    out += [(float(v), 0.0, f"× constant '{q}'"), (1 / float(v), 0.0, f"÷ constant '{q}'")]
    return out


def _conv_text(c):
    return (c.get("why") or "calculation") if c.get("kind") == "transform" else f"{c['from']} → {c['to']}"


def declared(conversions, axis, fac):
    """The code's own record (pw.convert / pw.transform) of scaling this axis by `fac`, or None.
    These declared factors are the ONLY changes to data values Plotwise allows, besides log axes."""
    recs = [c for c in conversions or [] if c.get("axis") in (axis, None) and not c.get("offset")]
    for c in recs:
        if c["factor"] and abs(c["factor"] / fac - 1) < 1e-6:
            return c
    if len(recs) > 1:  # e.g. pixels -> metres with a constant, then m -> cm
        prod = float(np.prod([c["factor"] for c in recs]))
        if prod and abs(prod / fac - 1) < 1e-6:
            return {"kind": "transform", "factor": prod, "why": " then ".join(_conv_text(c) for c in recs)}
    return None


def label_unit(label):
    m = re.search(r"[\[(]\s*([^\])]+?)\s*[\])]\s*$", label or "")
    if m:
        return m.group(1)
    m = re.search(r"/\s*([^\s/]+(?:/c²|/c2|/c)?)\s*$", label or "")
    return m.group(1) if m else None


def check(data, series, result):
    issues, out = [], []
    sheets = {name: (df, numeric_columns(df)) for name, df in data.items()}
    axes = result.get("axes", [])
    totals = dict(points=0, matched=0, unmatched=0, missing=0, empty_cells=0, hidden=0)

    def issue(level, msg, series_idx=None, point=None):
        # LaTeX in labels and column names ("$\\pi^{-}$") is shown as readable text ("π⁻")
        issues.append({"level": level, "message": readable(msg), "series": series_idx, "point": point})

    extra = known_transforms(data, result.get("conversions"))
    changes, seen_changes = [], set()  # the allowed changes to data values in this figure, for the log
    fits = {f["gid"]: f for f in result.get("fits") or []}
    fit_summaries = []
    used_rows = {}  # (sheet, xcol, ycol) -> set of rows already claimed by an earlier series
    for si, s in enumerate(series):
        name = s.get("label") or f"series {si + 1}"
        rep = {"index": si, "label": name, "kind": s["kind"], "n": s["n"], "status": "pass", "axes": s.get("axes", 0),
               "x_source": None, "y_source": None, "err_source": None, "rows": [None] * s["n"],
               "point_ok": [True] * s["n"], "notes": []}
        out.append(rep)
        if s["n"] == 0:
            continue
        if s["kind"] == "fit" and s.get("gid") in fits:
            fs = _check_fit(fits[s["gid"]], s, sheets, extra, result.get("conversions"), name, si, issue)
            rep.update(status=fs.pop("status"), fit=fs, where=fs.get("where"))
            fit_summaries.append(fs)
            if fs.get("change"):
                changes.append(fs["change"])
            continue
        # pick the sheet and columns that explain this series best
        best = None
        for sh, (df, cols) in sheets.items():
            bx = best_column(s["x"], cols, extra)
            by = best_column(s["y"], cols, extra)
            score = bx[2] + by[2]
            if best is None or score > best[0]:
                best = (score, sh, bx, by)
        score, sh, (xc, xs, xf, xo, xwhy), (yc, ys, yf, yo, ywhy) = best
        is_curve = s["kind"] in ("curve", "fit")
        if is_curve and (xf < 0.5 or yf < 0.5):
            if s["n"] <= 2:
                rep["notes"].append("Straight reference line drawn by the code; it is not data.")
                issue("warn", f"{name}: a straight reference line drawn by the code, not taken from the file.", si)
            else:
                rep["notes"].append("Curve that is not in the file (e.g. a fit or model computed by the code).")
                issue("fail", f"{name}: this curve ({s['n']} points) is not in your file, so the code computed it "
                              f"(e.g. a model or smoothing). Plotwise does not let the AI create data. Curves must come "
                              f"from columns in the file, and fits must be made with pw.fit, which draws them as a "
                              f"labelled overlay that is checked and logged.", si)
            rep["status"] = "fail" if s["n"] > 2 else "warn"
            continue
        totals["points"] += s["n"]
        df, cols = sheets[sh]
        place = where_name(df, sh)  # "the CSV file" / "sheet 'Sheet1'"
        rep["where"] = place
        rep["x_source"] = {"sheet": sh, "column": xc, "factor": xs, "offset": xo, "fraction": xf, "why": xwhy}
        rep["y_source"] = {"sheet": sh, "column": yc, "factor": ys, "offset": yo, "fraction": yf, "why": ywhy}
        X = cols[xc] * xs + xo if xc is not None else None
        Y = cols[yc] * ys + yo if yc is not None else None
        key = (sh, xc, yc)
        claimed = used_rows.setdefault(key, set())
        matched, altered, hidden = set(), set(), []
        if X is None or Y is None:
            bad = "x" if X is None else "y"
            issue("fail", f"{name}: the {bad} values match no column of {place}, even allowing declared scaling, so the "
                          f"code changed or computed them. For a log axis use ax.set_{bad}scale('log') instead of taking "
                          f"the log of the values.", si)
        for i, (x, y) in enumerate(zip(s["x"], s["y"])):
            if x is None or y is None or X is None or Y is None:
                rep["point_ok"][i] = False
                if X is None or Y is None:
                    totals["unmatched"] += 1
                continue
            cand = np.where(_close(X, x) & _close(Y, y))[0]
            cand = [int(r) for r in cand if int(r) not in claimed and int(r) not in matched] or [int(r) for r in cand]
            if cand:
                rep["rows"][i] = cand[0]
                matched.add(cand[0])
            else:
                rep["point_ok"][i] = False
                totals["unmatched"] += 1
                # describe the nearest source row so a human can see what changed
                near = np.where(_close(X, x))[0]
                near = [int(r) for r in near if np.isfinite(Y[r]) and r not in matched]
                if near:
                    r = min(near, key=lambda r: abs(Y[r] - y) / max(abs(Y[r]), 1e-300))
                    altered.add(r)
                    dev = (y / Y[r] - 1) * 100 if Y[r] else float("nan")
                    issue("fail", f"{name}: point #{i + 1} is plotted at y = {y:.6g}, but row {excel_row(df, r)} of {place} "
                                  f"gives {Y[r]:.6g} ({dev:+.2f}%). The plotted value does not come from the data.", si, i)
                else:
                    issue("fail", f"{name}: point #{i + 1} at ({x:.6g}, {y:.6g}) does not exist in {place}. "
                                  f"Possible hallucinated or altered point.", si, i)
            px = s["px"][i] if i < len(s["px"]) else None
            if px is None or px[0] is None or px[1] is None:
                hidden.append(i)
        if hidden:
            totals["hidden"] += len(hidden)
            where = ", ".join(f"#{i + 1}" for i in hidden[:8]) + (" …" if len(hidden) > 8 else "")
            if is_curve:
                rep["notes"].append(f"{len(hidden)} curve points are ≤ 0 and not drawn on the log axis.")
            else:
                issue("warn", f"{name}: {len(hidden)} point(s) can't be drawn on this axis (e.g. ≤ 0 on a log scale): {where}.", si)
        totals["matched"] += len(matched)
        claimed |= matched

        # which rows *should* be in this series? Detect a grouping column shared by all matched rows.
        expected = set(range(len(df)))
        group_note = None
        if matched:
            for c in df.columns:
                if c in (xc, yc) or (c in cols and df[c].nunique() > 12):
                    continue
                vals = df[c].iloc[sorted(matched)]
                if vals.nunique(dropna=False) == 1 and df[c].nunique(dropna=False) > 1:
                    v = vals.iloc[0]
                    expected = set(np.where(df[c].to_numpy() == v)[0].tolist()) if pd.notna(v) else expected
                    group_note = f"rows where '{c}' = {v}"
                    break
        if group_note is None and len(series) > 1:
            # several series drawn from the same columns without a group column: others claim their rows
            others = set()
            for k, v in used_rows.items():
                if k == key:
                    others |= (v - matched)
            expected -= others
        missing = sorted(expected - matched - altered) if xc is not None and yc is not None else []
        empty = [r for r in missing if not (np.isfinite(cols[xc][r]) and np.isfinite(cols[yc][r]))] if xc and yc else []
        absent = [r for r in missing if r not in empty]
        totals["empty_cells"] += len(empty)
        totals["missing"] += len(absent)
        for r in empty:
            issue("warn", f"{name}: row {excel_row(df, r)} of {place} has an empty or non-numeric cell in "
                          f"'{xc}' or '{yc}', so it cannot be plotted. Check the source data.", si)
        for r in absent:
            issue("fail", f"{name}: row {excel_row(df, r)} of {place} ({xc} = {df[xc].iloc[r]}, {yc} = {df[yc].iloc[r]}) "
                          f"is in the data but missing from the plot.", si)
        rep["expected_rows"] = len(expected)
        rep["excel_rows"] = [None if r is None else excel_row(df, r) for r in rep["rows"]]
        rep["group"] = group_note

        # error bars
        if s.get("yerr"):
            cand = {c: v for c, v in cols.items() if c not in (xc, yc)}
            trials = [(ys, None)] + [(f, None) for f in SCALES if f != ys] + [(f, why) for f, _, why in extra]
            lo = [None if (e is None or e[0] is None) else e[0] for e in s["yerr"]]
            hi = [None if (e is None or e[1] is None) else e[1] for e in s["yerr"]]
            asym = any(a is not None and b is not None and abs(a - b) > 1e-9 * max(abs(a), abs(b), 1e-300)
                       for a, b in zip(lo, hi))
            # asymmetric bars (e.g. HEPData "stat +" / "stat -") are checked side by side
            sides = [("lower ", lo), ("upper ", hi)] if asym else [("", [None if a is None else (a + b) / 2 for a, b in zip(lo, hi)])]
            for side, errs in sides:
                pairs = [(e, r) for e, r in zip(errs, rep["rows"]) if e is not None and r is not None]
                if not pairs:
                    continue  # no point traced to a row, already reported above
                ef, ec, es, ewhy = _err_match(pairs, cand, trials, ys)
                rep["err_source_upper" if side == "upper " else "err_source"] = \
                    {"sheet": sh, "column": ec, "factor": es, "fraction": ef, "why": ewhy}
                if ef < 0.999:
                    bad = [excel_row(df, r) for e, r in pairs if ec is None or not _err_ok(e, cols[ec][r], es)]
                    where = f" Closest column: '{ec}'; rows that differ: {', '.join(map(str, bad[:8]))}{' …' if len(bad) > 8 else ''}." if ec else ""
                    issue("fail", f"{name}: only {ef:.0%} of the {side}error bars match the uncertainty in their own row of {place}.{where}", si)
                elif abs(es - ys) > 1e-9 * max(es, ys) and declared(result.get("conversions"), "y", es):
                    change = f"{side}y error bars = '{ec}' × {es:.6g} ({_conv_text(declared(result.get('conversions'), 'y', es))})"
                    rep["notes"].append(change + ".")
                    if change not in seen_changes:
                        seen_changes.add(change)
                        changes.append(change)
                        issue("warn", f"Data changed: {change}. This is an allowed scaling, declared in the code and "
                                      f"checked point by point.", si)
                elif abs(es - ys) > 1e-9 * max(es, ys):
                    issue("fail", f"{name}: {side}error bars are scaled by {es:g} but the values by {ys:g}.", si)
            if asym:
                lc, uc = (rep.get("err_source") or {}).get("column"), (rep.get("err_source_upper") or {}).get("column")
                if _err_direction(lc) == "+" or _err_direction(uc) == "-":
                    issue("fail", f"{name}: the downward error bars use '{lc}' and the upward ones '{uc}'; "
                                  f"up and down look swapped.", si)
        else:
            errcols = [c for c in df.columns if c not in (xc, yc) and re.search(r"err|unc|sigma|σ|±|stat|syst", str(c), re.I)]
            if errcols and s["kind"] != "curve":
                issue("warn", f"{name}: {place} has uncertainty column(s) {errcols} but the plot shows no y error bars.", si)

        # units: does the axis label agree with any rescaling?
        ax = axes[s["axes"]] if s["axes"] < len(axes) else {}
        for which, col, fac, off, why in (("x", xc, xs, xo, xwhy), ("y", yc, ys, yo, ywhy)):
            if col is None:
                continue
            src_unit = split_header(col)[1]
            lab_unit = label_unit(ax.get(f"{which}label", ""))
            dec = None
            if abs(fac - 1) > 1e-9 or off:
                dec = None if off else declared(result.get("conversions"), which, fac)
                if dec is None:
                    hint = f" (× {fac:g} equals {why[2:]})" if why and not why.startswith("declared") else ""
                    issue("fail", f"{name}: {which} values are '{col}' × {fac:g}{f' + {off:g}' if off else ''}{hint}, but the code "
                                  f"did not declare this change with pw.convert or pw.transform. The AI may only scale data "
                                  f"by a declared factor.", si)
                    continue
                change = f"{which} = '{col}' × {fac:.6g} ({_conv_text(dec)})"
                if change not in seen_changes:
                    seen_changes.add(change)
                    changes.append(change)
                    msg = f"Data changed: {change}. This is an allowed scaling, declared in the code and checked point by point."
                    if dec.get("kind") != "transform" and not src_unit:
                        msg += (f" The file doesn't give the unit of '{col}', so check that it really is in {dec['from']}.")
                    issue("warn", msg, si)
            if dec is not None and dec.get("kind") == "transform":
                rep["notes"].append(f"{which}: '{col}' × {fac:.6g} ({_conv_text(dec)}).")
                continue
            if src_unit and lab_unit:
                exp = unit_factor(src_unit, lab_unit)
                if exp is None:
                    if fac != 1.0:
                        issue("warn", f"{name}: {which} values are '{col}' × {fac:g}, but the label unit "
                                      f"'{lab_unit}' can't be checked against '{src_unit}'.", si)
                elif abs(exp / fac - 1) > 1e-6:
                    issue("fail", f"{name}: {which} axis says '{lab_unit}' but the values are '{col}' × {fac:g}; "
                                  f"converting {src_unit} → {lab_unit} needs × {exp:g}. Unit label and data disagree.", si)
                else:
                    rep["notes"].append(f"{which}: '{col}' × {fac:g} matches the {src_unit} → {lab_unit} unit change." if fac != 1
                                        else f"{which}: '{col}', unit {lab_unit} as in the source.")

        lvl = {i["level"] for i in issues if i["series"] == si}
        rep["status"] = "fail" if "fail" in lvl else "warn" if "warn" in lvl else "pass"

    for ai, a in enumerate(axes):
        for which in ("x", "y"):
            sc = a.get(f"{which}scale") or "linear"
            panel = f" (panel {ai + 1})" if len(axes) > 1 else ""
            if sc in ("log", "symlog"):
                changes.append(f"{which} axis{panel} on a {sc} scale")
                issue("warn", f"Display change: the {which} axis{panel} uses a {sc} scale. The plotted values themselves "
                              f"are unchanged.")
            elif sc != "linear":
                issue("fail", f"The {which} axis{panel} uses a '{sc}' scale. Only linear and log axes are allowed.")

    status = "fail" if any(i["level"] == "fail" for i in issues) else "warn" if issues else "pass"
    for rep in out:
        rep["label_text"] = readable(rep["label"])
    return {"status": status, "totals": totals, "series": out, "issues": issues,
            "data_changes": [readable(c) for c in changes], "fits": fit_summaries}


def _check_fit(f, s, sheets, extra, conversions, name, si, issue):
    """A pw.fit line is allowed as an overlay when (1) its input points come from the file, (2) re-doing the fit
    gives the same parameters and (3) the drawn line is that model. It is listed as a change and a warning."""
    out = {k: f.get(k) for k in ("name", "kind", "degree", "equation", "params", "errors", "chi2", "ndf", "r2",
                                 "n_points", "weighted", "fit_range")}
    best = None
    for sh, (df, cols) in sheets.items():
        bx, by = best_column(f["x"], cols, extra), best_column(f["y"], cols, extra)
        if best is None or bx[2] + by[2] > best[0]:
            best = (bx[2] + by[2], sh, bx, by)
    _, sh, (xc, xs, xf, xo, _), (yc, ys, yf, yo, _) = best
    df, cols = sheets[sh]
    place = where_name(df, sh)
    out.update(where=place, x_column=xc, y_column=yc)
    group = ""
    if xc is not None and yc is not None:  # e.g. one fit per trial: name the rows it used
        X, Y = cols[xc] * xs + xo, cols[yc] * ys + yo
        used = sorted({int(r) for x, y in zip(f["x"], f["y"]) for r in np.where(_close(X, x) & _close(Y, y))[0]})
        for c in df.columns:
            if c in (xc, yc) or (c in cols and df[c].nunique() > 12) or not used:
                continue
            vals = df[c].iloc[used]
            if vals.nunique(dropna=False) == 1 and df[c].nunique(dropna=False) > 1 and pd.notna(vals.iloc[0]):
                group = f" where '{c}' = {vals.iloc[0]}"
                break
    bad = []
    if xf < 0.999 or yf < 0.999:
        bad.append(f"the points it was fitted to are not all in your file ({min(xf, yf):.0%} found in {place})")
    for which, fac, off in (("x", xs, xo), ("y", ys, yo)):
        if fac is not None and (abs(fac - 1) > 1e-9 or off) and not (not off and declared(conversions, which, fac)):
            bad.append(f"its {which} values are scaled by {fac:g} without a declared pw.convert / pw.transform")
    try:
        _, p, _, model = fit_model(f["kind"], f["degree"], f["x"], f["y"], f["yerr"])
        rec = np.array(list(f["params"].values()), dtype=float)
        tol = 1e-6 * np.abs(p) + 1e-9 * np.maximum(np.abs(np.array(list(f["errors"].values()), dtype=float)), 1e-300)
        if not np.all(np.abs(p - rec) <= tol):
            bad.append("re-doing the fit gives different parameters from the ones reported")
        xy = [(x, y) for x, y in zip(s["x"], s["y"]) if x is not None and y is not None]
        if xy:
            X, Y = np.array(xy, dtype=float).T
            if not np.allclose(model(X, *rec), Y, rtol=1e-6, atol=1e-12 * max(1.0, float(np.abs(Y).max()))):
                bad.append("the line drawn is not the fitted model")
    except Exception as e:  # noqa: BLE001
        bad.append(f"the fit could not be re-done ({e})")
    stat = (f"χ²/ndf = {f['chi2']:.1f}/{f['ndf']}" if f.get("chi2") is not None
            else f"R² = {f['r2']:.4f}" if f.get("r2") is not None else "")
    lo, hi = f["fit_range"]
    rows = f"{f['n_points']} points of '{yc}' vs '{xc}' from {place}{group}, x from {lo:.4g} to {hi:.4g}"
    scaled = [f"{w} = file value × {fac:g}" for w, fac in (("x", xs), ("y", ys)) if fac is not None and abs(fac - 1) > 1e-9]
    if scaled:  # the equation is in the plotted units, e.g. cm after "use cm"
        rows += f", in the plotted units ({', '.join(scaled)})"
    label = name if name and not name.startswith(f["name"]) else f["name"]
    fname = f["name"] if f["kind"] == "gauss" else f["name"][0].lower() + f["name"][1:]
    if bad:
        issue("fail", f"{label}: this {fname} can't be trusted: " + "; ".join(bad) + ".", si)
        out["status"] = "fail"
        return out
    out["change"] = f"{f['name']} added over the data ({rows}): {f['equation']}" + (f", {stat}" if stat else "")
    issue("warn", f"Fit added: {fname} to {rows}. {f['equation']}" + (f", {stat}" if stat else "")
          + ". Plotwise re-did the fit and got the same result. The fit is drawn as a line over your data; "
            "the data points are unchanged.", si)
    out["status"] = "warn"
    return out


def spot_points(series_report, series, k=3, rng=None, include_second=True):
    """Pick points for a human spot check: the 2nd point of the first data series plus random ones."""
    rng = rng or np.random.default_rng()
    pool = [(r["index"], i) for r in series_report if r["kind"] not in ("curve", "fit") and r.get("x_source")
            for i in range(r["n"])]
    picks = []
    if include_second and pool:
        first = pool[0][0]
        if any(p == (first, 1) for p in pool):
            picks.append((first, 1))
    rest = [p for p in pool if p not in picks]
    if rest:
        idx = rng.choice(len(rest), size=min(k - len(picks), len(rest)), replace=False)
        picks += [rest[i] for i in sorted(idx)]
    return picks
