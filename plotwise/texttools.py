"""Turns LaTeX / matplotlib mathtext into plain readable text for warnings, errors and the log.

    readable(r"$\\pi^{-}$ candidates")         -> "π⁻ candidates"
    readable(r"m($D^0D^0\\pi^+$) [GeV/$c^2$]")  -> "m(D⁰D⁰π⁺) [GeV/c²]"
    readable(r"$\\sqrt{s} = 13\\,\\mathrm{TeV}$") -> "√s = 13 TeV"

Self-contained (standard library only) so it can be copied anywhere.
"""
import re

GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ",
    "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "µ", "nu": "ν",
    "xi": "ξ", "omicron": "ο", "pi": "π", "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ", "sigma": "σ", "varsigma": "ς",
    "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ",
    "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}
SYMBOLS = {
    "pm": "±", "mp": "∓", "times": "×", "cdot": "·", "div": "÷", "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥",
    "neq": "≠", "ne": "≠", "approx": "≈", "simeq": "≃", "sim": "~", "equiv": "≡", "propto": "∝", "infty": "∞",
    "rightarrow": "→", "to": "→", "longrightarrow": "→", "leftarrow": "←", "Rightarrow": "⇒", "leftrightarrow": "↔",
    "uparrow": "↑", "downarrow": "↓", "degree": "°", "circ": "°", "prime": "′", "ell": "ℓ", "hbar": "ħ",
    "partial": "∂", "nabla": "∇", "sum": "Σ", "prod": "Π", "int": "∫", "langle": "⟨", "rangle": "⟩",
    "AA": "Å", "angstrom": "Å", "ldots": "…", "dots": "…", "cdots": "⋯", "perp": "⊥", "parallel": "∥",
    "in": "∈", "star": "*", "ast": "*", "dagger": "†", "%": "%", "$": "$", "&": "&", "#": "#", "_": "_",
    "{": "{", "}": "}", "|": "‖", "lvert": "|", "rvert": "|", "vert": "|", "slash": "/", "backslash": "\\",
    "lbrace": "{", "rbrace": "}", "lbrack": "[", "rbrack": "]", "textdegree": "°", "celsius": "°C",
    "micro": "µ", "permil": "‰", "textpm": "±", "texttimes": "×",
}
SPACES = {",": " ", ";": " ", ":": " ", ">": " ", " ": " ", "!": "", "quad": " ", "qquad": " ", "thinspace": " ",
          "enspace": " ", "space": " "}
# commands whose argument is shown as it is
FONTS = {"mathrm", "rm", "mathit", "it", "mathbf", "bf", "text", "textrm", "textit", "textbf", "textsf", "texttt",
         "mathsf", "mathtt", "mathcal", "mathbb", "mathfrak", "mathscr", "operatorname", "boldsymbol", "bm", "emph",
         "mathnormal", "mbox", "hbox", "ensuremath", "unit", "si", "mathdefault"}
SKIP = {"displaystyle", "textstyle", "scriptstyle", "left", "right", "big", "Big", "bigg", "Bigg", "bigl", "bigr",
        "Bigl", "Bigr", "limits", "nolimits", "mathstrut", "strut", "!"}
# combining marks for accents: \bar{D} -> D̄
ACCENTS = {"bar": "̄", "overline": "̅", "hat": "̂", "widehat": "̂", "tilde": "̃",
           "widetilde": "̃", "dot": "̇", "ddot": "̈", "vec": "⃗", "check": "̌",
           "acute": "́", "grave": "̀", "breve": "̆", "underline": "̲"}
SUP = dict(zip("0123456789+-−=()niabcdefghjklmoprstuvwxyzABDEGHIJKLMNOPRTUVW*′",
               "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻⁼⁽⁾ⁿⁱᵃᵇᶜᵈᵉᶠᵍʰʲᵏˡᵐᵒᵖʳˢᵗᵘᵛʷˣʸᶻᴬᴮᴰᴱᴳᴴᴵᴶᴷᴸᴹᴺᴼᴾᴿᵀᵁⱽᵂ*′"))
SUP.update({"°": "°", "∗": "*", "†": "†", "′": "′", "″": "″", "→": "→"})
SUB = dict(zip("0123456789+-−=()aehijklmnoprstuvx", "₀₁₂₃₄₅₆₇₈₉₊₋₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ"))


