"""Offline fallback: no AI. Parses simple requests into a plot spec and writes code from it.

Used when no Claude backend is reachable, so Plotwise keeps working without internet.
"""
import difflib
import json
import re

import pandas as pd

from .pwstyle import UNITS, norm_unit, pretty_unit, split_header

ERR_RE = re.compile(r"err|unc|sigma|σ|±|stat|syst", re.I)
FIT_RE = re.compile(r"fit|model|pdf|theory|prediction|signal|background|total", re.I)


def _tokens(s):
    return set(re.findall(r"[a-z0-9]+", str(s).lower()))


def _numeric(df, c):
    return pd.to_numeric(df[c], errors="coerce").notna().mean() > 0.5


def match_column(text, df, exclude=()):
    best, score = None, 0.0
    # "y axis" is filler, but the y in "y position" tells it apart from "x position"
    t = (_tokens(re.sub(r"\b[xy][- ]?axis\b", " ", str(text), flags=re.I)) - {"axis", "the", "on", "plot", "of"}) or _tokens(text)
    for c in df.columns:
        if c in exclude or not _numeric(df, c) or ERR_RE.search(str(c)):
            continue
        name, unit = split_header(c)
        ct = _tokens(name) | (_tokens(unit) if unit else set())
        jac = len(t & ct) / max(1, len(t | ct))
        ratio = difflib.SequenceMatcher(None, " ".join(sorted(t)), " ".join(sorted(ct))).ratio()
        sc = jac + 0.5 * ratio
        if sc > score:
            best, score = c, sc
    return best if score > 0.25 else None


def find_err(df, col):
    name_t = _tokens(split_header(col)[0])
    cands = [c for c in df.columns if c != col and ERR_RE.search(str(c)) and _numeric(df, c)]
    cands.sort(key=lambda c: -len(name_t & _tokens(c)))
    return cands[0] if cands else None


def new_spec(request, data):
    """Turn 'y axis (y displacement) vs x axis (time)' into a spec."""
    sheet = next(iter(data))
    df = data[sheet]
    parts = re.split(r"\s+(?:vs\.?|versus|against)\s+", request, flags=re.I)
    if len(parts) == 2:
        ytxt, xtxt = [(re.search(r"\(([^)]+)\)", p) or re.match(r"(.*)", p)).group(1) for p in parts]
        y, x = match_column(ytxt, df), match_column(xtxt, df)
        if y and not x:
            x = match_column(xtxt, df, exclude=(y,))
        if y and not x:  # fall back to the first other numeric, non-error column
            x = next((c for c in df.columns if c != y and _numeric(df, c) and not ERR_RE.search(str(c))), None)
    else:
        # HEPData tables carry bin edges ("... LOW", "... HIGH") next to the bin centre: not a y candidate
        nums = [c for c in df.columns if _numeric(df, c) and not ERR_RE.search(str(c))
                and not re.search(r"\s(LOW|HIGH)$", str(c))]
        x, y = (nums + [None, None])[:2]
    if not x or not y:
        raise ValueError("Offline mode could not tell which columns you mean. Try 'Y column vs X column' "
                         f"using these names: {', '.join(map(str, df.columns))}")
    groups = [c for c in df.columns if not _numeric(df, c) and 1 < df[c].nunique() <= 8]
    curves = []
    for sh, other in data.items():
        if sh == sheet or x not in other.columns:
            continue
        for c in other.columns:
            if c != x and _numeric(other, c):
                curves.append({"sheet": sh, "x": x, "y": c, "label": split_header(c)[0]})
    is_hist = bool(re.search(r"candidates|counts|entries|events", str(y), re.I))
    fit = parse_fit(request.lower())
    return {"sheet": sheet, "x": x, "y": y, "yerr": find_err(df, y), "xerr": None,
            "group": groups[0] if groups else None, "curves": curves, "hist_style": is_hist,
            "x_to": None, "y_to": None, "xname": None, "yname": None, "xlog": False, "ylog": False,
            "legend": True, "legend_loc": None, "title": None, "grid": None, "xlim": None, "ylim": None,
            "errorbars": True, "experiment_extra": None, "data_label": "Data", "fit": fit, "xstep": None, "ystep": None}


