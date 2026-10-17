"""Style from what the scientist types, from their old plotting scripts and from their old figures.

Each input becomes a *patch*: only the style fields it sets. Plotwise layers the patches over the style
guide (guide, then past code and figures, then typed preferences), so a later, more specific input wins.
Everything here works without AI; when Claude is connected it reads the inputs instead (see core.py).
"""
import io
import json
import re
from collections import Counter

import numpy as np

SECTIONS = ("figure", "font", "axes", "labels", "data", "lines", "legend")

OKABE_ITO = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#F0E442", "#000000"]
TAB10 = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]
GREYS = ["#000000", "#555555", "#888888", "#bbbbbb"]
SANS = ["Arial", "Helvetica", "Liberation Sans", "DejaVu Sans"]
SERIF = ["Times New Roman", "Nimbus Roman", "Liberation Serif", "DejaVu Serif"]
FONTS = {"arial": ("sans-serif", ["Arial", "Liberation Sans", "DejaVu Sans"]),
         "helvetica": ("sans-serif", ["Helvetica", "Arial", "Liberation Sans", "DejaVu Sans"]),
         "calibri": ("sans-serif", ["Calibri", "Carlito", "DejaVu Sans"]),
         "verdana": ("sans-serif", ["Verdana", "DejaVu Sans"]),
         "dejavu sans": ("sans-serif", ["DejaVu Sans"]),
         "times": ("serif", SERIF), "times new roman": ("serif", SERIF),
         "palatino": ("serif", ["Palatino", "TeX Gyre Pagella", "DejaVu Serif"]),
         "garamond": ("serif", ["Garamond", "EB Garamond", "DejaVu Serif"]),
         "computer modern": ("serif", ["CMU Serif", "Computer Modern Roman", "DejaVu Serif"]),
         "latex": ("serif", ["CMU Serif", "Computer Modern Roman", "DejaVu Serif"]),
         "courier": ("monospace", ["Courier New", "DejaVu Sans Mono"])}
MARKERS = {"circle": "o", "circles": "o", "round": "o", "dot": "o", "dots": "o", "square": "s", "squares": "s",
           "triangle": "^", "triangles": "^", "diamond": "D", "diamonds": "D", "cross": "x", "crosses": "x",
           "star": "*", "stars": "*", "plus": "+"}
LEGEND_POS = {"top right": "upper right", "upper right": "upper right", "top left": "upper left",
              "upper left": "upper left", "bottom right": "lower right", "lower right": "lower right",
              "bottom left": "lower left", "lower left": "lower left", "outside": "center left"}
EXPERIMENTS = ["LHCb", "ATLAS", "CMS", "ALICE", "Belle II", "BaBar", "CDF", "D0", "BESIII", "NA62", "IceCube"]


def merge(style, patch):
    """Lay a patch over a full style; only fields of a sensible type are taken."""
    out = json.loads(json.dumps(style))
    for k, v in (patch or {}).items():
        b = out.get(k)
        if k == "experiment_label":
            if isinstance(v, str) and v.strip():
                v = {"text": v.strip()}
            if isinstance(v, dict) and v.get("text"):
                fs = (out.get("font") or {}).get("size_pt", 10) + 4
                out[k] = {"loc": "upper right", "fontsize_pt": fs, **(b if isinstance(b, dict) else {}), **v}
            elif v in (None, False, ""):
                out[k] = None
        elif k in SECTIONS and isinstance(v, dict):
            b = b if isinstance(b, dict) else {}
            for kk, vv in v.items():
                old = b.get(kk)
                if vv is None and kk != "color":
                    continue
                if old is None or vv is None or isinstance(vv, type(old)) or \
                        (isinstance(vv, (int, float)) and isinstance(old, (int, float)) and not isinstance(vv, bool)):
                    b[kk] = vv
            out[k] = b
    return out


