"""Talks to Claude. Three backends, picked automatically:

1. ANTHROPIC_API_KEY set           -> Anthropic Python SDK
2. `claude` CLI on PATH (logged in) -> Claude Code in print mode (works with a Pro/Max login)
3. neither                          -> offline rule-based engine (see offline.py)
"""
import json
import os
import re
import shutil
import subprocess

MODEL = os.environ.get("PLOTWISE_MODEL", "claude-opus-5-5")

CODE_RULES = """You write matplotlib code for Plotwise, a tool that turns already-analysed physics data
into publication figures that follow a journal or collaboration style guide.

The code runs with these names already defined (do not import or redefine them):
  df     pandas DataFrame: the first table of the uploaded file
  data   dict {name: DataFrame} with every table (a CSV file gives one table named "data"; call it
         "the CSV file", not a sheet, when you talk about it)
  style  dict: the active style spec (shown below)
  pw     helper object:
           fig, ax = pw.figure(nrows=1, ncols=1, aspect=None)   # sized to the style's width
           pw.label(name, unit)        # axis label in the style's unit format, e.g. "Time [s]"
           pw.data_style(i)            # dict of kwargs for ax.errorbar(...) for data series i
                                       # (marker, ms, color, capsize, linestyle...; to change one,
                                       #  update the dict rather than passing the keyword twice)
           pw.line_style(i)            # kwargs for ax.plot(...) for curve i
           pw.legend(ax, loc=None)     # legend with the style's settings
           pw.experiment_label(ax, extra=None)  # e.g. "LHCb"; no-op if the style has none
           pw.convert(values, from_unit, to_unit, axis="x"|"y")  # logged unit conversion
           pw.transform(values, factor, axis="x"|"y", why="...")  # logged scaling by a constant, e.g.
                                       # time = frame / frame rate, metres = pixels * scale
           pw.fit(ax, x, y, kind="linear", yerr=None, degree=None, fit_range=None, label=None,
                  show_equation=False, of=None)
                                       # the ONLY way to fit: least-squares fit of the plotted data, drawn as
                                       # a labelled line ("Linear fit"; of="Air" gives "Air: linear fit");
                                       # kind "linear", "poly" (degree 2-6), "exp" (a·e^(b·x)), "log" (a + b·ln x),
                                       # "power" (a·x^b), "gauss" (peak + constant); fit_range=(lo, hi) limits
                                       # the x range fitted. show_equation=True writes the fitted equation and
                                       # R² (χ²/ndf when yerr is given) into the legend, like Excel's "Display
                                       # equation on chart"; use it whenever the user asks to see the equation.
                                       # Returns a dict with params, errors, chi2, ndf, r2, "equation" (with
                                       # uncertainties) and "equation_short" (the legend text); to place the
                                       # equation elsewhere, pass that string to ax.text(...). Never compute
                                       # fitted values or equations yourself (np.polyval/polyfit are blocked).
           pw.fit_plotted(ax, series=0, kind="linear", ...)  # same, for a data series already drawn on ax
           pw.colors                   # palette list
  plt, np, pd

Hard rules (integrity):
- Every plotted number must come from `df`/`data`. Never type data values into the code,
  never invent, smooth, interpolate, resample or drop rows, even if the user asks.
- The ONLY changes to data values you may make are:
    1. scaling by a unit factor with pw.convert(values, from_unit, to_unit, axis=...), e.g. MeV/c -> GeV/c,
       and updating the axis label to the new unit;
    2. scaling by a constant from the file with pw.transform(values, factor, axis=..., why=...) (no offsets);
    3. showing an axis on a log scale with ax.set_xscale("log") / ax.set_yscale("log"); never take np.log of values;
    4. when the user asks for a fit (linear, polynomial, exponential, logarithmic, power, Gaussian), calling
       pw.fit(ax, x, y, ...) with the same x, y (and yerr) you plotted. The data points stay drawn as they are.
       Never fit any other way: np.polyfit, curve_fit and scipy are blocked. A fit kind pw.fit does not offer
       is not allowed; say so in CHANGES.
  Everything else is forbidden: offsets, sums or differences of columns, normalising, smoothing, models that are not
  pw.fit fits, interpolating, filling gaps, resampling, filtering or dropping rows, or computing new data or curves.
  If the user asks for one of these, do not do it: keep the plot as it is and say in CHANGES that Plotwise does not
  allow it. To show part of the range use axis limits, never by removing rows. Every allowed change, fits included,
  is shown to the user as a warning and in the log.
- A table named "<sheet> constants" holds labelled single values from the file (columns Quantity, Value,
  maybe Unit/Uncertainty/Cell). When a plotted quantity is a column scaled by a constant, read the constant
  from that table by its Quantity label (never type its value) and apply it with pw.transform(...), saying why.
- Draw measured data with ax.errorbar(..., **pw.data_style(i), label=...) using the
  uncertainty columns when they exist. Draw model/fit curves that are columns in the file with
  ax.plot(..., **pw.line_style(i)).
- A block between "# >>> Plotwise format panel" and "# <<< end format panel" holds settings the scientist set by
  hand. Keep it, unchanged and at the end, unless the request changes one of those settings; then change the value
  inside the block.
- Follow the style spec: no in-figure title unless the style allows it, unit format via pw.label,
  experiment label via pw.experiment_label(ax) when the style has one.
- Write superscripts, subscripts and particle charges with matplotlib mathtext, e.g.
  r"$J/\\psi\\,K^{+}\\pi^{-}$", never Unicode superscript characters (fonts may lack them).
- Assign the figure to a variable named `fig`. No plt.show(), no savefig, no file or network access,
  no imports other than matplotlib/numpy/pandas submodules.

Reply with the complete script in ONE ```python block, then one line starting with
"CHANGES:" that says in plain words what the script does or what you changed."""