FIT_REQ = [(r"\b(?:linear|straight[- ]line)\s+(?:fit|trend ?line|regression)|\bfit\s+(?:a\s+)?(?:straight\s+)?line\b|\blinear regression\b|\bfit\s+(?:it\s+)?linear(?:ly)?\b", "linear"),
           (r"\bquadratic\b", "quadratic"), (r"\bcubic\b", "cubic"),
           (r"\bpoly(?:nomial)?\b", "poly"), (r"\bexponential\b", "exp"), (r"\blog(?:arithmic)?\s+(?:fit|trend ?line)\b", "log"),
           (r"\bpower(?:[- ]law)?\s+(?:fit|trend ?line)\b", "power"), (r"\bgauss(?:ian)?\b|\bpeak\s+fit\b|\bnormal\s+distribution\s+fit\b", "gauss")]


def parse_fit(r):
    """'add a quadratic fit from 2 to 8' -> {'kind': 'poly', 'degree': 2, 'range': [2, 8]}; None if no fit asked."""
    if not re.search(r"\bfit|trend ?line|regression", r):
        return None
    for pat, kind in FIT_REQ:
        if re.search(pat, r):
            deg = {"quadratic": 2, "cubic": 3}.get(kind)
            m = re.search(r"(?:degree|order|deg\.?)\s*(?:of\s*)?(\d)|(\d)(?:st|nd|rd|th)[- ](?:degree|order)", r)
            if kind == "poly":
                deg = int(m.group(1) or m.group(2)) if m else 2
            out = {"kind": "poly" if kind in ("poly", "quadratic", "cubic") else kind, "degree": deg}
            m = re.search(r"\bfit\b[^0-9-]*?(?:from|between|range|for x)\s*(-?[\d.]+(?:e-?\d+)?)\s*(?:to|-|and|–)\s*(-?[\d.]+(?:e-?\d+)?)", r)
            if m:
                out["range"] = [float(m.group(1)), float(m.group(2))]
            if re.search(r"equation|formula", r):
                out["show_equation"] = True
            return out
    return None


POS = {"top": "upper", "upper": "upper", "bottom": "lower", "lower": "lower",
       "centre": "center", "center": "center", "middle": "center"}