def to_hex(c):
    """'red' / 'C1' / (0.1, 0.2, 0.3) / '#abc' -> '#rrggbb', or None."""
    from matplotlib import colors as mc
    try:
        if isinstance(c, str) and re.fullmatch(r"C\d", c.strip()):
            return TAB10[int(c.strip()[1]) % 10]
        return mc.to_hex(c).lower()
    except (ValueError, TypeError):
        return None


def is_grey(h, tol=24):
    r, g, b = (int(h[i:i + 2], 16) for i in (1, 3, 5))
    return max(r, g, b) - min(r, g, b) < tol


def _uniq(seq):
    out = []
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out


# ---- typed preferences ------------------------------------------------------------------------------
def _colour_words():
    from matplotlib import colors as mc
    names = set(mc.CSS4_COLORS) | {"navy", "teal", "maroon", "olive", "orange", "purple", "pink", "brown", "grey", "gray"}
    return sorted((n for n in names if len(n) > 2), key=len, reverse=True)


def patch_from_text(text):
    """'Arial 8 pt, 85 mm wide, colour-blind palette, ticks inside on all sides, no grid' -> (patch, learned)."""
    t = " " + text.lower() + " "
    patch, learned = {}, []

    def put(section, **kw):
        patch.setdefault(section, {}).update(kw)

    for name in sorted(FONTS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", t):
            fam, names = FONTS[name]
            put("font", family=fam, names=names, mathtext="dejavusans" if fam == "sans-serif" else "stix" if name != "latex" and name != "computer modern" else "cm")
            learned.append(f"font {names[0]}")
            break
    else:
        if re.search(r"\bsans[- ]?serif\b|\bsans\b", t):
            put("font", family="sans-serif", names=SANS, mathtext="dejavusans")
            learned.append("a sans-serif font")
        elif re.search(r"\bserif\b", t):
            put("font", family="serif", names=SERIF, mathtext="stix")
            learned.append("a serif font")
    for what, keys in (("tick", ("tick_size_pt",)), ("axis label", ("label_size_pt",)), ("label", ("label_size_pt",)),
                       ("legend", None)):
        m = re.search(rf"{what}s?[^.,;\d]{{0,20}}?(\d+(?:\.\d+)?)\s*(?:pt|points?)\b", t)
        if m:
            v = float(m.group(1))
            if keys:
                put("font", **{k: v for k in keys})
            else:
                put("legend", fontsize_pt=v)
            learned.append(f"{what} text {v:g} pt")
            t = t.replace(m.group(0), " ")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:pt|points?)\b", t)
    if m:
        v = float(m.group(1))
        put("font", size_pt=v, **{k: v for k in ("tick_size_pt", "label_size_pt") if k not in patch.get("font", {})})
        put("legend", **({} if "fontsize_pt" in patch.get("legend", {}) else {"fontsize_pt": max(v - 1, 5)}))
        learned.append(f"text {v:g} pt")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(mm|cm|in|inch|inches)\b", t)
    if m:
        mm = float(m.group(1)) * {"mm": 1, "cm": 10}.get(m.group(2), 25.4)
        put("figure", width_mm=round(mm, 1))
        learned.append(f"{mm:g} mm wide")
    elif re.search(r"single[- ]column|one column|half[- ]width", t):
        put("figure", width_mm=88)
        learned.append("single column (88 mm, a common journal width)")
    elif re.search(r"double[- ]column|two[- ]column|full[- ]width|full page", t):
        put("figure", width_mm=180)
        learned.append("full width (180 mm, a common journal width)")
    m = re.search(r"(\d+(?:\.\d+)?)\s*[:x×/]\s*(\d+(?:\.\d+)?)", t)
    if m and re.search(r"aspect|ratio|shape", t):
        w, h = float(m.group(1)), float(m.group(2))
        if w > 0 and h > 0:
            put("figure", aspect=round(h / w, 3))
            learned.append(f"aspect {m.group(1)}:{m.group(2)}")
    elif re.search(r"\bsquare (?:figure|plot|shape|aspect)", t):
        put("figure", aspect=1.0)
        learned.append("square")
    m = re.search(r"(\d{2,4})\s*dpi", t)
    if m:
        put("figure", dpi=int(m.group(1)))
        learned.append(f"{m.group(1)} dpi")
    if re.search(r"square brackets|units? in \[|\[unit\]|brackets", t) and not re.search(r"round brackets", t):
        put("labels", unit_style="brackets")
        learned.append("units in [brackets]")
    elif re.search(r"parenthes|round brackets|\(unit\)", t):
        put("labels", unit_style="parentheses")
        learned.append("units in (parentheses)")
    elif re.search(r"slash|/ ?unit", t):
        put("labels", unit_style="slash")
        learned.append("units after a slash")
    if re.search(r"ticks?[^.,;]{0,20}\b(in|inside|inward|pointing in)\b", t):
        put("axes", ticks_direction="in")
        learned.append("ticks pointing in")
    elif re.search(r"ticks?[^.,;]{0,20}\b(out|outside|outward)\b", t):
        put("axes", ticks_direction="out")
        learned.append("ticks pointing out")
    if re.search(r"all (?:four )?sides|mirror(?:ed)? ticks|ticks on (?:the )?top and right|box(?:ed)? axes", t):
        put("axes", ticks_all_sides=True)
        learned.append("ticks on all four sides")
    elif re.search(r"(?:no|without) ticks? on (?:the )?top|only (?:the )?left and bottom|open axes", t):
        put("axes", ticks_all_sides=False)
        learned.append("ticks only left and bottom")
    if re.search(r"\b(no|without)\s+minor ticks", t):
        put("axes", minor_ticks=False)
        learned.append("no minor ticks")
    elif re.search(r"\bminor ticks", t):
        put("axes", minor_ticks=True)
        learned.append("minor ticks")
    if re.search(r"\b(no|without|remove)\s+grid", t):
        put("axes", grid=False)
        learned.append("no grid")
    elif re.search(r"\bgrid(?:lines)?\b", t):
        put("axes", grid=True)
        learned.append("gridlines")
    if re.search(r"\b(no|without)\s+(?:a\s+)?title\b", t):
        put("axes", title=False)
        learned.append("no title in the figure")
    if re.search(r"legend[^.,;]{0,25}\b(no|without)\s+(?:a\s+)?(frame|box|border)|(frameless|unboxed|no[- ]frame) legend", t):
        put("legend", frameon=False)
        learned.append("legend without a frame")
    elif re.search(r"legend[^.,;]{0,25}\b(with\s+(?:a\s+)?)?(frame|box|border)\b", t):
        put("legend", frameon=True)
        learned.append("legend in a frame")
    for k, v in LEGEND_POS.items():
        if re.search(rf"legend[^.,;]{{0,20}}\b{k}\b|\b{k}\b[^.,;]{{0,10}}legend", t):
            put("legend", loc=v)
            learned.append(f"legend {k}")
            break
    m = re.search(r"line ?widths?\s*(?:of\s*)?(\d+(?:\.\d+)?)", t)
    if m:
        put("lines", linewidth=float(m.group(1)))
        put("axes", linewidth=min(float(m.group(1)), 1.5))
        learned.append(f"line width {m.group(1)}")
    elif re.search(r"\bthick(?:er)? lines\b", t):
        put("lines", linewidth=2.2)
        learned.append("thick lines")
    elif re.search(r"\bthin(?:ner)? lines\b", t):
        put("lines", linewidth=1.0)
        learned.append("thin lines")
    m = re.search(r"marker ?size\s*(?:of\s*)?(\d+(?:\.\d+)?)", t)
    if m:
        put("data", markersize=float(m.group(1)))
        learned.append(f"marker size {m.group(1)}")
    for w, mk in MARKERS.items():
        if re.search(rf"\b{w}\b[^.,;]{{0,12}}\b(markers?|points?|symbols?)\b|\b(markers?|points?|symbols?)\b[^.,;]{{0,12}}\b{w}\b", t):
            put("data", marker=mk)
            learned.append(f"{w.rstrip('s')} markers")
            break
    if re.search(r"\bno caps\b|without caps|capless", t):
        put("data", capsize=0)
        learned.append("error bars without caps")
    elif re.search(r"\bcaps\b|capped", t):
        put("data", capsize=3)
        learned.append("error bars with caps")

    colours = []
    if len(re.findall(r"#[0-9a-f]{6}\b|#[0-9a-f]{3}\b", t)) >= 1:
        colours, why = _uniq(to_hex(h) for h in re.findall(r"#[0-9a-f]{6}\b|#[0-9a-f]{3}\b", t)), "your colours"
    elif re.search(r"colou?r[- ]?blind|okabe|\bcvd\b|accessible colou?rs", t):
        colours, why = OKABE_ITO, "colour-blind safe palette (Okabe-Ito)"
    elif re.search(r"\bviridis\b", t):
        from matplotlib import colormaps
        colours, why = [to_hex(colormaps["viridis"](x)) for x in np.linspace(0, 0.9, 6)], "viridis palette"
    elif re.search(r"\btab(?:leau)?10\b|matplotlib default", t):
        colours, why = TAB10, "matplotlib's default palette"
    elif re.search(r"black and white|black & white|gr[ae]yscale|monochrome", t):
        colours, why = GREYS, "black and grey only"
    else:
        hexes = [to_hex(h) for h in re.findall(r"#[0-9a-f]{6}\b|#[0-9a-f]{3}\b", t)]
        words = []
        for name in _colour_words():
            for m in re.finditer(rf"\b{name}\b", t):
                words.append((m.start(), name))
        # "black data points" sets the data colour; other colour words make the palette, in the order typed
        data_col = re.search(r"\b(" + "|".join(_colour_words()) + r")\s+(?:data|points|markers|dots)\b|\b(?:data|points|markers)\s+(?:in\s+)?(" + "|".join(_colour_words()) + r")\b", t)
        if data_col:
            c = to_hex(data_col.group(1) or data_col.group(2))
            put("data", color=c)
            learned.append(f"data points in {data_col.group(1) or data_col.group(2)}")
            words = [w for w in words if not (data_col.start() <= w[0] < data_col.end())]
        taken = set()
        for pos, name in sorted(words):
            if any(pos >= a and pos < b for a, b in taken):
                continue
            taken.add((pos, pos + len(name)))
            hexes.append(to_hex(name.replace("grey", "gray")))
        colours, why = _uniq(hexes), "your colours"
    if colours:
        put("lines", colors=list(colours))
        learned.append(why if why != "your colours" else "colours " + ", ".join(colours))
    m = re.search(r"['\"“]([^'\"”]{2,24})['\"”]\s+(?:label|logo|tag)", text) or \
        re.search(r"\b(" + "|".join(re.escape(e) for e in EXPERIMENTS) + r")\b[^.,;]{0,12}\b(?:label|logo|tag)", text, re.I)
    if m:
        name = next((e for e in EXPERIMENTS if e.lower() == m.group(1).lower()), m.group(1))
        patch["experiment_label"] = {"text": name}
        learned.append(f"'{name}' label on the figure")
    elif re.search(r"\b(no|without|remove)\s+(?:the\s+)?(?:experiment|collaboration)\s+label", t):
        patch["experiment_label"] = None
        learned.append("no experiment label")
    return patch, learned


