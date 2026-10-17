"""Runs one generated plot script in a separate process.

Usage: python harness.py JOB_DIR
JOB_DIR holds code.py, data.pkl ({sheet: DataFrame}) and style.json.
Writes figure.png/.pdf/.svg, points.json and result.json into JOB_DIR.
"""
import io
import json
import math
import os
import sys
import traceback
import warnings

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.container import BarContainer, ErrorbarContainer  # noqa: E402
from matplotlib.collections import PathCollection  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pwstyle import Plotwise  # noqa: E402

PREVIEW_DPI = 150


def clean(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def err_from_collections(xy, colls):
    """Return (xerr, yerr) arrays [[lo, hi], ...] read back from errorbar LineCollections."""
    n = len(xy)
    xerr = yerr = None
    for lc in colls:
        segs = lc.get_segments()
        if len(segs) != n:
            continue
        segs = [np.asarray(s, dtype=float) for s in segs]
        ok = [s for s in segs if s.ndim == 2 and len(s) and np.isfinite(s).all()]
        vertical = all(abs(s[0][0] - s[-1][0]) <= 1e-12 * max(1, abs(s[0][0])) for s in ok)
        arr = []
        for (x, y), s in zip(xy, segs):
            if s.ndim != 2 or not len(s) or not np.isfinite(s).all():
                arr.append([None, None])
            elif vertical:
                arr.append([clean(y - s[:, 1].min()), clean(s[:, 1].max() - y)])
            else:
                arr.append([clean(x - s[:, 0].min()), clean(s[:, 0].max() - x)])
        if vertical:
            yerr = arr
        else:
            xerr = arr
    return xerr, yerr


def extract(fig):
    fig.canvas.draw()
    H = fig.bbox.height
    series = []
    for ai, ax in enumerate(fig.axes):
        consumed = set()

        def to_px(xy):
            if len(xy) == 0:
                return []
            with np.errstate(all="ignore"):
                d = ax.transData.transform(np.asarray(xy, dtype=float))
            return [[clean(px), clean(H - py)] for px, py in d]

        def add(kind, label, xy, xerr=None, yerr=None, artist=None):
            xy = np.asarray(xy, dtype=float).reshape(-1, 2) if len(xy) else np.zeros((0, 2))
            series.append({
                "axes": ai, "kind": kind,
                "label": None if (label is None or str(label).startswith("_")) else str(label),
                "n": int(len(xy)),
                "x": [clean(v) for v in xy[:, 0]], "y": [clean(v) for v in xy[:, 1]],
                "xerr": xerr, "yerr": yerr, "px": to_px(xy),
                "marker": getattr(artist, "get_marker", lambda: None)() if artist is not None else None,
                "gid": artist.get_gid() if artist is not None else None,
            })

        for c in ax.containers:
            if isinstance(c, ErrorbarContainer):
                data_line, caplines, barcols = c.lines
                if data_line is not None:
                    xy = data_line.get_xydata()
                    consumed.add(id(data_line))
                else:
                    xy = np.zeros((0, 2))
                for cl in caplines:
                    consumed.add(id(cl))
                xerr, yerr = err_from_collections(np.asarray(xy), barcols)
                add("points", c.get_label(), xy, xerr, yerr, data_line)
            elif isinstance(c, BarContainer):
                xy, yerr = [], None
                for p in c.patches:
                    consumed.add(id(p))
                    xy.append([p.get_x() + p.get_width() / 2, p.get_y() + p.get_height()])
                if c.errorbar is not None:
                    dl, cls_, bcs = c.errorbar.lines
                    _, yerr = err_from_collections(np.asarray(xy), bcs)
                add("bars", c.get_label(), xy, None, yerr)

        for ln in ax.lines:
            if id(ln) in consumed:
                continue
            if ln.get_transform() != ax.transData:
                continue  # axhline / axvline style reference lines
            marker = ln.get_marker()
            has_marker = marker not in (None, "", "None", " ", "none")
            has_line = ln.get_linestyle() not in ("None", "", " ", "none")
            kind = "points" if has_marker and not has_line else "curve"
            if str(ln.get_gid() or "").startswith("pw-fit-"):
                kind = "fit"  # drawn by pw.fit; the checker re-does the fit to confirm it
            add(kind, ln.get_label(), ln.get_xydata(), artist=ln)

        for coll in ax.collections:
            if isinstance(coll, PathCollection) and id(coll) not in consumed:
                off = coll.get_offsets()
                add("points", coll.get_label(), np.asarray(off))
    return series


def _bbox_px(ax, fig):
    """The axes' plotting area in figure.png pixels, origin top-left: [left, top, right, bottom]."""
    b = ax.get_window_extent()
    H = fig.bbox.height
    return [clean(b.x0), clean(H - b.y1), clean(b.x1), clean(H - b.y0)]


def style_lint(fig, style, pw):
    checks = []

    def chk(ok, name, detail, level="warn"):
        checks.append({"ok": bool(ok), "name": name, "detail": detail, "level": "pass" if ok else level})

    f = style.get("figure", {})
    w_mm = fig.get_figwidth() * 25.4
    if f.get("width_mm"):
        chk(abs(w_mm - f["width_mm"]) < 2, "Figure width",
            f"{w_mm:.0f} mm (style asks for {f['width_mm']} mm)")
    unit_style = (style.get("labels") or {}).get("unit_style", "brackets")
    want_title = (style.get("axes") or {}).get("title", True)
    import re
    for ai, ax in enumerate(fig.axes):
        tag = f"axes {ai + 1}: " if len(fig.axes) > 1 else ""
        if not want_title:
            chk(not ax.get_title(), "No in-figure title",
                f"{tag}title is {ax.get_title()!r}" if ax.get_title() else f"{tag}no title (caption carries it)")
        for which in ("x", "y"):
            lab = getattr(ax, f"get_{which}label")()
            chk(bool(lab.strip()), f"{which.upper()} axis label", f"{tag}{lab!r}" if lab else f"{tag}missing", "fail")
            if not lab:
                continue
            has_br, has_par = bool(re.search(r"\[[^\]]+\]\s*$", lab)), bool(re.search(r"\([^)]+\)\s*$", lab))
            per_bin = bool(re.search(r"/\s*\(\s*[\d.]+\s*\S*\s*\)\s*$", lab))  # "Candidates / (5 MeV/c²)"
            if per_bin:
                chk(True, f"{which.upper()} unit format", f"{tag}{lab!r} (per-bin label)")
            elif unit_style == "brackets" and has_par and not has_br:
                chk(False, f"{which.upper()} unit format", f"{tag}{lab!r} uses ( ) but the style wants [unit]")
            elif unit_style == "parentheses" and has_br:
                chk(False, f"{which.upper()} unit format", f"{tag}{lab!r} uses [ ] but the style wants (unit)")
            elif has_br or has_par:
                chk(True, f"{which.upper()} unit format", f"{tag}{lab!r}")
        labelled = [h for h in ax.get_legend_handles_labels()[1] if not h.startswith("_")]
        if len(labelled) > 1:
            chk(ax.get_legend() is not None, "Legend", f"{tag}{len(labelled)} labelled series")
        fam = ax.xaxis.label.get_fontname()
        names = (style.get("font") or {}).get("names") or []
        if names:
            chk(fam in names, "Font", f"{fam} (style lists {', '.join(names[:3])})")
    el = style.get("experiment_label")
    if el:
        texts = [t.get_text() for ax in fig.axes for t in ax.texts] + [t.get_text() for t in fig.texts]
        chk(any(el["text"] in t for t in texts), "Experiment label", f"'{el['text']}' on the figure")
    return checks


def sandbox(job):
    """On a shared server, plot code may only open files in its own job folder (plus Python's and
    matplotlib's own files and fonts) and may not start programs or open network connections."""
    import matplotlib as mpl
    ok_read = [os.path.realpath(p) for p in [job, sys.prefix, sys.base_prefix, sys.exec_prefix, mpl.get_data_path(),
                                             mpl.get_cachedir(), mpl.get_configdir(), "/usr/share/fonts",
                                             "/usr/local/share/fonts", "/etc/fonts", os.path.expanduser("~/.fonts"),
                                             os.path.expanduser("~/.local/share/fonts"),
                                             os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")]
              + [p for p in sys.path if p and os.path.isdir(p)]]
    ok_write = [os.path.realpath(job), os.path.realpath(mpl.get_cachedir())]
    blocked = ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty",
               "socket.connect", "socket.bind", "socket.getaddrinfo", "ctypes.dlopen", "os.kill", "os.putenv",
               "os.unsetenv", "shutil.rmtree", "os.remove", "os.rmdir", "os.rename", "os.chmod", "webbrowser.open")

    def inside(p, roots):
        return any(p == r or p.startswith(r.rstrip(os.sep) + os.sep) for r in roots)

    def hook(event, args):
        if event in ("open", "os.listdir", "os.scandir"):
            path = args[0] if args else None
            if path is None or isinstance(path, int):
                return
            p = os.path.realpath(os.fsdecode(path))
            mode, flags = (args[1] if len(args) > 1 else None), (args[2] if len(args) > 2 else 0)
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or \
                (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND))
            if not (inside(p, ok_write) or (not writing and inside(p, ok_read))):
                raise PermissionError(f"Plotwise plot code may not {'write' if writing else 'read'} {path}")
        elif event in blocked or event.startswith("os.exec") or event.startswith("os.spawn"):
            raise PermissionError(f"Plotwise plot code may not use {event}")

    sys.addaudithook(hook)


