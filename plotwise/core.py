"""A Plotwise session: style, data, figure versions, integrity checks and the AI step log."""
import datetime as dt
import difflib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import zipfile

import numpy as np
import pandas as pd

from . import integrity, llm, offline, styleinputs
from .inspector import inspector_html
from .pwstyle import split_header
from .tables import excel_row, read_table, where
from .texttools import readable

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASE_STYLE = json.load(open(os.path.join(HERE, "base_style.json"), encoding="utf-8"))
SESSIONS = os.environ.get("PLOTWISE_SESSIONS", os.path.join(ROOT, "sessions"))
# on a shared server: longer, unguessable session ids, sessions listed only to the browser that made them,
# and plot code runs inside a file and network guard (harness.sandbox)
HOSTED = os.environ.get("PLOTWISE_HOSTED") == "1"

UNSAFE = re.compile(r"\bimport\s+(os|sys|subprocess|socket|shutil|requests|urllib|http|pathlib|pickle|importlib|ctypes)\b|"
                    r"__import__|\bopen\s*\(|\beval\s*\(|\bexec\s*\(|\.savefig\s*\(|\.to_(csv|excel|pickle)\s*\(|"
                    r"\b(pd|pandas)\.read_\w+|\bnp\.(load|loadtxt|genfromtxt|fromfile|memmap|save)\b|\bimread\b|"
                    r"__builtins__|__subclasses__|__globals__|\bos\.environ|\.environ\b")
# operations that create or reshape data values; the AI may only scale by a declared factor, use log axes
# and fit with pw.fit (which records the fit so the checker can re-do it)
DATA_CHANGE = re.compile(r"\.(rolling|ewm|interpolate|fillna|resample|sample)\s*\(|\bnp\.random\b|\bscipy\b|"
                         r"\b(curve_fit|savgol_filter|gaussian_filter1?d?|polyfit|polyval|convolve|lowess|smooth)\s*\(")
HARDCODED = re.compile(r"[\[(]\s*-?\d[\d.eE+-]*\s*(?:,\s*-?\d[\d.eE+-]*\s*){3,}[\])]")


def normalize_style(style):
    """Merge a style read from a guide onto the base style, so a wrongly shaped field can't break plotting."""
    out = json.loads(json.dumps(BASE_STYLE))
    out.update(id="custom", rules=[])
    for k, v in (style or {}).items():
        b = out.get(k)
        if k == "experiment_label":
            if isinstance(v, str) and v.strip():
                v = {"text": v.strip()}
            if isinstance(v, dict) and v.get("text"):
                out[k] = {"loc": "upper right", "fontsize_pt": out["font"]["size_pt"] + 4, **v}
            else:
                out[k] = None
        elif isinstance(b, dict) and isinstance(v, dict):
            out[k] = {**b, **{kk: vv for kk, vv in v.items() if vv is not None and (kk not in b or b[kk] is None
                                                                                  or isinstance(vv, type(b[kk])) or
                                                                                  (isinstance(vv, (int, float)) and isinstance(b[kk], (int, float))))}}
        elif k == "rules":
            out[k] = [str(r) for r in v] if isinstance(v, list) else [str(v)]
        elif b is None or isinstance(v, type(b)):
            out[k] = v
    return out


def now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def summarize(data, max_rows=8):
    out = []
    for name, df in data.items():
        where_rows = ""
        if df.attrs.get("rows"):
            where_rows = (f" (rows {df.attrs['rows'][0]}-{df.attrs['rows'][-1]} of the file"
                     + (f", header in row {'-'.join(map(str, df.attrs['header_rows']))}" if df.attrs.get("header_rows") else "")
                     + (f", titled {df.attrs['title']!r}" if df.attrs.get("title") else "") + ")")
        kind = "CSV file" if df.attrs.get("csv") else "Sheet"
        out.append(f"data[{name!r}] ({kind}): {len(df)} rows x {len(df.columns)} columns{where_rows}")
        for c in df.columns:
            nn = int(df[c].isna().sum())
            bad = int((pd.to_numeric(df[c], errors="coerce").isna() & df[c].notna()).sum())
            unit = split_header(c)[1]
            out.append(f"  - {c!r}: dtype {df[c].dtype}" + (f", unit {unit}" if unit else "") +
                       (f", {nn} empty" if nn else "") + (f", {bad} non-numeric" if bad and df[c].dtype != object else ""))
        out.append("  first rows:\n" + "\n".join("    " + l for l in df.head(max_rows).to_string().splitlines()))
    return "\n".join(out)


def read_doc_text(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages[:40])
    if ext == ".docx":
        import docx
        d = docx.Document(path)
        cells = ["\t".join(c.text for c in row.cells) for t in d.tables for row in t.rows]
        return "\n".join([p.text for p in d.paragraphs] + cells)
    return open(path, encoding="utf-8", errors="replace").read()


