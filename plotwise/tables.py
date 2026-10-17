"""Reads real-world spreadsheets and CSV files without assuming where anything is.

Every sheet is scanned for tables: a table is a run of mostly-numeric rows, and its header is the
text row (or two rows: names + units) directly above that run, wherever it sits. A sheet can hold
several tables (side by side, stacked with or without blank rows, titles or notes around them),
a separate table of uncertainties that is matched back to its values, and label/value notes such as
"Frame rate (fps) | 50". The spreadsheet row of every data row is kept for the integrity checks.
"""
import csv
import io
import os
import re

import numpy as np
import pandas as pd

UNC_RE = re.compile(r"\s*\(?\s*(uncertaint\w*|uncertatint\w*|uncert\w*|errors?|err|sigma|σ)\s*\)?\s*", re.I)
NO_UNC_RE = re.compile(r"\bno\s+uncert", re.I)
UNIT_CELL_RE = re.compile(r"^\s*[\[(]?\s*[A-Za-zµμΩ°%/^·*\d\-²³⁻¹ ]{1,14}\s*[\])]?\s*$")


def excel_row(df, r):
    """Spreadsheet row number of position r in df."""
    rows = df.attrs.get("rows")
    if rows and 0 <= int(r) < len(rows):
        return int(rows[int(r)])
    return int(df.attrs.get("first_row", 2)) + int(r)