def edit_spec(spec, request, data):
    s = dict(spec)
    r = request.lower().strip()
    done = []
    if re.search(r"log[- ]?log", r):
        s["xlog"] = s["ylog"] = True
        done.append("log scale on both axes")
    for ax in ("x", "y"):
        if re.search(rf"\blog(arithmic)?\b[^,.;]*\b{ax}\b|\b{ax}\b[^,.;]*\blog", r) and not re.search(r"log[- ]?log", r):
            s[f"{ax}log"] = True
            done.append(f"log {ax} axis")
        if re.search(rf"\blinear\b[^,.;]*\b{ax}\b|\b{ax}\b[^,.;]*\blinear", r):
            s[f"{ax}log"] = False
            done.append(f"linear {ax} axis")
    if "legend" in r:
        if re.search(r"(remove|hide|no|without)\s+(the\s+)?legend", r):
            s["legend"] = False
            done.append("legend removed")
        else:
            v = next((POS[w] for w in POS if re.search(rf"\b{w}\b", r)), "")
            h = next((w for w in ("left", "right") if w in r), "")
            loc = " ".join(p for p in (v, h) if p) or ("center" if "center" in r else None)
            if loc == "center center":
                loc = "center"
            if loc:
                s["legend"], s["legend_loc"] = True, loc
                done.append(f"legend at {loc}")
    m = re.search(r"\b(?:use|in|into|to|convert[^,.;]*?to)\s+([a-zµμ]+(?:/c2|/c\^2|/c²|/c)?)\b", r)
    if m and norm_unit(m.group(1)) in UNITS:
        target = m.group(1)
        dim = UNITS[norm_unit(target)][0]
        axes = [a for a in ("x", "y") if re.search(rf"\b{a}\b", r)] or ["x", "y"]
        for a in axes:
            src = split_header(s[a])[1]
            if not src or norm_unit(src) not in UNITS:
                continue
            sdim = UNITS[norm_unit(src)][0]
            t = target
            if sdim == "mass" and dim == "energy":  # "use GeV" on a MeV/c^2 axis
                t, dim_t = target + "/c^2", "mass"
            else:
                dim_t = dim
            if sdim == dim_t:
                t = pretty_unit(t)  # the request was lowercased: "gev/c^2" -> "GeV/c²"
                s[f"{a}_to"] = t
                s[f"{a}label"] = None
                done.append(f"{a} axis in {t}")
    m = re.search(r"(?:rename\s+(?:the\s+)?)?\b([xy])[- ]?(?:axis)?\s*(?:label|title)?\s*(?:to|=|:|as)\s*['\"“]?(.+?)['\"”]?$", request, re.I)
    if m and re.search(r"label|rename|call", r):
        s[f"{m.group(1).lower()}name"] = m.group(2).strip()
        s[f"{m.group(1).lower()}label"] = None
        done.append(f"{m.group(1).lower()} label → {m.group(2).strip()}")
    if re.search(r"(remove|no|hide|delete)\s+(the\s+)?title", r):
        s["title"] = None
        done.append("title removed")
    else:
        m = re.search(r"\btitle\s*(?:to|=|:)?\s*['\"“](.+?)['\"”]", request, re.I)
        if m:
            s["title"] = m.group(1)
            done.append(f"title '{m.group(1)}' (note: most journals put this in the caption)")
    if re.search(r"(no|remove|hide|without)\s+grid", r):
        s["grid"] = False
        done.append("grid off")
    elif re.search(r"\bgrid\b", r):
        s["grid"] = True
        done.append("grid on")
    for ax in ("x", "y"):
        m = re.search(rf"\b{ax}\b[^0-9-]*(?:range|limits?|lim|from)[^0-9-]*(-?[\d.]+(?:e-?\d+)?)\s*(?:to|-|,|and)\s*(-?[\d.]+(?:e-?\d+)?)", r)
        if m:
            s[f"{ax}lim"] = [float(m.group(1)), float(m.group(2))]
            done.append(f"{ax} range {m.group(1)}–{m.group(2)}")
    if re.search(r"(no|remove|hide|without)\s+error\s*bars?", r):
        s["errorbars"] = False
        done.append("error bars hidden")
    elif re.search(r"(show|add)\s+error\s*bars?", r):
        s["errorbars"] = True
        done.append("error bars shown")
    if re.search(r"(remove|delete|hide|no|without)\s+(the\s+)?(fit|trend ?line)\b(?!\s*equation)", r):
        s["fit"] = None
        done.append("fit removed")
    else:
        fit = parse_fit(r)
        if fit:
            s["fit"] = fit
            kind = {"exp": "exponential", "log": "logarithmic", "gauss": "Gaussian",
                    "poly": f"degree {fit['degree']} polynomial"}.get(fit["kind"], fit["kind"])
            done.append(f"{kind} fit" + (f" for x from {fit['range'][0]:g} to {fit['range'][1]:g}" if fit.get("range") else "")
                        + (" with its equation" if fit.get("show_equation") else ""))
        elif re.search(r"equation|formula", r):  # "show the equation of the fit"
            if not s.get("fit"):
                raise ValueError("There is no fit yet to show an equation for. Ask for one first, "
                                 "e.g. 'add a linear fit with its equation'.")
            hide = bool(re.search(r"(remove|hide|no|without|delete)\s+(the\s+)?(fit\s+)?(equation|formula)", r))
            s["fit"] = dict(s["fit"], show_equation=not hide)
            done.append("fit equation hidden" if hide else "fit equation shown in the legend")
    for word in ("preliminary", "simulation", "unofficial"):
        if word in r:
            s["experiment_extra"] = word.capitalize()
            done.append(f"'{word.capitalize()}' added to the experiment label")
    if not done and FORBIDDEN.search(r):
        raise ValueError("Plotwise does not allow that: the only changes to the data are scaling by a factor (units or a "
                         "constant from the file), log axes and fits drawn over the data. Smoothing, normalising, "
                         "offsets and removing rows are not allowed. Use an axis range to zoom instead.")
    if not done and re.search(r"\bfit", r):
        raise ValueError("Plotwise can add a linear, polynomial (degree 2-6), exponential, logarithmic, power or "
                         "Gaussian fit, e.g. 'add a quadratic fit'.")
    if not done:
        raise ValueError("Offline mode didn't understand that edit. It knows: log x/y, legend position, "
                         "units (use cm, in ms...), x/y label to ..., title, grid, x/y range a to b, error bars, "
                         "linear / polynomial / exponential / Gaussian fit, Preliminary. Connect Claude for anything else.")
    return s, done


# requests that would change data values beyond scaling (refused, as in the Claude prompt)
FORBIDDEN = re.compile(r"smooth|moving average|rolling|normali[sz]|interpolat|subtract|offset|shift|"
                       r"(remove|drop|delete|filter|exclude)\b.*\b(rows?|points?|outliers?)|average|rebin|resampl", re.I)