STYLE_RULES = """You read a journal's or collaboration's author guidelines and fill in a JSON style spec
for matplotlib figures. Use only what the document says; keep the template values for anything it
does not cover. Set "name" to the journal or collaboration name and "source" to one sentence naming
the document. Set "rules" to a short summary of every rule the document gives for FIGURES (size and
width, fonts and font sizes, line widths, axis labels and units, legends, colours, error bars, titles
and captions, file format and resolution, labels such as an experiment name), one plain sentence each,
most important first; ignore rules about the manuscript text. If the document has no figure rules,
say so as the only rule. Reply with ONE ```json block containing the full spec."""


_PATCH_FORMAT = """Reply with ONE ```json block holding ONLY the fields to set, using the spec's own keys and
structure: sections "figure" (width_mm, aspect, dpi), "font" (family: serif | sans-serif | monospace, names: list
of font names, most wanted first, size_pt, label_size_pt, tick_size_pt, mathtext), "axes" (linewidth,
ticks_direction: in | out, ticks_all_sides, minor_ticks, grid, title), "labels" (unit_style: brackets | parentheses
| slash), "data" (marker, markersize, color or null, capsize, elinewidth), "lines" (linewidth, colors: the palette
as hex strings, styles), "legend" (frameon, fontsize_pt, loc), and "experiment_label" ({"text": ...} or null).
Leave out everything not covered. Then one line starting "LEARNED:" that lists what you set in plain words,
separated by semicolons, e.g. "LEARNED: Arial 8 pt; 85 mm wide; colour-blind palette"."""

STYLE_PATCH_RULES = ("You turn a scientist's description of the figure style they want into a patch for a "
                     "matplotlib style spec. You get the current spec. Set only what they asked for; never "
                     "invent other rules. " + _PATCH_FORMAT)

STYLE_CODE_RULES = ("You read a plotting script a scientist used before (Python/matplotlib, PyROOT or ROOT C++, "
                    "R/ggplot2, MATLAB, Julia...) and copy its FIGURE STYLE into a patch for a matplotlib style "
                    "spec: colours in the order used (convert ROOT colours such as kRed+1 and named colours to hex), "
                    "fonts and font sizes in points, figure size, markers, line widths, error-bar caps, tick "
                    "direction and sides, grid, legend frame, unit format in axis labels and any experiment label "
                    "such as 'LHCb'. Ignore what the script computes. " + _PATCH_FORMAT)

