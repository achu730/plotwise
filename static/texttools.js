// lookup tables copied from plotwise/texttools.py; keep the two in step
const TEX = {"GREEK":{"alpha":"α","beta":"β","gamma":"γ","delta":"δ","epsilon":"ε","varepsilon":"ε","zeta":"ζ","eta":"η","theta":"θ","vartheta":"ϑ","iota":"ι","kappa":"κ","lambda":"λ","mu":"µ","nu":"ν","xi":"ξ","omicron":"ο","pi":"π","varpi":"ϖ","rho":"ρ","varrho":"ϱ","sigma":"σ","varsigma":"ς","tau":"τ","upsilon":"υ","phi":"φ","varphi":"φ","chi":"χ","psi":"ψ","omega":"ω","Gamma":"Γ","Delta":"Δ","Theta":"Θ","Lambda":"Λ","Xi":"Ξ","Pi":"Π","Sigma":"Σ","Upsilon":"Υ","Phi":"Φ","Psi":"Ψ","Omega":"Ω"},"SYMBOLS":{"pm":"±","mp":"∓","times":"×","cdot":"·","div":"÷","leq":"≤","le":"≤","geq":"≥","ge":"≥","neq":"≠","ne":"≠","approx":"≈","simeq":"≃","sim":"~","equiv":"≡","propto":"∝","infty":"∞","rightarrow":"→","to":"→","longrightarrow":"→","leftarrow":"←","Rightarrow":"⇒","leftrightarrow":"↔","uparrow":"↑","downarrow":"↓","degree":"°","circ":"°","prime":"′","ell":"ℓ","hbar":"ħ","partial":"∂","nabla":"∇","sum":"Σ","prod":"Π","int":"∫","langle":"⟨","rangle":"⟩","AA":"Å","angstrom":"Å","ldots":"…","dots":"…","cdots":"⋯","perp":"⊥","parallel":"∥","in":"∈","star":"*","ast":"*","dagger":"†","%":"%","$":"$","&":"&","#":"#","_":"_","{":"{","}":"}","|":"‖","lvert":"|","rvert":"|","vert":"|","slash":"/","backslash":"\\","lbrace":"{","rbrace":"}","lbrack":"[","rbrack":"]","textdegree":"°","celsius":"°C","micro":"µ","permil":"‰","textpm":"±","texttimes":"×"},"SPACES":{",":" ",";":" ",":":" ",">":" "," ":" ","!":"","quad":" ","qquad":" ","thinspace":" ","enspace":" ","space":" "},"FONTS":["bf","bm","boldsymbol","emph","ensuremath","hbox","it","mathbb","mathbf","mathcal","mathdefault","mathfrak","mathit","mathnormal","mathrm","mathscr","mathsf","mathtt","mbox","operatorname","rm","si","text","textbf","textit","textrm","textsf","texttt","unit"],"SKIP":["!","Big","Bigg","Bigl","Bigr","big","bigg","bigl","bigr","displaystyle","left","limits","mathstrut","nolimits","right","scriptstyle","strut","textstyle"],"ACCENTS":{"bar":"̄","overline":"̅","hat":"̂","widehat":"̂","tilde":"̃","widetilde":"̃","dot":"̇","ddot":"̈","vec":"⃗","check":"̌","acute":"́","grave":"̀","breve":"̆","underline":"̲"},"SUP":{"0":"⁰","1":"¹","2":"²","3":"³","4":"⁴","5":"⁵","6":"⁶","7":"⁷","8":"⁸","9":"⁹","+":"⁺","-":"⁻","−":"⁻","=":"⁼","(":"⁽",")":"⁾","n":"ⁿ","i":"ⁱ","a":"ᵃ","b":"ᵇ","c":"ᶜ","d":"ᵈ","e":"ᵉ","f":"ᶠ","g":"ᵍ","h":"ʰ","j":"ʲ","k":"ᵏ","l":"ˡ","m":"ᵐ","o":"ᵒ","p":"ᵖ","r":"ʳ","s":"ˢ","t":"ᵗ","u":"ᵘ","v":"ᵛ","w":"ʷ","x":"ˣ","y":"ʸ","z":"ᶻ","A":"ᴬ","B":"ᴮ","D":"ᴰ","E":"ᴱ","G":"ᴳ","H":"ᴴ","I":"ᴵ","J":"ᴶ","K":"ᴷ","L":"ᴸ","M":"ᴹ","N":"ᴺ","O":"ᴼ","P":"ᴾ","R":"ᴿ","T":"ᵀ","U":"ᵁ","V":"ⱽ","W":"ᵂ","*":"*","′":"′","°":"°","∗":"*","†":"†","″":"″","→":"→"},"SUB":{"0":"₀","1":"₁","2":"₂","3":"₃","4":"₄","5":"₅","6":"₆","7":"₇","8":"₈","9":"₉","+":"₊","-":"₋","−":"₋","=":"₌","(":"₍",")":"₎","a":"ₐ","e":"ₑ","h":"ₕ","i":"ᵢ","j":"ⱼ","k":"ₖ","l":"ₗ","m":"ₘ","n":"ₙ","o":"ₒ","p":"ₚ","r":"ᵣ","s":"ₛ","t":"ₜ","u":"ᵤ","v":"ᵥ","x":"ₓ"}};