def main(job):
    res = {"ok": False}
    try:
        style = json.load(open(os.path.join(job, "style.json")))
        data = pd.read_pickle(os.path.join(job, "data.pkl"))
        code = open(os.path.join(job, "code.py")).read()
        pw = Plotwise(style)
        pw.apply_rcparams()
        first = next(iter(data.values()))
        g = {"df": first.copy(), "data": {k: v.copy() for k, v in data.items()}, "style": style,
             "pw": pw, "plt": plt, "np": np, "pd": pd, "__name__": "__plotwise__"}
        if os.environ.get("PLOTWISE_SANDBOX") == "1":
            fig0 = plt.figure()  # load fonts and backends before the guard goes up
            fig0.text(0.5, 0.5, "x $x^2$")
            fig0.savefig(io.BytesIO(), format="png")
            fig0.savefig(io.BytesIO(), format="pdf")
            fig0.savefig(io.BytesIO(), format="svg")
            plt.close(fig0)
            sandbox(job)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            exec(compile(code, "plot_code.py", "exec"), g)
            fig = g.get("fig") or plt.gcf()
            fig.set_dpi(PREVIEW_DPI)
            series = extract(fig)
            meta = json.load(open(os.path.join(job, "meta.json"))) if os.path.exists(os.path.join(job, "meta.json")) else {}
            trace = (f"Plotwise session {meta.get('session')}, figure v{meta.get('version')}; data {meta.get('data_file')} "
                     f"sha256 {meta.get('data_sha256')}; code sha256 {meta.get('code_sha256')}") if meta else ""
            title = meta.get("title", "Plotwise figure")
            fig.savefig(os.path.join(job, "figure.png"), dpi=PREVIEW_DPI,
                        metadata={"Title": title, "Description": trace, "Software": "Plotwise"})
            fig.savefig(os.path.join(job, "figure.pdf"),
                        metadata={"Title": title, "Subject": trace, "Creator": "Plotwise", "Keywords": "Plotwise"})
            fig.savefig(os.path.join(job, "figure.svg"), metadata={"Title": title, "Description": trace, "Creator": "Plotwise"})
        glyphs = sorted({str(w.message) for w in caught if "Glyph" in str(w.message)})
        axes = [{"xlabel": ax.get_xlabel(), "ylabel": ax.get_ylabel(), "xscale": ax.get_xscale(),
                 "yscale": ax.get_yscale(), "xlim": [clean(v) for v in ax.get_xlim()],
                 "ylim": [clean(v) for v in ax.get_ylim()], "title": ax.get_title(),
                 "legend_loc": (ax.get_legend()._loc if ax.get_legend() else None),
                 "has_legend": ax.get_legend() is not None,
                 "grid": any(g.get_visible() for g in ax.get_xgridlines() + ax.get_ygridlines()),
                 "bbox_px": _bbox_px(ax, fig)} for ax in fig.axes]
        res.update(ok=True, width_px=int(fig.bbox.width), height_px=int(fig.bbox.height),
                   axes=axes, conversions=pw.conversions, fits=pw.fits,
                   style_checks=style_lint(fig, style, pw) + [
                       {"ok": False, "name": "Missing glyph", "detail": m, "level": "fail"} for m in glyphs[:5]])
        json.dump(series, open(os.path.join(job, "points.json"), "w"))
    except Exception:
        res["error"] = traceback.format_exc(limit=6)
    json.dump(res, open(os.path.join(job, "result.json"), "w"), default=str)


if __name__ == "__main__":
    main(sys.argv[1])