# ---- past plotting code -----------------------------------------------------------------------------
ROOT_BASE = {"kWhite": "#ffffff", "kBlack": "#000000", "kGray": "#999999", "kRed": "#ff0000", "kGreen": "#00ff00",
             "kBlue": "#0000ff", "kYellow": "#ffff00", "kMagenta": "#ff00ff", "kCyan": "#00ffff", "kOrange": "#ffcc00",
             "kSpring": "#ccff00", "kTeal": "#00ffcc", "kAzure": "#0099ff", "kViolet": "#cc00ff", "kPink": "#ff0099"}
ROOT_INDEX = {1: "#000000", 2: "#ff0000", 3: "#00ff00", 4: "#0000ff", 5: "#ffff00", 6: "#ff00ff", 7: "#00ffff",
              8: "#59d454", 9: "#5954d8", 28: "#7f4d33", 30: "#33a64d", 38: "#7fb3cc", 46: "#cc3333"}
ROOT_MARKERS = {20: "o", 21: "s", 22: "^", 23: "v", 24: "o", 25: "s", 26: "^", 29: "*", 33: "D", 34: "P", 8: "o"}
ROOT_FONTS = {4: ("sans-serif", SANS), 6: ("sans-serif", SANS), 13: ("serif", SERIF), 1: ("serif", SERIF),
              2: ("serif", SERIF), 8: ("monospace", ["Courier New", "DejaVu Sans Mono"])}