// LaTeX / mathtext -> readable text, the same rules as plotwise/texttools.py:  "$\pi^{-}$" -> "π⁻"
function texMath(src) {
  const s = src.replace(/\\\\(?=[A-Za-z{}$%_,;:! ])/g, '\\');
  let i = 0;
  const wrap = x => /^[\w.µ′⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+$/.test(x) ? x : `(${x})`;
  const fonts = new Set(TEX.FONTS), skip = new Set(TEX.SKIP);
  function group() {
    while (i < s.length && s[i] === ' ') i++;
    if (i >= s.length) return '';
    if (s[i] !== '{') return token();
    let depth = 0, j = i;
    for (; j < s.length; j++) { if (s[j] === '{') depth++; else if (s[j] === '}') depth--; if (depth === 0) break; }
    const inner = s.slice(i + 1, j); i = j + 1;
    return texMath(inner);
  }
  function token() {
    const c = s[i];
    if (c === '\\') {
      const m = /^\\([A-Za-z]+|.)/.exec(s.slice(i)); const name = m ? m[1] : '';
      i += m ? m[0].length : 1;
      if (name in TEX.GREEK) return TEX.GREEK[name];
      if (name in TEX.SPACES) return TEX.SPACES[name];
      if (skip.has(name)) return '';
      if (name in TEX.SYMBOLS) return TEX.SYMBOLS[name];
      if (fonts.has(name)) return group();
      if (name in TEX.ACCENTS) { const a = group(); return [...a].map(ch => ch.trim() ? ch + TEX.ACCENTS[name] : ch).join(''); }
      if (name === 'frac' || name === 'dfrac' || name === 'tfrac') { const a = group(), b = group(); return `${wrap(a)}/${wrap(b)}`; }
      if (name === 'sqrt') {
        if (s[i] === '[') { const j = s.indexOf(']', i); const n = j > 0 ? s.slice(i + 1, j).trim() : ''; if (j > 0) i = j + 1; return ({ 3: '∛', 4: '∜' }[n] || n + '√') + wrap(group()); }
        return '√' + wrap(group());
      }
      if (name === 'mathring') return group() + '̊';
      return name;
    }
    i++;
    if (c === '^' || c === '_') {
      const arg = group(), table = c === '^' ? TEX.SUP : TEX.SUB;
      const short = [...arg].length <= 2 || /^[\d+\-−=()]+$/.test(arg);
      if (arg && short && [...arg].every(ch => ch in table)) return [...arg].map(ch => table[ch]).join('');
      if (c === '_') return [...arg].length <= 2 ? arg : /^[A-Za-z]+$/.test(arg) ? '_' + arg : `(${arg})`;
      return '^' + ([...arg].length === 1 ? arg : `(${arg})`);
    }
    if (c === '{' || c === '}') return '';
    if (c === '~') return ' ';
    return c;
  }
  const out = [];
  while (i < s.length) out.push(s[i] === '{' ? group() : token());
  return out.join('').replace(/[ \t]{2,}/g, ' ');
}
const TEX_CMD = new RegExp('\\\\\\\\?(' + [...Object.keys(TEX.GREEK), ...Object.keys(TEX.SYMBOLS).filter(k => /^[A-Za-z]+$/.test(k)), ...TEX.FONTS,
  ...Object.keys(TEX.ACCENTS), 'frac', 'sqrt'].sort((a, b) => b.length - a.length).join('|') + ')(?![A-Za-z])');
function readable(text) {
  if (text == null) return text;
  let t = String(text);
  if (!t.includes('$') && !t.includes('\\')) return t;
  t = t.replace(/\\\$/g, '\x00');
  const parts = t.split(/(?<!\$)\$\$?([^$]+?)\$\$?/);
  return parts.map((p, k) => k % 2 ? texMath(p) : TEX_CMD.test(p)
    ? p.replace(/\\\\?[A-Za-z]+(?:\{[^{}]*\}){0,2}|[\^_]\{[^{}]*\}/g, m => (new RegExp('^' + TEX_CMD.source).test(m) || '^_'.includes(m[0])) ? texMath(m) : m)
    : p).join('').replace(/\x00/g, '$');
}

// Python syntax highlighting for the Code tab (no external library, works offline)
const PY_KW = new Set('False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield'.split(' '));
const PY_BI = new Set('abs all any bool dict enumerate filter float getattr hasattr int isinstance len list map max min next print range repr reversed round set sorted str sum tuple type zip'.split(' '));
const PY_PW = new Set('pw plt np pd df data style fig ax axes'.split(' '));
function highlightPython(code) {
  const e = s => s.replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  const re = /(#[^\n]*)|((?:[rRbBuUfF]{1,2})?(?:"""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'))|(\b\d[\d_]*(?:\.\d*)?(?:[eE][+-]?\d+)?j?\b|\.\d+(?:[eE][+-]?\d+)?\b)|(@[A-Za-z_][\w.]*)|([A-Za-z_]\w*)/g;
  let out = '', last = 0, m, prev = '';
  while ((m = re.exec(code))) {
    out += e(code.slice(last, m.index)); last = re.lastIndex;
    const [t, com, str, num, deco, name] = m;
    if (com) out += `<span class="${/^#\s*(>>>|<<<)/.test(com) ? (/FAULT/.test(com) ? 'hl-bad' : 'hl-mark') : 'hl-c'}">${e(com)}</span>`;
    else if (str) out += `<span class="hl-s">${e(str)}</span>`;
    else if (num) out += `<span class="hl-n">${e(num)}</span>`;
    else if (deco) out += `<span class="hl-d">${e(deco)}</span>`;
    else {
      const next = code.slice(last).match(/^\s*\(/);
      const cls = PY_KW.has(name) ? 'hl-k' : (prev === 'def' || prev === 'class') ? 'hl-f'
        : PY_PW.has(name) ? 'hl-p' : PY_BI.has(name) && next ? 'hl-b' : next ? 'hl-call' : '';
      out += cls ? `<span class="${cls}">${e(name)}</span>` : e(name);
      prev = name; continue;
    }
    prev = '';
  }
  return out + e(code.slice(last));
}