def _num(v):
    """Number in the cell, or None. Accepts numbers stored as text, e.g. '3.2' or '1,5e-3'."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float, np.integer, np.floating)):
        return float(v) if np.isfinite(v) else None
    if isinstance(v, str):
        t = v.strip().replace("−", "-")
        if re.fullmatch(r"[-+]?(\d+[.,]?\d*|[.,]\d+)([eE][-+]?\d+)?", t):
            try:
                return float(t.replace(",", ".")) if t.count(",") == 1 and "." not in t else float(t.replace(",", ""))
            except ValueError:
                return None
    return None


def _filled(v):
    return v is not None and not (isinstance(v, str) and not v.strip()) and not (isinstance(v, float) and np.isnan(v))


def _col_letter(c):
    s = ""
    while c >= 0:
        s = chr(65 + c % 26) + s
        c = c // 26 - 1
    return s


def _clean(h):
    return re.sub(r"\s+", " ", str(h)).strip()


def _row_kind(cells):
    vals = [v for v in cells if _filled(v)]
    if not vals:
        return "empty"
    n_num = sum(_num(v) is not None for v in vals)
    if n_num == 0:
        return "text"
    return "num" if n_num >= 0.5 * len(vals) else "text"


def _column_groups(grid, ncols):
    """Column ranges separated by columns that are empty on the whole sheet."""
    used = [any(_filled(r[j]) for r in grid) for j in range(ncols)]
    groups, j = [], 0
    while j < ncols:
        if not used[j]:
            j += 1
            continue
        j0 = j
        while j < ncols and used[j]:
            j += 1
        groups.append((j0, j))
    return groups


def _header_from(text_rows, width):
    """Pick the header among the text rows just above a numeric run. Returns (names, title rows used)."""
    if not text_rows:
        return None, []
    last = text_rows[-1][1]
    filled_last = sum(_filled(v) for v in last)
    if len(text_rows) >= 2:
        prev = text_rows[-2][1]
        filled_prev = sum(_filled(v) for v in prev)
        units_like = filled_last >= 1 and all(UNIT_CELL_RE.match(str(v)) for v in last if _filled(v))
        if units_like and filled_prev >= filled_last and filled_prev >= 2:
            # two-row header: names on top, units below
            names = []
            for a, b in zip(prev, last):
                a, b = (_clean(a) if _filled(a) else ""), (_clean(b) if _filled(b) else "")
                if a and b:
                    names.append(a if b.strip("[]()") in a else f"{a} ({b.strip('[]()')})")
                else:
                    names.append(a or b)
            return names, [text_rows[-2][0], text_rows[-1][0]]
    if filled_last == 1 and width > 1:
        return None, []  # a single text cell above a wide table is a title, not a header
    return [_clean(v) if _filled(v) else "" for v in last], [text_rows[-1][0]]


def extract_constants(grid):
    """Find label/value pairs anywhere on the sheet ("Frame rate (fps) | 50", optionally followed by a unit
    or an uncertainty), record them, and blank their cells so they don't disturb table detection.
    A constant has one value: its row carries nothing else, and its value column has no data run above or below."""
    nrows = len(grid)
    ncols = len(grid[0]) if grid else 0
    cand = []
    for r in range(nrows):
        row = grid[r]
        for c in range(ncols):
            v = row[c]
            if not _filled(v) or _num(v) is not None or len(_clean(v)) < 2:
                continue
            c2 = None
            for k in range(c + 1, min(c + 4, ncols)):  # value may sit up to 2 empty cells to the right
                if _filled(row[k]):
                    c2 = k if _num(row[k]) is not None else None
                    break
            if c2 is None:
                continue
            extra_c = c2 + 1 if c2 + 1 < ncols and _filled(row[c2 + 1]) else None
            if extra_c is not None and extra_c + 1 < ncols and _filled(row[extra_c + 1]):
                continue  # more values follow: this is a table row, not a constant
            if extra_c is not None and _num(row[extra_c]) is None and not UNIT_CELL_RE.match(str(row[extra_c])):
                continue
            if c > 0 and _filled(row[c - 1]):
                continue  # a label glued to other cells on its left is part of a table row
            cand.append((r, c, c2, extra_c))
    keys = {(r, c2) for r, c, c2, e in cand}
    labels = {}
    for r, c, c2, e in cand:
        labels.setdefault((c, _clean(grid[r][c]).lower()), []).append(r)
    # a run of 3+ pairs under a header row with text over both columns is a small data table, not constants
    in_cand = {(r, c, c2) for r, c, c2, e in cand}
    headed = set()
    for r, c, c2, e in cand:
        if (r - 1, c, c2) in in_cand:
            continue
        run = r
        while (run + 1, c, c2) in in_cand:
            run += 1
        if r > 0 and run - r >= 2 and _filled(grid[r - 1][c]) and _filled(grid[r - 1][c2]) \
                and _num(grid[r - 1][c]) is None and _num(grid[r - 1][c2]) is None:
            headed |= {(rr, c, c2) for rr in range(r, run + 1)}
    consts = []
    for r, c, c2, e in cand:
        if (r, c, c2) in headed or len(labels[(c, _clean(grid[r][c]).lower())]) > 1:
            continue  # repeated labels are a category column (Air, Air, Oil...), not constants
        ok = True
        for rr in (r - 1, r + 1):
            if 0 <= rr < nrows:
                if _filled(grid[rr][c]) and _num(grid[rr][c]) is not None:
                    ok = False  # label sits inside a numeric column
                if _filled(grid[rr][c2]) and _num(grid[rr][c2]) is not None and (rr, c2) not in keys:
                    ok = False  # value is part of a column of data
        if not ok:
            continue
        item = {"Quantity": _clean(grid[r][c]), "Value": _num(grid[r][c2]), "Unit": "", "Uncertainty": np.nan,
                "Cell": f"{_col_letter(c2)}{r + 1}"}
        if e is not None:
            if _num(grid[r][e]) is not None:
                item["Uncertainty"] = _num(grid[r][e])
            else:
                item["Unit"] = _clean(grid[r][e]).strip("[]()")
        consts.append((r, item))
        for k in (c, c2, e):
            if k is not None:
                grid[r][k] = None
    consts.sort(key=lambda t: t[0])
    return [i for _, i in consts]


def find_tables(grid, sheet):
    """Return (tables, constants, notes) found in a 2-D list of cell values."""
    nrows = len(grid)
    ncols = max((len(r) for r in grid), default=0)
    grid = [list(r) + [None] * (ncols - len(r)) for r in grid]
    tables, notes = [], []
    consts = extract_constants(grid)
    for (j0, j1) in _column_groups(grid, ncols):
        sub = [r[j0:j1] for r in grid]
        kinds = [_row_kind(r) for r in sub]
        i = 0
        pending_text = []  # consecutive text rows seen just before the current position
        while i < nrows:
            k = kinds[i]
            if k == "empty":
                if pending_text and i + 1 < nrows and kinds[i + 1] == "empty":
                    pending_text = []
                i += 1
                continue
            if k == "text":
                pending_text.append((i, sub[i]))
                i += 1
                continue
            # a numeric run starts here; it ends at a text row or two blank rows in a row
            start = i
            rows = []
            while i < nrows and kinds[i] != "text":
                if kinds[i] == "num":
                    rows.append(i)
                elif i + 1 < nrows and kinds[i + 1] == "empty":
                    break
                i += 1
            width_used = [any(_filled(sub[r][c]) for r in rows) for c in range(j1 - j0)]
            # label/value notes: two used cells per row, text then number, and no header above
            per_row = [[v for v in sub[r] if _filled(v)] for r in rows]
            is_kv = all(len(p) == 2 and _num(p[0]) is None and _num(p[1]) is not None for p in per_row)
            header, used_rows = _header_from(pending_text[-2:] if pending_text else [], j1 - j0)
            if is_kv and (header is None or sum(bool(h) for h in header) < 2):
                for r, p in zip(rows, per_row):
                    c = next(c for c in range(j1 - j0) if _filled(sub[r][c]) and _num(sub[r][c]) is not None)
                    consts.append({"Quantity": _clean(p[0]), "Value": _num(p[1]), "Unit": "", "Uncertainty": np.nan,
                                   "Cell": f"{_col_letter(j0 + c)}{r + 1}"})
                pending_text = []
                continue
            cols = [c for c in range(j1 - j0) if width_used[c] or (header and header[c])]
            if not cols:
                pending_text = []
                continue
            names, seen = [], set()
            for c in cols:
                n = (header[c] if header and header[c] else f"column {_col_letter(j0 + c)}")
                while n in seen:
                    n += "'"
                seen.add(n)
                names.append(n)
            data = []
            for r in rows:
                row = []
                for c in cols:
                    v = sub[r][c]
                    nv = _num(v)
                    row.append(nv if nv is not None else (_clean(v) if _filled(v) else np.nan))
                data.append(row)
            df = pd.DataFrame(data, columns=names)
            df.attrs["rows"] = [r + 1 for r in rows]
            df.attrs["first_row"] = rows[0] + 1
            df.attrs["header_rows"] = [r + 1 for r in used_rows]
            rng = f"{_col_letter(j0 + cols[0])}{(used_rows[0] if used_rows else start) + 1}:{_col_letter(j0 + cols[-1])}{rows[-1] + 1}"
            df.attrs["range"] = rng
            titles = [_clean(" ".join(str(v) for v in t if _filled(v))) for (ti, t) in pending_text if ti not in used_rows]
            if titles:
                df.attrs["title"] = titles[-1]
            hdr = (f"header in row {used_rows[0] + 1}" + (f"-{used_rows[-1] + 1}" if len(used_rows) > 1 else "")
                   if used_rows else "no header row found, columns named by letter")
            gaps = (rows[-1] - rows[0] + 1) - len(rows)
            notes.append(f"'{sheet}': table at {rng}, {hdr}, data in rows {rows[0] + 1}-{rows[-1] + 1}"
                         + (f" ({gaps} blank row(s) skipped)" if gaps else "")
                         + (f", titled '{df.attrs['title']}'" if titles else "") + ".")
            tables.append(df)
            pending_text = []
    return _merge_side_by_side(tables), consts, notes


def _merge_side_by_side(tables):
    """Rejoin one table that was split by an empty spacer column (same header and data rows)."""
    out = []
    for df in tables:
        prev = out[-1] if out else None
        if prev is not None and prev.attrs.get("rows") == df.attrs.get("rows") and \
                prev.attrs.get("header_rows") == df.attrs.get("header_rows") and df.attrs.get("header_rows"):
            merged = pd.concat([prev, df], axis=1)
            merged.attrs = {**prev.attrs, "range": prev.attrs["range"].split(":")[0] + ":" + df.attrs["range"].split(":")[1]}
            out[-1] = merged
        else:
            out.append(df)
    return out


def _unc_base(h):
    """'Time (seconds) (uncertainties)' -> 'Time (seconds)'; None if the header isn't an uncertainty header."""
    h = _clean(h)
    if NO_UNC_RE.search(h) or not UNC_RE.search(h):
        return None
    return _clean(UNC_RE.sub(" ", h))