def _conv(expr, src_unit, to, axis):
    if not to or not src_unit:
        return expr
    return f'pw.convert({expr}, "{src_unit}", "{to}", axis="{axis}")'


def spec_to_code(spec, request_note=""):
    xs_name, xs_unit = split_header(spec["x"])
    ys_name, ys_unit = split_header(spec["y"])
    xunit, yunit = spec.get("x_to") or xs_unit, spec.get("y_to") or ys_unit
    xname, yname = spec.get("xname") or xs_name, spec.get("yname") or ys_name
    L = [f"# Plotwise offline engine (no AI). Spec: {json.dumps({k: v for k, v in spec.items() if v not in (None, False, [], '')}, ensure_ascii=False)}",
         f'd = data[{spec["sheet"]!r}]',
         f'X, Y = {spec["x"]!r}, {spec["y"]!r}']
    if spec.get("yerr") and spec.get("errorbars", True):
        L.append(f'E = {spec["yerr"]!r}')
    L.append("fig, ax = pw.figure()")
    err = (f', yerr={_conv("pd.to_numeric(g[E], errors=\'coerce\')", ys_unit, spec.get("y_to"), "y")}'
           if spec.get("yerr") and spec.get("errorbars", True) else "")
    xv = _conv("pd.to_numeric(g[X], errors='coerce')", xs_unit, spec.get("x_to"), "x")
    yv = _conv("pd.to_numeric(g[Y], errors='coerce')", ys_unit, spec.get("y_to"), "y")
    fit = spec.get("fit")
    fit_call = ""
    if fit:
        args = [f'kind="{fit["kind"]}"'] + ([f"degree={fit['degree']}"] if fit["kind"] == "poly" else []) \
            + ([f"fit_range=({fit['range'][0]!r}, {fit['range'][1]!r})"] if fit.get("range") else [])
        fit_call = "pw.fit(ax, {x}, {y}" + (", yerr={e}" if err else "") + ", " + ", ".join(args) + "{color})"
    ev = _conv("pd.to_numeric(g[E], errors='coerce')", ys_unit, spec.get("y_to"), "y")
    if spec.get("group"):
        L += [f'for i, (name, g) in enumerate(d.groupby({spec["group"]!r}, sort=False)):',
              f"    ax.errorbar({xv}, {yv}{err}, **pw.data_style(i), label=str(name))"]
        if fit:
            call = fit_call.format(x=xv, y=yv, e=ev, color=", color=pw.data_style(i)['color'], of=str(name)"
                                   + (", show_equation=True" if fit.get("show_equation") else ""))
            L += [f"    if i == {fit['series']}:", "        " + call] if fit.get("series") is not None else ["    " + call]
    else:
        L += ["g = d", f"ax.errorbar({xv}, {yv}{err}, **pw.data_style(0), label={spec.get('data_label', 'Data')!r})"]
        if fit:
            L.append(fit_call.format(x=xv, y=yv, e=ev, color=", show_equation=True" if fit.get("show_equation") else ""))
    for i, c in enumerate(spec.get("curves") or []):
        cx = _conv(f"data[{c['sheet']!r}][{c['x']!r}]", xs_unit, spec.get("x_to"), "x")
        cy = _conv(f"data[{c['sheet']!r}][{c['y']!r}]", ys_unit, spec.get("y_to"), "y") if split_header(c["y"])[1] == ys_unit else f"data[{c['sheet']!r}][{c['y']!r}]"
        L.append(f"ax.plot({cx}, {cy}, **pw.line_style({i}), label={c['label']!r})")
    L.append(f"ax.set_xlabel({spec['xlabel']!r})" if spec.get("xlabel") else f"ax.set_xlabel(pw.label({xname!r}, {xunit!r}))")
    L.append(f"ax.set_ylabel({spec['ylabel']!r})" if spec.get("ylabel") else f"ax.set_ylabel(pw.label({yname!r}, {yunit!r}))")
    if spec.get("xlog"):
        L.append('ax.set_xscale("log")')
    if spec.get("ylog"):
        L.append('ax.set_yscale("log")')
    if spec.get("xlim"):
        L.append(f"ax.set_xlim({spec['xlim'][0]!r}, {spec['xlim'][1]!r})")
    if spec.get("ylim"):
        L.append(f"ax.set_ylim({spec['ylim'][0]!r}, {spec['ylim'][1]!r})")
    elif spec.get("hist_style"):
        L.append("ax.set_ylim(bottom=0.5)" if spec.get("ylog") else "ax.set_ylim(bottom=0)")
    for a in ("x", "y"):
        if spec.get(f"{a}step"):
            L.append(f"ax.{a}axis.set_major_locator(plt.MultipleLocator({spec[a + 'step']}))")
    if spec.get("grid") is not None:
        L.append(f"ax.grid({spec['grid']})")
    if spec.get("title"):
        L.append(f"ax.set_title({spec['title']!r})")
    if spec.get("legend", True):
        L.append(f"pw.legend(ax, loc={spec.get('legend_loc')!r})")
    L.append(f"pw.experiment_label(ax, extra={spec.get('experiment_extra')!r})")
    return "\n".join(L) + "\n"