class Session:
    def __init__(self, sid=None):
        self.sid = sid or uuid.uuid4().hex[:20 if HOSTED else 10]
        self.dir = os.path.join(SESSIONS, self.sid)
        os.makedirs(self.dir, exist_ok=True)
        p = os.path.join(self.dir, "state.json")
        self.state = json.load(open(p)) if os.path.exists(p) else {
            "sid": self.sid, "created": now(), "style": None, "style_source": None, "data_file": None,
            "data_sha256": None, "versions": [], "current": None, "history": [], "spec": None, "spot_checks": []}
        self._data = None

    # -- persistence & log -------------------------------------------------
    def summary(self):
        """One line for the saved-sessions list."""
        st = self.state
        cur = next((v for v in st["versions"] if v["n"] == st["current"]), None)
        log = os.path.join(self.dir, "log.jsonl")
        guide = (st.get("style") or {}).get("guide_title")
        return {"sid": self.sid, "name": st.get("name") or "", "created": st.get("created"),
                "updated": dt.datetime.fromtimestamp(os.path.getmtime(log if os.path.exists(log) else self.dir))
                .astimezone().isoformat(timespec="seconds"),
                "data_file": st.get("data_file"), "guide": guide, "versions": len(st["versions"]),
                "status": cur.get("status") if cur else None, "request": (st["versions"][0]["request"] if st["versions"] else None)}

    @staticmethod
    def saved(limit=200, only=None):
        """Sessions that hold something (a guide, data or a figure), most recently used first.
        only: the session ids this browser made (on a shared server nobody sees other people's sessions)."""
        out = []
        for sid in os.listdir(SESSIONS) if os.path.isdir(SESSIONS) else []:
            if only is not None and sid not in only:
                continue
            if os.path.exists(os.path.join(SESSIONS, sid, "state.json")):
                try:
                    s = Session(sid).summary()
                except (OSError, ValueError, KeyError):
                    continue
                if s["data_file"] or s["guide"] or s["versions"]:
                    out.append(s)
        return sorted(out, key=lambda s: s["updated"], reverse=True)[:limit]

    def rename(self, name):
        name = " ".join(str(name).split())[:80]
        self.state["name"] = name
        self.save()
        self.log("Human", "session", f"Named this session '{name}'." if name else "Cleared the session name.")
        return name

    def save(self):
        json.dump(self.state, open(os.path.join(self.dir, "state.json"), "w"), indent=1, default=str)

    def log(self, actor, step, summary, **details):
        path = os.path.join(self.dir, "log.jsonl")
        n = sum(1 for _ in open(path)) + 1 if os.path.exists(path) else 1
        if actor != "Human":  # what people typed is kept as typed; app and AI text is made readable
            summary = readable(summary)
        entry = {"n": n, "time": now(), "actor": actor, "step": step, "summary": summary, "details": details}
        with open(path, "a") as f:
            f.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")
        return entry

    def read_log(self):
        path = os.path.join(self.dir, "log.jsonl")
        return [json.loads(l) for l in open(path)] if os.path.exists(path) else []

    @property
    def data(self):
        if self._data is None and self.state["data_file"]:
            self._data = pd.read_pickle(os.path.join(self.dir, "data.pkl"))
        return self._data

    # -- step 1: style -------------------------------------------------------
    def use_base_style(self):
        self.compose_style()
        self.save()
        self.log("App", "style", "No style guide uploaded yet, so a plain default style is used.")
        return self.state["style"]

    def compose_style(self):
        """style = the guide (or the plain default), then past code and figures, then typed preferences.
        Later layers win, so what the scientist types has the last word."""
        st = self.state
        if "guide_style" not in st:  # sessions saved before layers existed
            st["guide_style"] = st["style"] if (st.get("style") or {}).get("guide_file") else None
        style = st.get("guide_style") or dict(BASE_STYLE)
        refs = [r for r in st.get("style_refs", []) if r.get("use")]
        notes = st.get("style_notes", [])
        for layer in refs + notes:
            style = styleinputs.merge(style, layer.get("patch"))
        parts = [f"guide: {style.get('guide_file')}" if st.get("guide_style") else "no style guide uploaded (plain default)"]
        if refs:
            parts.append(f"{len(refs)} past plot{'s' if len(refs) > 1 else ''} ({', '.join(r['file'] for r in refs)})")
        if notes:
            parts.append(f"{len(notes)} typed preference{'s' if len(notes) > 1 else ''}")
        st["style"], st["style_source"] = style, " + ".join(parts)
        return style

    def _ask_patch(self, rules, prompt, image=None):
        """(patch, learned, via) from Claude, or None when no Claude backend is reachable."""
        backend = llm.detect_backend()
        if backend == "offline" or (image is not None and backend != "api"):
            return None
        try:
            reply = llm.ask(rules, prompt, backend) if image is None else llm.ask_image(rules, prompt, *image)
            patch = json.loads(llm.extract_block(reply, "json") or "{}")
            m = re.search(r"LEARNED:\s*(.+)", reply)
            learned = [x.strip() for x in m.group(1).split(";") if x.strip()] if m else []
            if not isinstance(patch, dict):
                raise ValueError("the reply was not a JSON object")
            return patch, learned, llm.BACKEND_NAMES[backend], reply
        except Exception as e:  # noqa: BLE001
            self.log("App", "style", f"Claude could not read it ({e}); using the built-in rules instead.")
            return None

    def add_style_note(self, text):
        """Section 1 chatbox: the style the scientist describes in words, layered over the guide."""
        text = " ".join(str(text or "").split())[:2000]
        if not text:
            raise ValueError("Type the style you want first.")
        self.log("Human", "style", f"Style preference: {text}")
        cur = self.compose_style()
        got = self._ask_patch(llm.STYLE_PATCH_RULES,
                              f"CURRENT STYLE SPEC:\n```json\n{json.dumps(cur, indent=1, ensure_ascii=False)}\n```\n\n"
                              f"WHAT THE SCIENTIST WANTS:\n{text}")
        if got:
            patch, learned, via, reply = got
        else:
            patch, learned = styleinputs.patch_from_text(text)
            via, reply = "built-in rules (no AI)", None
        if not patch:
            raise ValueError("Plotwise couldn't find a style setting in that. Try e.g. 'Arial 8 pt, 85 mm wide, "
                             "colour-blind palette, ticks inside on all sides'.")
        self.state.setdefault("style_notes", []).append({"text": text, "patch": patch, "learned": learned, "via": via,
                                                         "time": now()})
        self.compose_style()
        self.save()
        self.log("AI" if reply else "App", "style", f"Applied your style preference ({via}): " + "; ".join(learned or ["done"]) + ".",
                 patch=patch, **({"reply": reply[:4000]} if reply else {}))
        self.restyle("your style preference")
        return learned

    def remove_style_note(self, i):
        notes = self.state.get("style_notes", [])
        if not 0 <= i < len(notes):
            raise ValueError("That preference no longer exists.")
        gone = notes.pop(i)
        self.compose_style()
        self.save()
        self.log("Human", "style", f"Removed the style preference '{gone['text']}'.")
        self.restyle("a removed preference")

    # -- step 2: previous plotting code or figures --------------------------
    def add_refs(self, uploads):
        """Old plotting scripts and old figures: their colours, fonts, sizes and markers become a style layer."""
        added = []
        rdir = os.path.join(self.dir, "refs")
        os.makedirs(rdir, exist_ok=True)
        for filename, raw in uploads:
            name = os.path.basename(filename)
            kind = styleinputs.kind_of(name)
            if not kind:
                raise ValueError(f"{name}: Plotwise reads plotting code (.py, .ipynb, .C, .cpp, .R, .m) "
                                 f"and figures (.png, .jpg, .svg, .pdf).")
            digest = hashlib.sha256(raw).hexdigest()
            open(os.path.join(rdir, digest[:12] + "_" + name), "wb").write(raw)
            self.log("Human", "style", f"Uploaded a previous {'plotting script' if kind == 'code' else 'figure'} '{name}'.",
                     sha256=digest)
            palette, via, reply = [], "built-in rules (no AI)", None
            if kind == "code":
                text = styleinputs.code_text(name, raw)
                got = self._ask_patch(llm.STYLE_CODE_RULES, f"STYLE SPEC TEMPLATE (keys to use):\n```json\n"
                                      f"{json.dumps(BASE_STYLE, indent=1)}\n```\n\nSCRIPT '{name}':\n```\n{text[:40000]}\n```")
                if got:
                    patch, learned, via, reply = got
                else:
                    patch, learned = styleinputs.patch_from_code(name, text)
                palette = list((patch.get("lines") or {}).get("colors") or [])
            else:
                try:
                    palette = styleinputs.palette_from_figure(name, raw)
                except Exception as e:  # noqa: BLE001
                    raise ValueError(f"{name}: Plotwise couldn't open this figure ({e}).") from None
                patch, learned = ({"lines": {"colors": palette}} if palette else {}), []
                if palette:
                    learned.append(f"{len(palette)} colour{'s' if len(palette) > 1 else ''} in the figure")
                ext = name.lower().rsplit(".", 1)[-1]
                media = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}.get(ext)
                if media and len(raw) < 4_500_000:
                    got = self._ask_patch(llm.STYLE_FIGURE_RULES, f"STYLE SPEC TEMPLATE (keys to use):\n```json\n"
                                          f"{json.dumps(BASE_STYLE, indent=1)}\n```\nThe colours measured from the image "
                                          f"are {palette}; use them as lines.colors.", image=(raw, media))
                    if got:
                        p2, l2, via, reply = got
                        if palette:
                            p2.setdefault("lines", {})["colors"] = palette  # measured pixels beat a description
                        patch, learned = p2, l2 or learned
                if not reply:
                    via = "colours measured from the image (no AI)"
            if not patch:
                learned = ["no style settings found"]
            ref = {"id": digest[:12], "file": name, "kind": kind, "sha256": digest, "patch": patch, "learned": learned,
                   "palette": palette, "use": bool(patch), "via": via, "time": now()}
            refs = self.state.setdefault("style_refs", [])
            refs[:] = [r for r in refs if r["id"] != ref["id"]] + [ref]
            added.append(ref)
            self.log("AI" if reply else "App", "style", f"From '{name}' ({via}): " + "; ".join(learned) + ".",
                     patch=patch, palette=palette, **({"reply": reply[:4000]} if reply else {}))
        self.compose_style()
        self.save()
        self.restyle("the style of " + ", ".join(r["file"] for r in added))
        return added

    def set_ref(self, ref_id, use=None, remove=False):
        refs = self.state.get("style_refs", [])
        ref = next((r for r in refs if r["id"] == ref_id), None)
        if not ref:
            raise ValueError("That file is no longer in this session.")
        if remove:
            refs.remove(ref)
            self.log("Human", "style", f"Removed '{ref['file']}' from the style.")
        else:
            ref["use"] = bool(use)
            self.log("Human", "style", f"{'Using' if use else 'Not using'} the style of '{ref['file']}'.")
        self.compose_style()
        self.save()
        self.restyle(f"the change to '{ref['file']}'")

    def restyle(self, why):
        """Redraw the current figure with the new style. Same code, no AI, a new checked version."""
        cur = self.current()
        if not cur or not self.state.get("data_file"):
            return None
        code = open(os.path.join(cur["dir"], "code.py")).read()
        return self._run(code, f"Style updated: {why}", "Redrawn in the updated style, without the AI.", "manual")

    def set_style_doc(self, filename, raw):
        path = os.path.join(self.dir, "guide_" + os.path.basename(filename))
        open(path, "wb").write(raw)
        text = read_doc_text(path)
        digest = sha256(path)
        self.log("Human", "style", f"Uploaded style guide '{filename}' ({len(text)} characters of text).", sha256=digest)
        backend = llm.detect_backend()
        style, note = None, ""
        if backend != "offline":
            base = dict(BASE_STYLE, id="custom", name="", source="", rules=[])
            prompt = (f"TEMPLATE (fill this in):\n```json\n{json.dumps(base, indent=1, ensure_ascii=False)}\n```\n\n"
                      f"GUIDE '{filename}':\n{text[:60000]}")
            try:
                reply = llm.ask(llm.STYLE_RULES, prompt, backend)
                style = json.loads(llm.extract_block(reply, "json") or "null")
                note = f"Claude read the guide and filled in the style spec ({llm.BACKEND_NAMES[backend]})."
                self.log("AI", "style", note, backend=backend, prompt_chars=len(prompt), reply=reply[:4000])
            except Exception as e:  # noqa: BLE001
                self.log("App", "style", f"Claude could not read the guide ({e}); falling back to offline rules.")
        if not style:
            style, note = offline.style_from_text(text, BASE_STYLE)
            self.log("App", "style", note)
        style = normalize_style(style)
        style["guide_file"], style["guide_sha256"] = filename, digest
        style["guide_title"] = os.path.splitext(os.path.basename(filename))[0]
        self.state["guide_style"] = style
        self.compose_style()
        self.log("App", "style", f"{style['guide_title']} uploaded. Figure rules: " + " | ".join(style.get("rules") or []),
                 rules=style.get("rules"))
        self.save()
        self.restyle(f"the guide {filename}")
        return style, note

    # -- step 2: data --------------------------------------------------------
    def set_data(self, filename, raw):
        ddir = os.path.join(self.dir, "data")
        shutil.rmtree(ddir, ignore_errors=True)
        os.makedirs(ddir)
        path = os.path.join(ddir, os.path.basename(filename))
        open(path, "wb").write(raw)
        data, table_notes = read_table(path)
        pd.to_pickle(data, os.path.join(self.dir, "data.pkl"))
        self._data = data
        self.state.update(data_file=os.path.basename(filename), data_sha256=sha256(path), spec=None, data_notes=table_notes)
        self.save()
        problems = []
        for sh, df in data.items():
            if sh.endswith(" constants"):
                continue  # labelled single values; a constant without an uncertainty is normal
            for c in df.columns:
                num = pd.to_numeric(df[c], errors="coerce")
                if num.notna().mean() > 0.5:
                    for r in np.where(num.isna())[0]:
                        problems.append(readable(f"Row {excel_row(df, r)} of {where(df, sh)}, column '{c}': " + ("empty" if pd.isna(df[c].iloc[r]) else f"not a number ({df[c].iloc[r]!r})")))
        self.log("Human", "data", f"Uploaded data '{filename}'.", sha256=self.state["data_sha256"],
                 sheets={k: {"rows": len(v), "columns": list(map(str, v.columns))} for k, v in data.items()})
        if table_notes:
            self.log("App", "data", "Read the file layout: " + " ".join(table_notes), layout=table_notes)
        if problems:
            self.log("App", "data", f"Found {len(problems)} cell(s) that can't be plotted.", problems=problems)
        return self.data_info(problems)

    def data_info(self, problems=None):
        d = self.data or {}
        return {"file": self.state["data_file"], "sha256": self.state["data_sha256"], "problems": problems or [],
                "notes": self.state.get("data_notes") or [],
                "sheets": [{"name": k, "label": where(v, k), "rows": len(v), "columns": list(map(str, v.columns)),
                            "head": json.loads(v.head(12).to_json(orient="split", default_handler=str)),
                            "constants": json.loads(v.to_json(orient="records", default_handler=str))
                            if str(k).endswith(" constants") else None}
                           for k, v in d.items()]}

    # -- steps 3/4: plot and edit -------------------------------------------
    def plot(self, request):
        return self._generate(request, edit=False)

    def edit(self, request):
        return self._generate(request, edit=True)

    def _generate(self, request, edit):
        if not self.data:
            raise ValueError("Upload data first.")
        if not self.state["style"]:
            self.use_base_style()
        style = self.state["style"]
        cur = self.current()
        cur_code = open(os.path.join(cur["dir"], "code.py")).read() if (edit and cur) else None
        step = "edit" if edit else "plot"
        self.log("Human", step, request)
        backend = llm.detect_backend()
        code, changes, via = None, "", backend
        if backend != "offline":
            prompt = llm.code_prompt(summarize(self.data), style, request, cur_code, self.state["history"] if edit else None)
            try:
                reply = llm.ask(llm.CODE_RULES, prompt, backend)
                code, changes = llm.extract_block(reply, "python"), llm.extract_changes(reply)
                if not code:
                    raise llm.LLMError("reply had no code block")
                self.log("AI", step, changes or "Wrote plotting code.", backend=llm.BACKEND_NAMES[backend],
                         prompt=prompt, reply=reply, diff=self._diff(cur_code, code))
            except Exception as e:  # noqa: BLE001
                self.log("App", step, f"Claude unavailable ({e}). Using the offline engine.")
                code, via = None, "offline"
        if code is None:
            via = "offline"
            try:
                if edit and self.state.get("spec"):
                    spec, done = offline.edit_spec(self.state["spec"], request, self.data)
                    changes = "; ".join(done)
                else:
                    spec = offline.new_spec(request, self.data)
                    changes = f"Plotted '{spec['y']}' vs '{spec['x']}'" + (f", one series per '{spec['group']}'" if spec["group"] else "")
                self.state["spec"] = spec
                code = offline.spec_to_code(spec)
                self.log("App", step, f"Offline engine: {changes}.", diff=self._diff(cur_code, code))
            except ValueError as e:
                self.log("App", step, str(e))
                raise
        if via != "offline":
            self.state["spec"] = None
        v = self._run(code, request, changes, via)
        if not v["ok"] and via != "offline":
            # one self-repair round, logged
            prompt = llm.code_prompt(summarize(self.data), style, request, code, None, v["error"])
            try:
                reply = llm.ask(llm.CODE_RULES, prompt, via)
                fixed = llm.extract_block(reply, "python")
                self.log("AI", "repair", "The script failed; Claude fixed it. " + llm.extract_changes(reply),
                         error=v["error"], diff=self._diff(code, fixed))
                v = self._run(fixed, request, changes, via)
            except Exception as e:  # noqa: BLE001
                self.log("App", "repair", f"Repair failed: {e}")
        self.state["history"].append(request)
        self.save()
        return v

    # -- format panel: axis and chart settings changed by hand, like Excel's "Format Axis" ------------
    LEGEND_LOCS = ("best", "upper right", "upper left", "lower left", "lower right", "right", "center left",
                   "center right", "lower center", "upper center", "center")
    FIT_KINDS = ("linear", "poly", "exp", "log", "power", "gauss")
    BLOCK = re.compile(r"\n*# >>> Plotwise format panel.*?# <<< end format panel\n?", re.S)

    @staticmethod
    def _empty_panel():
        return {"axes": 0, "x": {"label": None, "min": None, "max": None, "step": None, "log": None},
                "y": {"label": None, "min": None, "max": None, "step": None, "log": None},
                "legend": "keep", "grid": None, "fit": {"kind": "none", "degree": None, "series": -1, "range": None,
                                                        "equation": False}}

    def panel(self, code=None):
        """The format-panel settings in force for the current figure, read back from its spec (offline figures)
        or from the marked block in its code, so an AI edit to that block shows up in the panel too."""
        cur = self.current()
        p = self._empty_panel()
        if not cur:
            return p
        spec = self.state.get("spec")
        if spec and cur.get("via") in ("offline", "manual"):
            for a in ("x", "y"):
                lim = spec.get(f"{a}lim") or [None, None]
                p[a].update(min=lim[0], max=lim[1], step=spec.get(f"{a}step"), label=spec.get(f"{a}label"),
                            log=bool(spec.get(f"{a}log")))
            p["legend"] = "none" if spec.get("legend") is False else spec.get("legend_loc") or "keep"
            p["grid"] = spec.get("grid")
            if spec.get("fit"):
                f = spec["fit"]
                p["fit"].update(kind=f["kind"], degree=f.get("degree"), range=f.get("range"),
                                series=-1 if f.get("series") is None else f["series"], equation=bool(f.get("show_equation")))
            p["source"] = "spec"
            return p
        import ast
        code = code if code is not None else open(os.path.join(cur["dir"], "code.py")).read()
        m = self.BLOCK.search(code)
        if not m:
            return p
        try:
            tree = ast.parse(m.group(0))
        except SyntaxError:
            return p
        lit = lambda n: ast.literal_eval(n)  # noqa: E731
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Subscript):
                try:
                    p["axes"] = int(lit(node.value.slice))
                except (ValueError, TypeError):
                    pass
            if not isinstance(node, ast.Call):
                continue
            f = ast.unparse(node.func)
            try:
                args = [lit(a) for a in node.args if not (isinstance(a, ast.Name) and a.id == "_ax")]
                kw = {k.arg: lit(k.value) for k in node.keywords}
            except (ValueError, TypeError, SyntaxError):
                args, kw = [], {}
            for a in ("x", "y"):
                if f == f"_ax.set_{a}label" and args:
                    p[a]["label"] = args[0]
                elif f == f"_ax.set_{a}scale" and args:
                    p[a]["log"] = args[0] == "log"
                elif f == f"_ax.set_{a}lim" and len(args) == 2:
                    p[a]["min"], p[a]["max"] = args
            if f.endswith("axis.set_major_locator") and node.args and isinstance(node.args[0], ast.Call) \
                    and ast.unparse(node.args[0].func) == "plt.MultipleLocator" and node.args[0].args:
                try:
                    p[f[4]]["step"] = lit(node.args[0].args[0])  # "_ax.xaxis..." -> "x"
                except (ValueError, KeyError):
                    pass
            if f == "_ax.grid" and args:
                p["grid"] = bool(args[0])
            elif f == "pw.legend":
                p["legend"] = kw.get("loc") or "best"
            elif f == "_ax.get_legend().remove":
                p["legend"] = "none"
            elif f == "pw.fit_plotted":
                n_fits = sum(1 for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func) == "pw.fit_plotted")
                p["fit"].update(kind=kw.get("kind", "linear"), degree=kw.get("degree"),
                                series=-1 if n_fits > 1 else kw.get("series", 0), equation=bool(kw.get("show_equation")),
                                range=list(kw["fit_range"]) if kw.get("fit_range") else None)
        p["source"] = "block"
        return p

    def format(self, settings):
        """Apply the format panel without the AI. Offline-made figures update their spec; AI-written code
        gets one marked block at the end, which later AI edits keep (see llm.CODE_RULES)."""
        cur = self.current()
        if not cur:
            raise ValueError("Make a figure first.")
        rep = self.integrity()
        axes = rep.get("axes") or [{}]
        ai = int(settings.get("axes") or 0)
        if not 0 <= ai < len(axes):
            raise ValueError("That panel doesn't exist.")
        prev, now_ax = self.panel(), axes[ai]
        if prev.get("source") == "block" and prev["axes"] != ai:
            prev = self._empty_panel()  # the old block was for another panel; it is replaced

        def num(v, what):
            if v in (None, ""):
                return None
            try:
                f = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"The {what} '{v}' is not a number.") from None
            if not np.isfinite(f):
                raise ValueError(f"The {what} '{v}' is not a number.")
            return f

        new, done = self._empty_panel(), []
        new["axes"] = ai
        for a in ("x", "y"):
            o, n = settings.get(a) or {}, new[a]
            n["min"], n["max"] = num(o.get("min"), f"{a} minimum"), num(o.get("max"), f"{a} maximum")
            n["step"], n["log"] = num(o.get("step"), f"{a} tick spacing"), bool(o.get("log"))
            n["label"] = o.get("label") if isinstance(o.get("label"), str) else None
            if n["min"] is not None and n["max"] is not None and n["min"] >= n["max"]:
                raise ValueError(f"The {a} axis minimum must be below its maximum.")
            if n["log"] and any(v is not None and v <= 0 for v in (n["min"], n["max"])):
                raise ValueError(f"A log {a} axis only shows values above 0. Change the {a} minimum or leave it empty.")
            if n["step"] is not None and (n["step"] <= 0 or n["log"]):
                raise ValueError(f"Tick spacing must be above 0, and can't be set on a log axis.")
            was_log = now_ax.get(f"{a}scale") == "log"
            if n["label"] is not None and n["label"] != now_ax.get(f"{a}label"):
                done.append(f"{a} axis title '{n['label']}'")
            elif n["label"] is not None and prev[a]["label"] is None:
                n["label"] = None  # unchanged: leave the title to the code
            if n["log"] != was_log:
                done.append(f"{a} axis {'log' if n['log'] else 'linear'}")
            elif prev[a]["log"] is None:
                n["log"] = None
            if (n["min"], n["max"]) != (prev[a]["min"], prev[a]["max"]):
                lo = "auto" if n["min"] is None else f"{n['min']:g}"
                hi = "auto" if n["max"] is None else f"{n['max']:g}"
                done.append(f"{a} axis from {lo} to {hi}")
            if n["step"] != prev[a]["step"]:
                done.append(f"{a} ticks every {n['step']:g}" if n["step"] else f"{a} ticks automatic")
        legend = settings.get("legend") or "keep"
        if legend not in ("keep", "none") + self.LEGEND_LOCS:
            raise ValueError("Unknown legend position.")
        new["legend"] = legend
        if legend != prev["legend"]:
            done.append({"keep": "legend as the code sets it", "none": "legend hidden"}.get(legend, f"legend {legend}"))
        grid = settings.get("grid")
        if grid is not None and bool(grid) != bool(now_ax.get("grid")):
            new["grid"] = bool(grid)
            done.append("gridlines " + ("on" if grid else "off"))
        else:
            new["grid"] = prev["grid"]
        fit = settings.get("fit") or {}
        kind = fit.get("kind") or "none"
        if kind not in ("none",) + self.FIT_KINDS:
            raise ValueError("Unknown fit type.")
        rng = fit.get("range") or [None, None]
        frange = [num(rng[0], "fit range start"), num(rng[1], "fit range end")]
        n_series = sum(1 for r in rep["series"] if r.get("axes", 0) == ai and r["kind"] == "points")
        series = int(fit.get("series") if fit.get("series") not in (None, "") else -1)
        if n_series <= 1 or series >= n_series:
            series = 0 if n_series <= 1 else -1
        new["fit"] = {"kind": kind, "degree": int(fit.get("degree") or 2) if kind == "poly" else None,
                      "series": series, "equation": bool(fit.get("equation")) and kind != "none",
                      "range": frange if any(v is not None for v in frange) and kind != "none" else None}
        if None not in frange and frange[0] >= frange[1]:
            raise ValueError("The fit range must go from a lower to a higher x.")
        pf = prev["fit"]
        if new["fit"]["equation"] != bool(pf.get("equation")) and kind != "none" and kind == pf["kind"]:
            done.append("fit equation " + ("shown" if new["fit"]["equation"] else "hidden") + " on the chart")
        if (new["fit"]["kind"], new["fit"]["degree"], new["fit"]["series"], new["fit"]["range"]) != \
                (pf["kind"], pf.get("degree"), pf.get("series") if pf.get("series") is not None else -1, pf.get("range")):
            name = {"linear": "linear", "poly": f"degree {new['fit']['degree']} polynomial", "exp": "exponential",
                    "log": "logarithmic", "power": "power", "gauss": "Gaussian"}.get(kind)
            lo = "the first point" if frange[0] is None else f"{frange[0]:g}"
            hi = "the last point" if frange[1] is None else f"{frange[1]:g}"
            done.append("trendline removed" if kind == "none" else f"{name} trendline"
                        + (f" for x from {lo} to {hi}" if new["fit"]["range"] else ""))
        if not done:
            raise ValueError("Nothing changed.")

        old_code = open(os.path.join(cur["dir"], "code.py")).read()
        spec = self.state.get("spec")
        if spec and cur.get("via") in ("offline", "manual") and ai == 0:
            spec = dict(spec)
            for a in ("x", "y"):
                n = new[a]
                spec[f"{a}label"] = n["label"] if n["label"] is not None else spec.get(f"{a}label")
                if n["log"] is not None:
                    spec[f"{a}log"] = n["log"]
                spec[f"{a}lim"] = [n["min"], n["max"]] if (n["min"] is not None or n["max"] is not None) else None
                spec[f"{a}step"] = n["step"]
            if legend == "none":
                spec["legend"] = False
            elif legend != "keep":
                spec["legend"], spec["legend_loc"] = True, legend
            else:
                spec["legend"], spec["legend_loc"] = True, None
            if new["grid"] is not None:
                spec["grid"] = new["grid"]
            spec["fit"] = None if kind == "none" else {"kind": kind, "degree": new["fit"]["degree"],
                                                       "series": None if series < 0 else series,
                                                       "show_equation": new["fit"]["equation"],
                                                       **({"range": new["fit"]["range"]} if new["fit"]["range"] else {})}
            code = offline.spec_to_code(spec)
            self.state["spec"] = spec
        else:
            L = ["# >>> Plotwise format panel (set by hand in the app, no AI)", f"_ax = fig.axes[{ai}]"]
            for a in ("x", "y"):
                n = new[a]
                if n["label"] is not None:
                    L.append(f"_ax.set_{a}label({n['label']!r})")
                if n["log"] is not None:
                    L.append(f"_ax.set_{a}scale({'log' if n['log'] else 'linear'!r})")
                if n["min"] is not None or n["max"] is not None:
                    L.append(f"_ax.set_{a}lim({n['min']!r}, {n['max']!r})")
                if n["step"] is not None:
                    L.append(f"_ax.{a}axis.set_major_locator(plt.MultipleLocator({n['step']!r}))")
            if new["grid"] is not None:
                L.append(f"_ax.grid({new['grid']})")
            if kind != "none":
                for k in (range(n_series) if series < 0 else [series]):
                    args = [f"series={k}", f"kind={kind!r}"]
                    if kind == "poly":
                        args.append(f"degree={new['fit']['degree']}")
                    if new["fit"]["range"]:
                        args.append(f"fit_range=({frange[0]!r}, {frange[1]!r})")  # None = from the first / to the last point
                    if new["fit"]["equation"]:
                        args.append("show_equation=True")
                    L.append(f"pw.fit_plotted(_ax, {', '.join(args)})")
            if legend == "none":
                L.append("if _ax.get_legend() is not None:\n    _ax.get_legend().remove()")
            elif legend != "keep":
                L.append(f"pw.legend(_ax, loc={legend!r})")
            L.append("# <<< end format panel")
            code = self.BLOCK.sub("\n", old_code).rstrip() + "\n\n" + "\n".join(L) + "\n"
        summary = "Changed by hand: " + "; ".join(done) + "."
        self.log("Human", "format", summary, settings=settings, diff=self._diff(old_code, code))
        v = self._run(code, "Format panel: " + "; ".join(done), "Applied by hand, without the AI.", "manual")
        self.state["history"].append("(set by hand in the format panel) " + "; ".join(done))
        self.save()
        return v

    @staticmethod
    def _diff(a, b):
        if not a or not b:
            return None
        return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), "before.py", "after.py"))

    def _run(self, code, request, changes, via, kind="figure"):
        n = len(self.state["versions"]) + 1
        vdir = os.path.join(self.dir, f"v{n:03d}")
        os.makedirs(vdir, exist_ok=True)
        open(os.path.join(vdir, "code.py"), "w").write(code)
        static = []
        if UNSAFE.search(code):
            res = {"ok": False, "error": f"Blocked before running: the script uses {UNSAFE.search(code).group(0)!r}, "
                                         f"which Plotwise does not allow in plot code."}
        elif DATA_CHANGE.search(code):
            used = DATA_CHANGE.search(code).group(0).strip()
            hint = (" To draw a fit with its equation, use pw.fit(ax, x, y, kind=..., show_equation=True); the "
                    "dict it returns has the equation as text." if re.search(r"poly|curve_fit", used) else "")
            res = {"ok": False, "error": f"Blocked before running: the script uses {used!r}, "
                                         f"which creates or changes data values. Plotwise only allows scaling data by a "
                                         f"factor (pw.convert / pw.transform), log axes, and fits made with pw.fit.{hint}"}
        else:
            shutil.copy(os.path.join(self.dir, "data.pkl"), os.path.join(vdir, "data.pkl"))
            json.dump(self.state["style"], open(os.path.join(vdir, "style.json"), "w"), ensure_ascii=False)
            # written into the figure files themselves, so a PDF found later still says where it came from
            json.dump({"title": f"Plotwise figure v{n}" + (f" · {self.state['name']}" if self.state.get("name") else ""),
                       "session": self.sid, "version": n, "data_file": self.state["data_file"],
                       "data_sha256": self.state["data_sha256"],
                       "code_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest()},
                      open(os.path.join(vdir, "meta.json"), "w"), ensure_ascii=False)
            # the plot code never sees API keys, tokens or passwords from the environment
            env = {k: v for k, v in os.environ.items()
                   if not re.search(r"KEY|TOKEN|SECRET|PASS|ANTHROPIC|CREDENTIAL|AWS_|GITHUB", k, re.I)}
            if HOSTED:
                env["PLOTWISE_SANDBOX"] = "1"
            try:
                subprocess.run([sys.executable, os.path.join(HERE, "harness.py"), vdir], timeout=120,
                               capture_output=True, text=True, env=env)
                res = json.load(open(os.path.join(vdir, "result.json")))
            except subprocess.TimeoutExpired:
                res = {"ok": False, "error": "The script took longer than 120 s and was stopped."}
            os.remove(os.path.join(vdir, "data.pkl"))
        for m in HARDCODED.finditer(code):
            static.append({"level": "warn", "message": f"The code contains a literal list of numbers {m.group(0)[:60]!r}. "
                                                       f"Data values must come from the file, not be typed in.",
                           "series": None, "point": None})
        version = {"n": n, "dir": vdir, "request": request, "changes": changes, "via": via, "ok": res.get("ok", False),
                   "error": res.get("error"), "time": now(), "kind": kind}
        if res.get("ok"):
            series = json.load(open(os.path.join(vdir, "points.json")))
            rep = integrity.check(self.data, series, res)
            rep["issues"] = static + rep["issues"]
            if static and rep["status"] == "pass":
                rep["status"] = "warn"
            rep["style_checks"] = [{**c, "detail": readable(c.get("detail"))} for c in res.get("style_checks", [])]
            rep["axes"], rep["conversions"] = res.get("axes"), res.get("conversions")
            json.dump(rep, open(os.path.join(vdir, "integrity.json"), "w"), default=str, ensure_ascii=False)
            open(os.path.join(vdir, "inspector.html"), "w").write(
                inspector_html(os.path.join(vdir, "figure.png"), series, rep, self.state, n))
            t = rep["totals"]
            version.update(status=rep["status"], totals=t)
            self.log("App", "check",
                     f"v{n}: {rep['status'].upper()}. {t['matched']}/{t['points']} plotted points traced to source rows; "
                     f"{t['unmatched']} not in the data; {t['missing']} rows missing; {t['empty_cells']} empty cells.",
                     issues=rep["issues"], style_checks=rep["style_checks"],
                     sources=[{k: s.get(k) for k in ("label", "x_source", "y_source", "err_source", "notes")} for s in rep["series"]])
            ch = rep.get("data_changes") or []
            self.log("App", "data-change",
                     (f"v{n}: changes to the data: " + "; ".join(ch) + ". Only scaling by a factor, log axes and "
                      f"declared fits are allowed."
                      if ch else f"v{n}: no changes to the data values; every point is plotted as it is in the file."),
                     changes=ch)
        else:
            self.log("App", "run", f"v{n}: the script failed to run.", error=res.get("error"))
        self.state["versions"].append(version)
        if res.get("ok"):
            self.state["current"] = n
        self.save()
        return version

    def current(self):
        c = self.state.get("current")
        return self.state["versions"][c - 1] if c else None

    def revert(self, n):
        v = self.state["versions"][n - 1]
        if not v["ok"]:
            raise ValueError("That version did not render.")
        self.state["current"] = n
        self.save()
        self.log("Human", "revert", f"Went back to v{n}.")
        return v

    def integrity(self, n=None):
        v = self.state["versions"][(n or self.state["current"]) - 1]
        p = os.path.join(v["dir"], "integrity.json")
        return json.load(open(p)) if os.path.exists(p) else None

    # -- integrity test: inject a fault ------------------------------------
    def inject_fault(self):
        cur = self.current()
        rep = self.integrity()
        src = next((s for s in rep["series"] if s.get("y_source")), None)
        if not cur or not src:
            raise ValueError("Make a plot first.")
        sheet, ycol = src["y_source"]["sheet"], src["y_source"]["column"]
        rows = [r for r in src["rows"] if r is not None]
        if len(rows) < 2:
            raise ValueError("The fault test needs a plot with at least two points.")
        drop = rows[min(7, len(rows) - 1)]
        bump = rows[min(4, len(rows) - 2)]  # never the dropped row, so both faults show up on short series
        fault = (f"# >>> INJECTED FAULT (test only): imitates an AI quietly changing the data\n"
                 f"_d = data[{sheet!r}]\n"
                 f"_d.loc[_d.index[{bump}], {ycol!r}] = _d.loc[_d.index[{bump}], {ycol!r}] * 1.05  # nudge one value by 5%\n"
                 f"_d.drop(index=_d.index[{drop}], inplace=True)  # silently drop a row\n"
                 f"if {sheet!r} == next(iter(data)):\n    df = _d\n# <<< END FAULT\n")
        code = fault + open(os.path.join(cur["dir"], "code.py")).read()
        self.log("Human", "fault-injection",
                 f"Injected a fake AI mistake: row {excel_row(self.data[sheet], bump)} of {where(self.data[sheet], sheet)} scaled by 1.05 and row {excel_row(self.data[sheet], drop)} dropped. "
                 f"The checker has not been told.", )
        return self._run(code, "Injected fault (test)", "Fake AI mistake injected", "fault", kind="fault")

    # -- step 5: human spot check -------------------------------------------
    def spot_check(self, k=3):
        cur = self.current()
        rep = self.integrity()
        series = json.load(open(os.path.join(cur["dir"], "points.json")))
        picks = integrity.spot_points(rep["series"], series, k=k)
        out = []
        for si, i in picks:
            s, r = series[si], rep["series"][si]
            row = r["rows"][i]
            src = None
            if row is not None:
                sh = r["y_source"]["sheet"]
                src = {"sheet": sh, "where": where(self.data[sh], sh), "excel_row": excel_row(self.data[sh], row),
                       "values": {str(k): (None if pd.isna(v) else v) for k, v in self.data[sh].iloc[row].items()}}
            out.append({"series": si, "point": i, "label": r["label"], "x": s["x"][i], "y": s["y"][i],
                        "yerr": (s["yerr"][i] if s.get("yerr") else None),
                        "x_column": (r.get("x_source") or {}).get("column"), "y_column": (r.get("y_source") or {}).get("column"),
                        "x_factor": (r.get("x_source") or {}).get("factor"), "y_factor": (r.get("y_source") or {}).get("factor"),
                        "source": src, "auto_ok": r["point_ok"][i]})
        self.log("App", "spot-check", f"Picked {len(out)} points for a human check on v{cur['n']} "
                                      f"(always includes the 2nd point).", points=[(o["label"], o["point"] + 1) for o in out])
        return out

    def record_spot(self, answers, who="Human"):
        cur = self.current()
        for a in answers:
            a["version"] = cur["n"]
            a["time"] = now()
        self.state["spot_checks"] += answers
        self.save()
        bad = [a for a in answers if a.get("verdict") != "ok"]
        summary = (f"Confirmed {len(answers) - len(bad)}/{len(answers)} spot-checked points on v{cur['n']}."
                   + (f" Flagged: {', '.join(a.get('label', '?') + ' #' + str(a.get('point', 0) + 1) for a in bad)}." if bad else ""))
        self.log(who, "spot-check", summary, answers=answers)
        return summary

    # -- step 7: export -------------------------------------------------------
    def artifact_files(self, cur, include_data=True):
        """Everything that makes one figure version traceable, as {name: bytes}, plus manifest.json with
        the SHA-256 of each file, so a figure, its code, its checks and its data can be matched up later."""
        rep = self.integrity(cur["n"])
        code = open(os.path.join(cur["dir"], "code.py")).read()
        files = {f: open(os.path.join(cur["dir"], f), "rb").read()
                 for f in ("figure.pdf", "figure.png", "figure.svg", "inspector.html", "integrity.json")}
        files["plot_code.py"] = code.encode("utf-8")
        files["reproduce.py"] = self.reproduce_script(code).encode("utf-8")
        files["style.json"] = json.dumps(self.state["style"], indent=1, ensure_ascii=False).encode("utf-8")
        files["ai_log.md"] = self.log_markdown().encode("utf-8")
        files["ai_log.jsonl"] = open(os.path.join(self.dir, "log.jsonl"), "rb").read()
        if include_data:
            files["data/" + self.state["data_file"]] = open(os.path.join(self.dir, "data", self.state["data_file"]), "rb").read()
        manifest = {
            "tool": "Plotwise", "session": self.sid, "session_name": self.state.get("name") or None,
            "figure_version": cur["n"], "figure_made": cur.get("time"), "manifest_written": now(),
            "request": cur.get("request"), "made_by": {"offline": "offline engine (no AI)", "manual": "format panel (by hand)",
                                                        "fault": "fault injection test"}.get(cur.get("via"), "Claude"),
            "integrity": {"status": rep["status"], "totals": rep["totals"], "data_changes": rep.get("data_changes") or [],
                          "fits": rep.get("fits") or []},
            "data": {"file": self.state["data_file"], "sha256": self.state["data_sha256"], "included": include_data},
            "style": self.state["style_source"],
            "files": {k: {"sha256": hashlib.sha256(v).hexdigest(), "bytes": len(v)} for k, v in files.items()},
        }
        files["manifest.json"] = json.dumps(manifest, indent=1, ensure_ascii=False).encode("utf-8")
        return files, manifest

    def export_zip(self):
        cur = self.current()
        if not cur:
            raise ValueError("Nothing to export yet.")
        rep = self.integrity()
        self.log("Human", "export", f"Exported v{cur['n']} (integrity: {rep['status']}).")
        files, _ = self.artifact_files(cur)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, content in files.items():
                z.writestr(name, content)
            z.writestr("README.txt", self.bundle_readme(cur, rep))
        return buf.getvalue(), f"plotwise_{self.sid}_v{cur['n']}.zip"

    def deposit_github(self, token, repo, branch="", folder="", include_data=False):
        """Commit the current figure's code and artifacts to the scientist's own GitHub repository.
        The token is passed straight to GitHub for this request and is never stored or logged."""
        from . import github
        cur = self.current()
        if not cur:
            raise ValueError("Make a figure first.")
        rep = self.integrity()
        files, manifest = self.artifact_files(cur, include_data=include_data)
        files = {"README.md": self.github_readme(cur, rep, manifest).encode("utf-8"), **files}
        title = self.state.get("name") or os.path.splitext(self.state["data_file"])[0]
        message = (f"Plotwise figure v{cur['n']}: {title}\n\nRequest: {cur.get('request')}\n"
                   f"Integrity check: {rep['status'].upper()} ({rep['totals']['matched']}/{rep['totals']['points']} points traced)\n"
                   f"Data: {self.state['data_file']} sha256 {self.state['data_sha256']}\n"
                   f"Plotwise session {self.sid}")
        res = github.deposit(token, repo, branch, folder, files, message)
        res.update(version=cur["n"], time=now(), files=sorted(files))
        cur.setdefault("github", []).append(res)
        self.save()
        self.log("Human", "github", f"Saved v{cur['n']} to GitHub: {res['repo']}, branch {res['branch']}"
                                    + (f", folder {res['folder']}" if res["folder"] else "") + f", commit {res['commit'][:7]}.",
                 commit_url=res["commit_url"], folder_url=res["folder_url"], files=res["files"],
                 data_included=include_data, manifest_sha256=hashlib.sha256(files["manifest.json"]).hexdigest())
        return res

    def github_readme(self, cur, rep, manifest):
        t = rep["totals"]
        title = self.state.get("name") or self.state["data_file"]
        changes = rep.get("data_changes") or []
        rows = [("Made with", f"Plotwise, session `{self.sid}`, figure v{cur['n']}, {cur.get('time', '')[:16].replace('T', ' ')}"),
                ("Request", (cur.get("request") or "").replace("|", "/")),
                ("Made by", manifest["made_by"]),
                ("Data", f"`{self.state['data_file']}`, SHA-256 `{self.state['data_sha256']}`"
                         + (" (copy in `data/`)" if manifest["data"]["included"] else " (not included here)")),
                ("Integrity check", f"**{rep['status'].upper()}**: {t['matched']}/{t['points']} plotted points traced to a source row, "
                                    f"{t['unmatched']} not in the data, {t['missing']} rows missing"),
                ("Changes to the data", "; ".join(changes).replace("|", "/") if changes else "None, every point is plotted as it is in the file"),
                ("Style", str(self.state["style_source"]).replace("|", "/"))]
        files = [("figure.pdf / .png / .svg", "the figure (the PDF and PNG carry the session, version and data SHA-256 in their metadata)"),
                 ("plot_code.py", "the plotting code that made it"), ("reproduce.py", "rebuilds the figure from the data file without Plotwise"),
                 ("integrity.json", "the point-by-point check"), ("inspector.html", "open in a browser to click any point and see its source row"),
                 ("ai_log.md", "every step the AI, the app and the scientist took"), ("style.json", "the style that was applied"),
                 ("manifest.json", "SHA-256 of every file here, to show nothing was changed afterwards")]
        return "\n".join([f"# {title} · figure v{cur['n']}", "", "![figure](figure.png)", "", "| | |", "|---|---|"]
                         + [f"| {k} | {v} |" for k, v in rows] + ["", "## Files", ""]
                         + [f"- `{k}`: {v}" for k, v in files] + ["", "## Rebuild the figure", "", "```",
                            f"python reproduce.py path/to/{self.state['data_file']}", "```",
                            "", "reproduce.py warns if the data file's SHA-256 differs from the one above.", ""])

    def reproduce_script(self, code):
        helpers = open(os.path.join(HERE, "pwstyle.py")).read()
        reader = open(os.path.join(HERE, "tables.py")).read()
        return f'''"""Rebuild this figure from the data file, without Plotwise.

    python reproduce.py            # reads data/{self.state["data_file"]}, writes figure_reproduced.pdf
Requires: numpy, pandas, matplotlib (and openpyxl for .xlsx).
Data SHA-256 when exported: {self.state["data_sha256"]}
"""
import hashlib, json, os, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ---- Plotwise style helpers (verbatim copy) ----
{helpers}
# ---- end helpers ----

# ---- Plotwise spreadsheet reader (verbatim copy) ----
{reader}
# ---- end reader ----

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "data", {self.state["data_file"]!r})
EXPECTED_SHA256 = {self.state["data_sha256"]!r}
digest = hashlib.sha256(open(DATA, "rb").read()).hexdigest()
if digest != EXPECTED_SHA256:
    print("WARNING: data file differs from the one this figure was made from.")
data, _notes = read_table(DATA)  # same table finder Plotwise used
df = next(iter(data.values()))
style = json.load(open(os.path.join(HERE, "style.json"), encoding="utf-8"))
pw = Plotwise(style)
pw.apply_rcparams()

# ---- plot code (as generated and checked in Plotwise) ----
{code}
# ---- end plot code ----

fig = globals().get("fig") or plt.gcf()
fig.savefig(os.path.join(HERE, "figure_reproduced.pdf"))
print("wrote figure_reproduced.pdf")
'''

    def bundle_readme(self, cur, rep):
        t = rep["totals"]
        return (f"Plotwise export, session {self.sid}, figure v{cur['n']}\n"
                f"Exported {now()}\n\n"
                f"figure.pdf       vector figure for LaTeX (\\includegraphics{{figure.pdf}})\n"
                f"figure.png/.svg  raster preview / editable vector\n"
                f"inspector.html   open in a browser: hover or click any point to see its value and source row\n"
                f"reproduce.py     rebuilds the figure from data/ without Plotwise\n"
                f"ai_log.md        every step taken by the AI, the app and the human, in order\n"
                f"integrity.json   machine-readable check results\n\n"
                f"Integrity: {rep['status'].upper()} - {t['matched']}/{t['points']} plotted points traced to source rows, "
                f"{t['unmatched']} not in the data, {t['missing']} rows missing, {t['empty_cells']} empty cells.\n"
                f"Style: {self.state['style_source']}\nData: {self.state['data_file']} (SHA-256 {self.state['data_sha256']})\n")

    def log_markdown(self):
        L = [f"# Plotwise AI step log", "",
             f"- Session: `{self.sid}`", f"- Data: `{self.state['data_file']}` (SHA-256 `{self.state['data_sha256']}`)",
             f"- Style: {self.state['style_source']}", f"- Generated: {now()}", "",
             "| # | Time | Actor | Step | What happened |", "|---|---|---|---|---|"]
        entries = self.read_log()
        for e in entries:
            L.append(f"| {e['n']} | {e['time'][11:19]} | {e['actor']} | {e['step']} | {e['summary'].replace('|', '/')} |")
        L += ["", "## Details", ""]
        for e in entries:
            d = e["details"]
            if not d:
                continue
            L.append(f"### {e['n']}. {e['actor']} · {e['step']} · {e['time']}")
            L.append(e["summary"])
            for k in ("diff", "error"):
                if d.get(k):
                    L += [f"**{k}**", "```diff" if k == "diff" else "```", d[k].rstrip(), "```"]
            for k in ("issues", "style_checks", "answers", "problems", "sources"):
                if d.get(k):
                    L.append(f"**{k}**")
                    for it in d[k]:
                        L.append("- " + (f"[{it.get('level')}] {it.get('message') or it.get('name', '')} {it.get('detail', '')}"
                                         if isinstance(it, dict) and ("level" in it) else json.dumps(it, ensure_ascii=False, default=str)))
            if d.get("backend"):
                L.append(f"- backend: {d['backend']}")
            L.append("")
        return "\n".join(L)