def _err_name(base):
    m = re.match(r"^(.*?)\s*([\[(][^\])]+[\])])\s*$", base)
    return f"{m.group(1)} error {m.group(2)}" if m else f"{base} error"


def _pair_uncertainties(tables, sheet, notes):
    """A table whose headers are '<name> (uncertainties)' for another table's columns becomes its error columns."""
    used = set()
    for a, dfa in enumerate(tables):
        for b, dfb in enumerate(tables):
            if a == b or a in used or b in used or len(dfa) != len(dfb):
                continue
            bases = {c: _unc_base(c) for c in dfb.columns}
            matched = {c: base for c, base in bases.items() if base in dfa.columns}
            if matched and len(matched) >= max(1, sum(1 for v in bases.values() if v) // 2):
                for c, base in matched.items():
                    dfa[_err_name(base)] = pd.to_numeric(dfb[c], errors="coerce").to_numpy()
                used.add(b)
                notes.append(f"'{sheet}': the table at {dfb.attrs['range']} holds uncertainties for the table at "
                             f"{dfa.attrs['range']}; added them as {len(matched)} error column(s).")
    return [t for k, t in enumerate(tables) if k not in used]


def _const_key(label):
    """'diameter of the ball in meters' / 'diameter of the ball (meters)' -> ('diameter of the ball', 'meters')."""
    s = _clean(label).lower()
    m = re.search(r"[\[(]([^\])]+)[\])]\s*$", s) or re.search(r"\s+in\s+([^\s()]+)\s*$", s)
    unit = m.group(1).strip() if m else ""
    return re.sub(r"\s+", " ", s[:m.start()] if m else s).strip(), unit


def _pair_constant_uncertainties(consts, sheet, notes):
    """'Frame rate uncertainties | 1' becomes the uncertainty of 'Frame rate (fps) | 50', so each constant keeps one row."""
    values = [c for c in consts if _unc_base(c["Quantity"]) is None]
    paired = []
    for u in consts:
        base = _unc_base(u["Quantity"])
        if base is None:
            continue
        name, unit = _const_key(base)
        hits = [c for c in values if _const_key(c["Quantity"])[0] == name
                and (not unit or not _const_key(c["Quantity"])[1] or _const_key(c["Quantity"])[1] == unit)
                and not np.isfinite(c["Uncertainty"])]
        if len(hits) == 1:
            hits[0]["Uncertainty"], hits[0]["Uncertainty cell"] = u["Value"], u["Cell"]
            paired.append(u)
    if paired:
        notes.append(f"'{sheet}': matched {len(paired)} uncertainty value(s) to their constants "
                     f"({', '.join(u['Quantity'] for u in paired[:2])}{', …' if len(paired) > 2 else ''}).")
    return [c for c in consts if all(c is not u for u in paired)]


def sheets_from_grids(grids):
    sheets, notes = {}, []
    for sheet, grid in grids:
        tables, consts, n = find_tables(grid, sheet)
        notes += n
        tables = _pair_uncertainties(tables, sheet, notes)
        for df in [t for t in tables if len(t) == 1 and t.attrs.get("header_rows")]:
            # a header row with a single row of values under it is a set of constants, one value each
            row = df.attrs["rows"][0]
            for k, col in enumerate(df.columns):
                v = df[col].iloc[0]
                if isinstance(v, (int, float)) and np.isfinite(v):
                    consts.append({"Quantity": col, "Value": float(v), "Unit": "", "Uncertainty": np.nan,
                                   "Cell": f"row {row}"})
            notes.append(f"'{sheet}': the one-row table at {df.attrs['range']} was read as constants.")
            tables = [t for t in tables if t is not df]
        for df in tables:
            name = sheet if len(tables) == 1 else f"{sheet} {df.attrs['range']}"
            df.attrs.update(sheet=sheet, single=len(tables) == 1)
            sheets[name] = df
        pair_notes = []
        consts = _pair_constant_uncertainties(consts, sheet, pair_notes)
        if consts:
            cdf = pd.DataFrame(consts)
            cdf = cdf.dropna(axis=1, how="all")
            if "Unit" in cdf and not cdf["Unit"].astype(bool).any():
                cdf = cdf.drop(columns="Unit")
            cdf.attrs["rows"] = [int(re.sub(r"\D", "", c["Cell"]) or 0) for c in consts]
            cdf.attrs.update(sheet=sheet, constants=True)
            sheets[f"{sheet} constants"] = cdf
            notes.append(f"'{sheet}': read {len(consts)} labelled value(s) outside the tables "
                         f"({', '.join(c['Quantity'] for c in consts[:3])}{', …' if len(consts) > 3 else ''}).")
        notes += pair_notes
    return sheets, notes


def _csv_grid(path):
    raw = open(path, encoding="utf-8-sig", errors="replace").read()
    try:
        dialect = csv.Sniffer().sniff(raw[:20000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [row for row in csv.reader(io.StringIO(raw), dialect)]
    # HEPData exports start with "#: key: value" lines; keep the table's description (or name) as its title
    meta = {}
    for i, row in enumerate(rows):
        m = re.match(r"^#:\s*([\w ]+?):\s*(.*)$", ",".join(row).strip())
        if m:
            meta.setdefault(m.group(1).lower(), (i, m.group(2).strip().strip('"')))
            rows[i] = []
    keep = meta.get("description") or meta.get("name")
    if keep and keep[1]:
        rows[max(i for i, _ in meta.values())] = [keep[1]]  # next to the header, where titles are looked for
    return rows


def read_table(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        grids = [(ws.title, [list(r) for r in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]
    elif ext == ".xls":
        grids = [(str(k), v.where(pd.notna(v), None).values.tolist())
                 for k, v in pd.read_excel(path, sheet_name=None, header=None).items()]
    else:
        grids = [("data", _csv_grid(path))]
    sheets, notes = sheets_from_grids(grids)
    if not sheets:
        raise ValueError("No table of numbers was found in this file.")
    if ext not in (".xlsx", ".xlsm", ".xls"):
        # a CSV has no sheets: say "the CSV file" to people, keep the key "data" for the code
        for df in sheets.values():
            df.attrs["csv"] = True
        notes = [re.sub(r"^'data': (\w)", lambda m: m.group(1).upper(), n) for n in notes]
    return sheets, notes


def where(df, key=None):
    """How to name a table to people: "the CSV file", "sheet 'Sheet1'", "the table at A10:B16 of sheet 'Sheet1'"."""
    a = df.attrs
    sheet = a.get("sheet", key)
    if a.get("csv"):
        place = "the CSV file"
    else:
        place = f"sheet '{sheet}'"
    if a.get("constants"):
        return f"the constants in {place}"
    if a.get("single", True) or not a.get("range"):
        return place
    return f"the table at {a['range']} of {place}"