class _Math:
    """A tiny recursive reader for one $...$ segment."""

    def __init__(self, s):
        self.s, self.i = s, 0

    def done(self):
        return self.i >= len(self.s)

    def group(self):
        """The next argument: {...} or one token."""
        while not self.done() and self.s[self.i] == " ":
            self.i += 1
        if self.done():
            return ""
        if self.s[self.i] == "{":
            depth, j = 0, self.i
            while j < len(self.s):
                depth += {"{": 1, "}": -1}.get(self.s[j], 0)
                if depth == 0:
                    break
                j += 1
            inner = self.s[self.i + 1:j]
            self.i = j + 1
            return _Math(inner).render()
        return self.token()

    def token(self):
        c = self.s[self.i]
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+|.)", self.s[self.i:])
            name = m.group(1) if m else ""
            self.i += len(m.group(0)) if m else 1
            if name in GREEK:
                return GREEK[name]
            if name in SPACES:
                return SPACES[name]
            if name in SKIP:
                return ""
            if name in SYMBOLS:
                return SYMBOLS[name]
            if name in FONTS:
                return self.group()
            if name in ACCENTS:
                arg = self.group()
                return "".join(ch + ACCENTS[name] if ch.strip() else ch for ch in arg)
            if name in ("frac", "dfrac", "tfrac"):
                a, b = self.group(), self.group()
                return f"{_wrap(a)}/{_wrap(b)}"
            if name == "sqrt":
                if not self.done() and self.s[self.i] == "[":
                    j = self.s.find("]", self.i)
                    n = self.s[self.i + 1:j] if j > 0 else ""
                    self.i = j + 1 if j > 0 else self.i
                    return {"3": "∛", "4": "∜"}.get(n.strip(), f"{n}√") + _wrap(self.group())
                return "√" + _wrap(self.group())
            if name in ("mathring",):
                return self.group() + "̊"
            return name  # unknown command: show its name rather than a backslash
        self.i += 1
        if c in "^_":
            arg = self.group()
            table = SUP if c == "^" else SUB
            short = len(arg) <= 2 or re.fullmatch(r"[\d+\-−=()]+", arg)
            if arg and short and all(ch in table for ch in arg):
                return "".join(table[ch] for ch in arg)
            if c == "_":  # p_{T} -> pT, sigma_{stat} -> σ_stat, m_{D^0D^0} -> m(D⁰D⁰)
                return arg if len(arg) <= 2 else "_" + arg if arg.isalpha() else f"({arg})"
            return "^" + (arg if len(arg) == 1 else f"({arg})")
        if c in "{}":
            return ""
        if c == "~":
            return " "
        return c

    def render(self):
        out = []
        while not self.done():
            out.append(self.token() if self.s[self.i] != "{" else self.group())
        return "".join(out)


def _wrap(s):
    return s if re.fullmatch(r"[\w.µ′⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+", s) else f"({s})"


def math_to_text(s):
    s = re.sub(r"\\\\(?=[A-Za-z{}$%_,;:! ])", r"\\", s)  # Python reprs double the backslash
    return re.sub(r"[ \t]{2,}", " ", _Math(s).render())


_CMD = re.compile(r"\\\\?(" + "|".join(sorted(set(GREEK) | {k for k in SYMBOLS if k.isalpha()} | FONTS | set(ACCENTS)
                                            | {"frac", "sqrt"}, key=len, reverse=True)) + r")(?![A-Za-z])")


def readable(text):
    """Plain-text version of a message that may hold $...$ math or stray LaTeX commands. Idempotent."""
    if text is None:
        return text
    t = str(text)
    if "$" not in t and "\\" not in t:
        return t
    t = t.replace("\\$", "\x00")
    parts = re.split(r"(?<!\$)\$\$?([^$]+?)\$\$?", t)  # odd items are math
    out = []
    for k, p in enumerate(parts):
        if k % 2:
            out.append(math_to_text(p))
        elif _CMD.search(p):  # LaTeX outside $...$, e.g. a column header "\mathrm{p_T}"
            out.append(re.sub(r"\\\\?[A-Za-z]+(?:\{[^{}]*\}){0,2}|[\^_]\{[^{}]*\}",
                              lambda m: math_to_text(m.group(0)) if _CMD.match(m.group(0)) or m.group(0)[0] in "^_" else m.group(0), p))
        else:
            out.append(p)
    return "".join(out).replace("\x00", "$")