FIG_WORDS = re.compile(r"figure|figs?\b|font|typeface|\bpt\b|point size|\bmm\b|width|axis|axes|label|unit|legend|"
                       r"colou?r|error bar|title|caption|vector|\bpdf\b|\beps\b|resolution|dpi|line width|marker|tick", re.I)


def style_from_text(text, base):
    """No-AI reading of a style guide: keep the sentences about figures and pick out a few values."""
    style = json.loads(json.dumps(base))
    style.update(id="custom", name="", source="Read without AI (keyword rules).", rules=[])
    text = re.sub(r"(?<![.!?:])\s*\n\s*(?=[a-z(])", " ", text)  # re-join lines wrapped mid-sentence
    title = next((x.strip() for x in text.splitlines() if x.strip()), "")
    if len(title) < 120 and not title.endswith("."):  # first line is the document's title, not a rule
        style["name"] = re.split(r"\s+[-–—:]\s+", title)[0]
        text = text.replace(title, "", 1)
    safe = re.sub(r"\b(e\.g|i\.e|etc|approx|cf|vs|Fig|Eq)\.", lambda m: m.group(1) + "\u2024", text)
    sentences = [re.sub(r"^[\s\-•*\d.)]+", "", x).strip().replace("\u2024", ".")
                 for x in re.split(r"(?<=[.!?])\s+|\n+", safe)]
    rules = [x for x in sentences if 15 < len(x) < 300 and FIG_WORDS.search(x) and not x.lower().startswith("note")]
    style["rules"] = rules[:12] or ["The document did not contain recognisable figure rules."]
    t = text.lower()
    m = re.search(r"single[- ]column[^.\n]*?(\d{2,3})\s*mm", t) or re.search(r"(\d{2,3})\s*mm\s*(?:wide|width)", t)
    if m:
        style["figure"]["width_mm"] = int(m.group(1))
    if re.search(r"arial|helvetica|sans-serif", t):
        style["font"].update(family="sans-serif", names=["Arial", "Helvetica", "Liberation Sans", "DejaVu Sans"], mathtext="dejavusans")
    elif re.search(r"times|serif", t):
        style["font"].update(family="serif", names=["Times New Roman", "Nimbus Roman", "Liberation Serif", "DejaVu Serif"], mathtext="stix")
    m = re.search(r"(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*pt", t)
    if m:
        lo = int(m.group(1))
        style["font"].update(size_pt=lo, tick_size_pt=lo, label_size_pt=lo + 1)
        style["legend"]["fontsize_pt"] = max(lo - 1, 6)
    if re.search(r"units? in (?:square )?brackets|\[\s*(?:mev|gev|s|m)\b", t):
        style["labels"]["unit_style"] = "brackets"
    elif re.search(r"units? in parenthes|unit in parenthes|\(s\)\"", t):
        style["labels"]["unit_style"] = "parentheses"
    if re.search(r"ticks? inside|inward ticks", t):
        style["axes"]["ticks_direction"] = "in"
    if re.search(r"all (?:four )?sides", t):
        style["axes"]["ticks_all_sides"] = True
    m = re.search(r"[\"“']([A-Za-z]{2,12})[\"”'] label", text)
    if m:
        style["experiment_label"] = {"text": m.group(1), "loc": "upper right", "fontsize_pt": style["font"]["size_pt"] + 4,
                                     "style": "normal"}
    note = f"Read the guide without AI and kept {len(rules[:12])} figure rule(s). Connect Claude for a full reading."
    return style, note