def _root_shade(base, off):
    """kRed+2 -> darker, kAzure-9 -> lighter (approximately as ROOT does)."""
    r, g, b = (int(base[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = 1 - 0.15 * off if off > 0 else 1
    w = min(-off * 0.09, 0.9) if off < 0 else 0
    r, g, b = (min(1, max(0, c * f + (1 - c * f) * w)) for c in (r, g, b))
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def code_text(filename, raw):
    """The source of a script; for a notebook, its code cells."""
    text = raw.decode("utf-8", errors="replace")
    if filename.lower().endswith(".ipynb"):
        try:
            nb = json.loads(text)
            text = "\n\n".join("".join(c.get("source", "")) for c in nb.get("cells", []) if c.get("cell_type") == "code")
        except ValueError:
            pass
    return text


def patch_from_code(filename, text):
    """Read the figure style a plotting script uses (matplotlib, ROOT C++/PyROOT, R/ggplot, MATLAB)."""
    patch, learned = {}, []

    def put(section, **kw):
        patch.setdefault(section, {}).update(kw)

    colours = []  # (position, hex)
    for m in re.finditer(r"['\"](#[0-9a-fA-F]{6}|#[0-9a-fA-F]{3})['\"]", text):
        colours.append((m.start(), to_hex(m.group(1))))
    for m in re.finditer(r"\b(?:color|colour|c|ecolor|mfc|mec|markerfacecolor|markeredgecolor|facecolor|edgecolor|fill|col)\s*=\s*['\"]([A-Za-z][A-Za-z ]+|C\d)['\"]", text):
        colours.append((m.start(), to_hex(m.group(1).strip().replace("grey", "gray"))))
    for m in re.finditer(r"\b(?:color|c)\s*=\s*\(\s*([01](?:\.\d+)?)\s*,\s*([01](?:\.\d+)?)\s*,\s*([01](?:\.\d+)?)", text):
        colours.append((m.start(), to_hex(tuple(float(x) for x in m.groups()))))
    for m in re.finditer(r"['\"]Color['\"]\s*,\s*\[\s*([01](?:\.\d+)?)[\s,]+([01](?:\.\d+)?)[\s,]+([01](?:\.\d+)?)\s*\]", text):
        colours.append((m.start(), to_hex(tuple(float(x) for x in m.groups()))))  # MATLAB
    for m in re.finditer(r"\b(k(?:White|Black|Gray|Red|Green|Blue|Yellow|Magenta|Cyan|Orange|Spring|Teal|Azure|Violet|Pink))\s*([+-]\s*\d+)?", text):
        off = int(m.group(2).replace(" ", "")) if m.group(2) else 0
        colours.append((m.start(), _root_shade(ROOT_BASE[m.group(1)], off)))
    for m in re.finditer(r"Set(?:Line|Marker|Fill)Color\s*\(\s*(\d+)\s*\)", text):
        if int(m.group(1)) in ROOT_INDEX:
            colours.append((m.start(), ROOT_INDEX[int(m.group(1))]))
    m = re.search(r"palette\s*=\s*['\"](\w+)['\"]|scale_(?:colou?r|fill)_brewer\([^)]*palette\s*=\s*['\"](\w+)['\"]|cmap\s*=\s*['\"](\w+)['\"]", text)
    cmap_cols = []
    if m:
        from matplotlib import colormaps
        name = next(g for g in m.groups() if g)
        if name in colormaps:
            cm = colormaps[name]
            n = getattr(cm, "N", 256)
            cmap_cols = [to_hex(cm(i)) for i in range(min(n, 8))] if n <= 20 else [to_hex(cm(x)) for x in np.linspace(0, 0.9, 6)]
            learned.append(f"the '{name}' colour map")
    if re.search(r"scale_colou?r_viridis|viridis", text) and not cmap_cols:
        from matplotlib import colormaps
        cmap_cols = [to_hex(colormaps["viridis"](x)) for x in np.linspace(0, 0.9, 6)]
        learned.append("the viridis colour map")
    pal = _uniq([c for _, c in sorted(colours)] + cmap_cols)
    named = [c for c in pal if c not in ("#ffffff",)]
    pal = [c for c in named if not is_grey(c)] or named
    if pal:
        put("lines", colors=pal[:10])
        if not cmap_cols:
            learned.append(f"{len(pal[:10])} colour{'s' if len(pal) > 1 else ''} used in the code")
    if re.search(r"errorbar\([^)]*(?:color|c)\s*=\s*['\"](?:k|black)['\"]|\bfmt\s*=\s*['\"]k|SetMarkerColor\(\s*(?:1|kBlack)\s*\)", text):
        put("data", color="#000000")
        learned.append("black data points")

    m = re.search(r"font\.family['\"]\s*[\]:,]\s*=?\s*\[?['\"]([\w -]+)['\"]|fontname\s*=\s*['\"]([\w ]+)['\"]|family\s*=\s*['\"]([\w -]+)['\"]|['\"]FontName['\"]\s*,\s*['\"]([\w ]+)['\"]", text)
    if m:
        name = next(g for g in m.groups() if g).strip()
        key = name.lower()
        if key in FONTS:
            fam, names = FONTS[key]
        elif "sans" in key:
            fam, names = "sans-serif", SANS
        elif "serif" in key:
            fam, names = "serif", SERIF
        elif "mono" in key:
            fam, names = "monospace", ["DejaVu Sans Mono"]
        else:
            fam, names = None, None
        if fam:
            put("font", family=fam, names=names, mathtext="dejavusans" if fam == "sans-serif" else "stix")
            learned.append(f"font {names[0] if key in FONTS else fam}")
    else:
        m = re.search(r"Set(?:Text|Label|Title|Legend)Font\s*\(\s*(\d+)", text)
        if m and int(m.group(1)) // 10 in ROOT_FONTS:
            fam, names = ROOT_FONTS[int(m.group(1)) // 10]
            put("font", family=fam, names=names, mathtext="dejavusans" if fam == "sans-serif" else "stix")
            learned.append(f"ROOT font {m.group(1)} ({names[0]})")
    sizes = [float(x) for x in re.findall(r"(?:fontsize|font\.size['\"]\s*\]\s*=|size\s*=|['\"]FontSize['\"]\s*,)\s*(\d+(?:\.\d+)?)", text)]
    sizes = [x for x in sizes if 5 <= x <= 30]
    if sizes:
        v = Counter(sizes).most_common(1)[0][0]
        put("font", size_pt=v, tick_size_pt=min(sizes), label_size_pt=max(sizes) if max(sizes) <= v + 4 else v)
        put("legend", fontsize_pt=min(sizes))
        learned.append(f"text {v:g} pt")
    m = re.search(r"figsize\s*=\s*[\[(]\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)", text)
    if m:
        w, h = float(m.group(1)), float(m.group(2))
        put("figure", width_mm=round(w * 25.4, 1), aspect=round(h / w, 3))
        learned.append(f"{w * 25.4:.0f} mm wide, aspect {h / w:.2f}")
    else:
        m = re.search(r"TCanvas\s*\([^)]*?,\s*(\d{3,4})\s*,\s*(\d{3,4})\s*\)", text) or \
            re.search(r"ggsave\([^)]*width\s*=\s*(\d+(?:\.\d+)?)[^)]*height\s*=\s*(\d+(?:\.\d+)?)", text)
        if m:
            w, h = float(m.group(1)), float(m.group(2))
            put("figure", aspect=round(h / w, 3))
            learned.append(f"aspect {h / w:.2f}")
    m = re.search(r"\bmarker\s*=\s*['\"]([osd^vD*x+.ph<>])['\"]|\bfmt\s*=\s*['\"][a-z]?([osd^vD*x+.ph])['\"]", text)
    if m:
        mk = next(g for g in m.groups() if g)
        put("data", marker=mk if mk != "." else "o")
        learned.append(f"marker '{mk}'")
    else:
        m = re.search(r"SetMarkerStyle\s*\(\s*(\d+)\s*\)", text)
        if m and int(m.group(1)) in ROOT_MARKERS:
            put("data", marker=ROOT_MARKERS[int(m.group(1))])
            learned.append(f"ROOT marker {m.group(1)}")
    m = re.search(r"\b(?:ms|markersize)\s*=\s*(\d+(?:\.\d+)?)", text)
    if m:
        put("data", markersize=float(m.group(1)))
        learned.append(f"marker size {m.group(1)}")
    m = re.search(r"\b(?:lw|linewidth)\s*=\s*(\d+(?:\.\d+)?)", text)
    if m:
        put("lines", linewidth=float(m.group(1)))
        learned.append(f"line width {m.group(1)}")
    m = re.search(r"\bcapsize\s*=\s*(\d+(?:\.\d+)?)", text)
    if m:
        put("data", capsize=float(m.group(1)))
        learned.append(f"error-bar caps {m.group(1)}")
    if re.search(r"direction\s*=\s*['\"]in['\"]|xtick\.direction['\"]\s*\]\s*=\s*['\"]in", text):
        put("axes", ticks_direction="in")
        learned.append("ticks pointing in")
    elif re.search(r"direction\s*=\s*['\"]out['\"]", text):
        put("axes", ticks_direction="out")
        learned.append("ticks pointing out")
    if re.search(r"top\s*=\s*True[^)]*right\s*=\s*True|right\s*=\s*True[^)]*top\s*=\s*True|SetPadTick[XY]\s*\(\s*1\s*\)|xtick\.top['\"]\s*\]\s*=\s*True", text):
        put("axes", ticks_all_sides=True)
        learned.append("ticks on all four sides")
    if re.search(r"minorticks_on\(\)|which\s*=\s*['\"](?:minor|both)['\"]", text):
        put("axes", minor_ticks=True)
        learned.append("minor ticks")
    if re.search(r"\.grid\(\s*(?:True|b\s*=\s*True)|SetGrid[xy]?\(|theme_bw|\bgrid on\b|plt\.grid\(\s*\)", text):
        put("axes", grid=True)
        learned.append("gridlines")
    if re.search(r"frameon\s*=\s*False|SetBorderSize\s*\(\s*0\s*\)|legend\.frameon['\"]\s*\]\s*=\s*False|legend\.position|'box'\s*,\s*'off'", text):
        put("legend", frameon=False)
        learned.append("legend without a frame")
    br = len(re.findall(r"label\s*\(\s*r?['\"][^'\"]*\[[^\]]+\]\s*['\"]|SetTitle\([^)]*\[[^\]]+\]", text))
    pa = len(re.findall(r"label\s*\(\s*r?['\"][^'\"]*\([^)]+\)\s*['\"]|SetTitle\([^)]*\([^)]+\)\s*['\"]", text))
    if br or pa:
        put("labels", unit_style="brackets" if br >= pa else "parentheses")
        learned.append("units in [brackets]" if br >= pa else "units in (parentheses)")
    exp = next((e for e in EXPERIMENTS if re.search(rf"['\"]\s*{re.escape(e)}(?:\s+\w+)?\s*['\"]", text)), None)
    if exp:
        patch["experiment_label"] = {"text": exp}
        learned.append(f"'{exp}' label")
    return patch, learned


# ---- past figures ------------------------------------------------------------------------------------
def _merge_close(cols, tol=48):
    """[(count, (r, g, b))] -> merged list, biggest first."""
    out = []
    for n, c in sorted(cols, reverse=True):
        for o in out:
            if np.linalg.norm(np.subtract(o[1], c)) < tol:
                o[0] += n
                break
        else:
            out.append([n, c])
    return sorted(out, reverse=True)


def palette_from_image(raw, max_colours=8):
    """Main colours of a raster figure, ignoring the white/black/grey of text, axes and background."""
    from PIL import Image
    im = Image.open(io.BytesIO(raw))
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    im = im.convert("RGB")
    im.thumbnail((500, 500))
    a = np.asarray(im, dtype=float).reshape(-1, 3)
    mx, mn = a.max(1), a.min(1)
    keep = (mx - mn > 60) & ((mx - mn) / np.maximum(mx, 1) > 0.35) & (mx > 50)
    px = a[keep]
    if len(px) < 40:
        return []
    q = Image.fromarray(px.reshape(-1, 1, 3).astype(np.uint8)).quantize(colors=16, method=Image.Quantize.MEDIANCUT)
    idx = np.asarray(q).ravel()
    cols = []
    for i, n in Counter(idx.tolist()).items():
        # the most common exact colour in the cluster is the colour that was drawn (edges are blends)
        exact = Counter(map(tuple, px[idx == i].astype(int))).most_common(1)[0][0]
        cols.append((n, tuple(min(255, v) for v in exact)))
    merged = _merge_close(cols)
    kept = []
    for n, c in merged:  # drop edge blends of a kept colour with the white background
        for k in kept:
            p = np.array(k[1], dtype=float)
            t = (np.array(c, dtype=float) - p) / np.maximum(255 - p, 1)
            if 0.08 < t.mean() < 0.95 and t.std() < 0.08:
                k[0] += n
                break
        else:
            kept.append([n, c])
    merged = kept
    total = sum(n for n, _ in merged)
    return ["#%02x%02x%02x" % tuple(int(v) for v in c) for n, c in merged if n / total >= 0.03][:max_colours]


def palette_from_svg(raw, max_colours=8):
    text = raw.decode("utf-8", errors="replace")
    found = []
    for m in re.finditer(r"(?:fill|stroke)\s*[:=]\s*['\"]?\s*(#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|rgb\([^)]*\))", text):
        v = m.group(1)
        if v.startswith("rgb"):
            nums = [float(x.strip().rstrip("%")) * (2.55 if "%" in x else 1) for x in v[4:-1].split(",")[:3]]
            v = "#%02x%02x%02x" % tuple(int(round(x)) for x in nums)
        h = to_hex(v)
        if h and not is_grey(h):
            found.append(h)
    return [c for c, _ in Counter(found).most_common(max_colours)]


def palette_from_pdf(raw, max_colours=8):
    from pypdf import PdfReader
    found = []
    for page in PdfReader(io.BytesIO(raw)).pages[:3]:
        try:
            data = page.get_contents().get_data().decode("latin-1")
        except Exception:  # noqa: BLE001
            continue
        for m in re.finditer(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(rg|RG|sc|SC|scn|SCN)\b", data):
            try:
                rgb = tuple(min(1.0, float(x)) for x in m.groups()[:3])
            except ValueError:
                continue
            h = to_hex(rgb)
            if h and not is_grey(h):
                found.append(h)
    return [c for c, _ in Counter(found).most_common(max_colours)]


def palette_from_figure(filename, raw):
    ext = filename.lower().rsplit(".", 1)[-1]
    if ext == "svg":
        return palette_from_svg(raw)
    if ext == "pdf":
        return palette_from_pdf(raw)
    return palette_from_image(raw)


CODE_EXT = {"py", "ipynb", "c", "cc", "cpp", "cxx", "h", "hpp", "r", "m", "jl", "txt"}
IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp", "svg", "pdf", "eps"}


def kind_of(filename):
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    return "code" if ext in CODE_EXT else "figure" if ext in IMAGE_EXT else None