STYLE_FIGURE_RULES = ("This image is a figure a scientist made before. Copy its visual style into a patch for a "
                      "matplotlib style spec: serif or sans-serif text, marker shapes, filled or open markers, line "
                      "widths, tick direction and whether ticks are on all four sides, grid, legend frame, unit "
                      "format in the axis labels and any experiment label such as 'LHCb'. Use the measured colours "
                      "you are given for lines.colors. " + _PATCH_FORMAT)


def detect_backend():
    forced = os.environ.get("PLOTWISE_BACKEND")
    if forced:
        return forced
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    if shutil.which("claude"):
        return "cli"
    return "offline"


BACKEND_NAMES = {"api": f"Claude API ({MODEL})", "cli": "Claude Code (your Claude login)", "offline": "Offline rules (no AI)"}


class LLMError(Exception):
    pass


def ask(system, user, backend=None):
    backend = backend or detect_backend()
    if backend == "api":
        return _ask_api(system, user)
    if backend == "cli":
        return _ask_cli(system, user)
    raise LLMError("No Claude backend available")


def _ask_api(system, user):
    import anthropic

    client = anthropic.Anthropic()
    kwargs = dict(model=MODEL, max_tokens=16000, system=system,
                  messages=[{"role": "user", "content": user}],
                  output_config={"effort": os.environ.get("PLOTWISE_EFFORT", "medium")})
    try:
        resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    except TypeError:
        resp = client.messages.create(**kwargs)
    if resp.stop_reason == "refusal":
        raise LLMError("Claude declined this request")
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def ask_image(system, text, image_bytes, media_type):
    """Ask Claude about an image (API backend only; Claude Code's print mode takes text)."""
    import base64

    import anthropic

    client = anthropic.Anthropic()
    resp = client.messages.create(model=MODEL, max_tokens=4000, system=system, messages=[{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                     "data": base64.b64encode(image_bytes).decode()}},
        {"type": "text", "text": text}]}])
    if resp.stop_reason == "refusal":
        raise LLMError("Claude declined this request")
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def _ask_cli(system, user):
    import tempfile
    # full path, so Windows can run claude.cmd / claude.exe; system prompt via a file avoids shell quoting
    exe = shutil.which("claude") or "claude"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(system)
    cmd = [exe, "-p", "--output-format", "json", "--system-prompt-file", f.name, "--tools", "",
           "--no-session-persistence"]
    if os.environ.get("PLOTWISE_CLI_MODEL"):
        cmd += ["--model", os.environ["PLOTWISE_CLI_MODEL"]]
    try:
        p = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=300, encoding="utf-8")
    except subprocess.TimeoutExpired:
        raise LLMError("Claude Code timed out")
    except OSError as e:
        raise LLMError(f"Could not start Claude Code: {e}")
    finally:
        os.unlink(f.name)
    try:
        d = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise LLMError(f"Claude Code failed: {(p.stderr or p.stdout)[:400]}")
    if d.get("is_error"):
        raise LLMError(f"Claude Code error: {d.get('result')}")
    return d.get("result", "")


def extract_block(text, lang):
    m = re.search(rf"```{lang}\s*\n(.*?)```", text, re.S) or re.search(r"```\w*\s*\n(.*?)```", text, re.S)
    return m.group(1).strip() if m else None


def extract_changes(text):
    m = re.search(r"CHANGES:\s*(.+)", text)
    return m.group(1).strip() if m else ""


def code_prompt(data_summary, style, request, current_code=None, history=None, error=None):
    parts = [f"STYLE SPEC:\n```json\n{json.dumps(style, indent=1, ensure_ascii=False)}\n```",
             f"DATA:\n{data_summary}"]
    if history:
        parts.append("EARLIER REQUESTS (oldest first):\n" + "\n".join(f"- {h}" for h in history[-12:]))
    if current_code:
        parts.append(f"CURRENT SCRIPT:\n```python\n{current_code}\n```")
    if error:
        parts.append(f"THE CURRENT SCRIPT FAILED WITH:\n```\n{error[-2500:]}\n```\nFix it.")
    parts.append(f"REQUEST: {request}" if not current_code else
                 f"EDIT REQUEST: {request}\nReturn the full updated script; change only what the request needs.")
    return "\n\n".join(parts)
