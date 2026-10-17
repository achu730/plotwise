const $ = s => document.querySelector(s);
let S = null;          // current session state
let SID = null;
const plain = s => readable(String(s ?? ''));  // LaTeX such as $\pi^{-}$ shown as π⁻ (texttools.js)
const cap = s => String(s ?? '').replace(/^./, c => c.toUpperCase());
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt = v => v == null ? '–' : (Math.abs(v) >= 1e4 || (Math.abs(v) < 1e-3 && v !== 0) ? (+v).toExponential(4) : (+(+v).toPrecision(6)).toString());

function toast(msg, ms = 3500) { const t = $('#toast'); t.textContent = plain(msg); t.classList.add('on'); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('on'), ms); }
function busy(on, text) { $('#busy').classList.toggle('on', on); if (text) $('#busyText').textContent = text; }

// a hosted Plotwise may have a password; it is kept in this browser and sent with each request
const passHeader = () => { const p = localStorageGet('plotwise_pass'); return p ? { 'X-Plotwise-Pass': p } : {}; };
function askPassword(wrong) {
  return new Promise(resolve => {
    const o = $('#passGate'); o.hidden = false; $('#passMsg').textContent = wrong ? 'That password was not right. Try again.' : '';
    $('#passInput').value = ''; $('#passInput').focus();
    $('#passForm').onsubmit = e => { e.preventDefault(); localStorageSet('plotwise_pass', $('#passInput').value); o.hidden = true; resolve(); };
  });
}
async function api(path, opts = {}, tries = 0) {
  const r = await fetch(path, { ...opts, headers: { ...(opts.headers || {}), ...passHeader() } });
  const j = await r.json().catch(() => ({ error: 'Server error' }));
  if (r.status === 401 && j.need_password && tries < 5) { await askPassword(tries > 0 || !!localStorageGet('plotwise_pass')); return api(path, opts, tries + 1); }
  if (j.sid) { S = j; render(); }
  if (!r.ok || j.error) throw new Error(j.error || 'Request failed');
  return j;
}
const post = (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });

async function init() {
  const st = await (await fetch('/api/status')).json();
  const b = $('#backend'); b.textContent = 'AI: ' + st.backend_name; b.classList.toggle('offline', st.backend === 'offline');
  SID = new URLSearchParams(location.search).get('s') || localStorageGet('plotwise_sid');
  if (SID) { try { await api('/api/s/' + SID); return; } catch (e) { SID = null; } }
  await newSession();
}
// constants found in the file: one value per labelled quantity, with the cell it came from
function constantsHtml(s) {
  const has = v => v != null && v !== '';
  return `<div style="margin-top:6px">${esc(cap(s.label || s.name))}:<ul class="consts">${s.constants.map(c =>
    `<li>${esc(c.Quantity)} = <b>${typeof c.Value === 'number' ? fmt(c.Value) : esc(c.Value)}</b>${has(c.Unit) ? ' ' + esc(c.Unit) : ''}` +
    `${has(c.Uncertainty) ? ' ± ' + (typeof c.Uncertainty === 'number' ? fmt(c.Uncertainty) : esc(c.Uncertainty)) : ''}` +
    `${has(c.Cell) ? ` <span class="muted">(cell ${esc(c.Cell)}${has(c['Uncertainty cell']) ? ', ± from ' + esc(c['Uncertainty cell']) : ''})</span>` : ''}</li>`).join('')}</ul></div>`;
}
function localStorageGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
function localStorageSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

async function newSession() { const j = await post('/api/session'); SID = j.sid; localStorageSet('plotwise_sid', SID); history.replaceState(null, '', '?s=' + SID); }

function render() {
  if (!S) return;
  SID = S.sid; localStorageSet('plotwise_sid', SID); rememberSession(SID);
  document.title = S.name ? `${S.name} · Plotwise` : 'Plotwise';
  if (!location.search.includes(SID)) history.replaceState(null, '', '?s=' + SID);
  // step 1
  const st = S.style;
  $('#styleInfo').innerHTML = st && st.guide_file
    ? `<div class="guide-ok"><b>${esc(st.guide_title || st.guide_file)}</b> uploaded.</div>` +
      (st.name ? `<div class="muted">${esc(st.name)}</div>` : '') +
      `<div style="margin-top:8px"><b>Figure rules in this guide</b></div><ul>${(st.rules || []).map(r => `<li>${esc(r)}</li>`).join('')}</ul>`
    : '<span class="muted">No style guide uploaded yet. Until you upload one, figures use a plain default style.</span>';
  renderStyleInputs();
  // step 3
  const d = S.data;
  $('#dataInfo').innerHTML = d ? `<b>${esc(d.file)}</b><div class="sha">SHA-256 ${esc(d.sha256)}</div>` +
    d.sheets.map(s => s.constants ? constantsHtml(s) :
      `<div style="margin-top:6px">${esc(cap(s.label || s.name))}: ${s.rows} rows<div class="chipcols">${s.columns.map(c => `<span title="${esc(c)}">${esc(plain(c))}</span>`).join('')}</div></div>`).join('') +
    (d.notes && d.notes.length ? `<div class="muted" style="margin-top:8px">Layout found: <ul>${d.notes.map(n => `<li>${esc(plain(n))}</li>`).join('')}</ul></div>` : '') +
    (problemsFromLog().length ? `<div class="banner warn" style="margin-top:8px">${problemsFromLog().length} cell(s) can't be plotted:<ul>${problemsFromLog().slice(0, 6).map(p => `<li>${esc(p)}</li>`).join('')}</ul></div>` : '')
    : '';
  // figure + versions
  const cur = S.current;
  $('#versions').innerHTML = S.versions.map(v => `<button data-v="${v.n}" class="${v.n === cur ? 'cur' : ''}" title="${esc(v.request)}" ${v.ok ? '' : 'disabled'}>
     <span class="dot ${v.status || (v.ok ? '' : 'fail')}"></span>v${v.n}${v.kind === 'fault' ? ' ⚠' : ''}</button>`).join('');
  if (cur) {
    $('#fig').src = `/s/${SID}/v/${cur}/figure.png?t=${Date.now()}`; $('#fig').style.display = 'block'; $('#empty').style.display = 'none';
  } else { $('#fig').style.display = 'none'; $('#empty').style.display = 'block'; }
  ['#openInspector', '#inject', '#spotPick', '#fmtBtn'].forEach(s => $(s).disabled = !cur);
  if (!cur) { $('#fmt').hidden = true; $('#fmtBtn').setAttribute('aria-expanded', 'false'); }
  else if (!$('#fmt').hidden && !$('#fmt').contains(document.activeElement)) fillFormat(+(S.panel && S.panel.axes) || 0);
  if (typeof closePop === 'function' && PTS.key !== SID + '/' + cur) closePop();
  document.querySelectorAll('#exports a[data-f]').forEach(a => { a.href = cur ? `/s/${SID}/v/${cur}/${a.dataset.f}?dl=1` : '#'; a.classList.toggle('disabled', !cur); });
  $('#logDl').href = `/s/${SID}/log.md`;
  $('#zipDl').href = cur ? `/s/${SID}/export.zip` : '#'; $('#zipDl').classList.toggle('disabled', !cur);
  renderThread(); renderChecks(); renderLog(); renderGitHub();
  $('#code').innerHTML = highlightPython(S.code || '');
  $('#codeLines').textContent = S.code ? S.code.replace(/\n$/, '').split('\n').map((_, i) => i + 1).join('\n') : '';
  $('#codeInfo').textContent = cur ? `Plotting code for v${cur}, the figure shown.` : 'No figure yet.';
  $('#codeDl').href = cur ? `/s/${SID}/v/${cur}/code.py?dl=1` : '#'; $('#codeDl').classList.toggle('disabled', !cur); $('#codeCopy').disabled = !cur;
  renderChips();
}

// ---- style inputs: typed preferences (section 1) and past code or figures (section 2) ----------------
const swatches = cols => `<div class="swatches">${(cols || []).map(c => `<span class="sw" title="${esc(c)}"><i style="background:${esc(c)}"></i>${esc(c)}</span>`).join('')}</div>`;
function renderStyleInputs() {
  $('#noteList').innerHTML = (S.style_notes || []).map((n, i) => `<li><button type="button" class="rm" data-note="${i}" title="Remove" aria-label="Remove">×</button>
    <q>${esc(n.text)}</q><div class="learned">${esc((n.learned || []).join(' · ') || 'applied')}<span class="small"> · ${esc(n.via || '')}</span></div></li>`).join('');
  $('#refList').innerHTML = (S.style_refs || []).map(r => `<li><button type="button" class="rm" data-ref="${esc(r.id)}" title="Remove" aria-label="Remove">×</button>
    <span class="kind">${r.kind === 'code' ? 'Plotting code' : 'Figure'}</span> <b>${esc(r.file)}</b>
    <div class="learned">${esc((r.learned || []).join(' · '))}<span class="small"> · ${esc(r.via || '')}</span></div>
    ${r.palette && r.palette.length ? swatches(r.palette) : ''}
    <label><input type="checkbox" data-use="${esc(r.id)}" ${r.use ? 'checked' : ''}> Use this style</label></li>`).join('');
  const st = S.style || {}, f = st.font || {}, fig = st.figure || {}, ax = st.axes || {};
  const unit = { brackets: '[unit]', parentheses: '(unit)', slash: '/ unit' }[(st.labels || {}).unit_style] || '[unit]';
  $('#styleNow').innerHTML = `<b>Figure style now</b> <span class="muted small">(${esc(S.style_source || 'plain default')})</span><dl>
    <dt>Font</dt><dd>${esc((f.names || [f.family])[0] || 'default')}, ${esc(f.size_pt)} pt</dd>
    <dt>Size</dt><dd>${esc(fig.width_mm)} mm wide, aspect ${esc(fig.aspect)}</dd>
    <dt>Units</dt><dd>${esc(unit)} · ticks ${esc(ax.ticks_direction || 'in')}${ax.ticks_all_sides ? ' on all sides' : ''}${ax.grid ? ' · grid' : ''}</dd>
    ${st.experiment_label ? `<dt>Label</dt><dd>${esc(st.experiment_label.text)}</dd>` : ''}
    <dt>Colours</dt><dd>${swatches(((st.data || {}).color ? [st.data.color] : []).concat((st.lines || {}).colors || []).filter((c, i, a) => a.indexOf(c) === i))}</dd></dl>`;
}
$('#noteForm').onsubmit = async e => {
  e.preventDefault();
  const text = $('#noteText').value.trim(); if (!text) return;
  busy(true, 'Applying your style…');
  try { await post(`/api/s/${SID}/style-note`, { text }); $('#noteText').value = ''; toast(S.current ? 'Style applied and the figure redrawn.' : 'Style applied.'); }
  catch (err) { toast(err.message, 7000); } finally { busy(false); }
};
$('#noteText').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#noteForm').requestSubmit(); } };
$('#noteList').onclick = async e => {
  const b = e.target.closest('[data-note]'); if (!b) return;
  busy(true, 'Updating the style…');
  try { await post(`/api/s/${SID}/style-note-remove`, { i: +b.dataset.note }); } catch (err) { toast(err.message); } finally { busy(false); }
};
$('#refList').onclick = async e => {
  const b = e.target.closest('[data-ref]'); if (!b) return;
  busy(true, 'Updating the style…');
  try { await post(`/api/s/${SID}/ref`, { id: b.dataset.ref, remove: true }); } catch (err) { toast(err.message); } finally { busy(false); }
};
$('#refList').onchange = async e => {
  const c = e.target.closest('[data-use]'); if (!c) return;
  busy(true, 'Updating the style…');
  try { await post(`/api/s/${SID}/ref`, { id: c.dataset.use, use: c.checked }); } catch (err) { toast(err.message); } finally { busy(false); }
};
async function uploadRefs(files) {
  if (!files.length) return;
  const fd = new FormData(); for (const f of files) fd.append('file', f);
  busy(true, 'Reading the style of your past plots…');
  try { const j = await api(`/api/s/${SID}/refs`, { method: 'POST', body: fd }); toast(j.note || 'Done.'); }
  catch (err) { toast(err.message, 7000); } finally { busy(false); $('#refFile').value = ''; }
}
$('#refFile').onchange = e => uploadRefs([...e.target.files]);
{
  const d = $('#refDrop');
  d.addEventListener('dragover', e => { e.preventDefault(); d.classList.add('over'); });
  d.addEventListener('dragleave', () => d.classList.remove('over'));
  d.addEventListener('drop', e => { e.preventDefault(); d.classList.remove('over'); uploadRefs([...e.dataTransfer.files]); });
}

// ---- GitHub deposit ------------------------------------------------------
const slug = t => String(t || '').normalize('NFKD').replace(/[^\w.\- ]+/g, '').trim().replace(/\s+/g, '-').slice(0, 60);
function renderGitHub() {
  const cur = S.current, v = S.versions.find(v => v.n === cur);
  $('#ghSend').disabled = !cur;
  $('#ghSend').textContent = cur ? `Save v${cur} to GitHub` : 'Save to GitHub';
  const name = slug(S.name || (S.data && S.data.file || '').replace(/\.[^.]+$/, '')) || SID;
  if (!$('#ghFolder').dataset.touched) $('#ghFolder').value = cur ? `figures/${name}/v${cur}` : `figures/${name}`;
  const done = (v && v.github) || [];
  $('#ghResult').innerHTML = done.map(g => `<div class="gh-ok">Saved v${g.version} to <a href="${esc(g.folder_url)}" target="_blank" rel="noopener">${esc(g.repo)}/${esc(g.folder || '')}</a> · commit <a href="${esc(g.commit_url)}" target="_blank" rel="noopener">${esc(g.commit.slice(0, 7))}</a></div>`).join('');
}
for (const id of ['ghRepo', 'ghBranch']) {
  $('#' + id).value = localStorageGet('plotwise_' + id) || '';
  $('#' + id).addEventListener('change', e => localStorageSet('plotwise_' + id, e.target.value.trim()));
}
$('#ghFolder').addEventListener('input', e => { e.target.dataset.touched = '1'; });
$('#ghToken').value = localStorageGet('plotwise_ghToken') || '';
$('#ghRemember').checked = !!$('#ghToken').value;
$('#ghRemember').onchange = e => { if (!e.target.checked) localStorageSet('plotwise_ghToken', ''); };
$('#ghSend').onclick = async () => {
  const token = $('#ghToken').value.trim();
  if ($('#ghRemember').checked) localStorageSet('plotwise_ghToken', token); else localStorageSet('plotwise_ghToken', '');
  busy(true, 'Saving to GitHub…');
  try {
    const j = await post(`/api/s/${SID}/github`, { token, repo: $('#ghRepo').value, branch: $('#ghBranch').value,
                                                 folder: $('#ghFolder').value, include_data: $('#ghData').checked });
    toast(`Saved to ${j.github.repo}, commit ${j.github.commit.slice(0, 7)}.`, 5000);
  } catch (err) { $('#ghResult').innerHTML = `<div class="banner fail" style="margin:0">${esc(err.message)}</div>`; }
  finally { busy(false); }
};

function problemsFromLog() {
  const e = [...S.log].reverse().find(e => e.step === 'data' && e.details && e.details.problems);
  const up = [...S.log].reverse().find(e => e.step === 'data' && e.actor === 'Human');
  return e && up && e.n > up.n ? e.details.problems : [];
}

function renderThread() {
  const th = $('#thread');
  th.innerHTML = S.versions.map(v => `<div class="msg me">${esc(v.request)}</div>` +
    (v.ok ? `<div class="msg ai">${esc(plain(v.changes || 'Done.'))} <div class="via">v${v.n} · ${v.via === 'offline' ? 'offline engine' : v.via === 'fault' ? 'fault injected by you' : v.via === 'manual' ? 'set by hand' : 'Claude'} · checks: ${esc(v.status || '')}</div></div>`
          : `<div class="msg ai err">That didn't run: ${esc(plain((v.error || '').split('\n').filter(Boolean).slice(-1)[0] || 'error'))}</div>`)).join('');
  th.scrollTop = th.scrollHeight;
}

function renderChips() {
  const ideas = !S.current ? (S.data ? suggestFirst() : []) :
    ['log y axis', 'legend bottom left', 'use cm', 'add Preliminary', 'x range 0 to 5', 'no error bars'];
  $('#chips').innerHTML = ideas.map(i => `<button type="button">${esc(i)}</button>`).join('');
}
function suggestFirst() {
  const f = (S.data.file || '').toLowerCase();
  if (f.includes('oscillator')) return ['y axis (y displacement) vs x axis (time)'];
  if (f.includes('lhcb')) return ['candidates vs invariant mass, data with error bars and the fit components as lines'];
  return ['<y column> vs <x column>'];
}

function renderChecks() {
  const r = S.integrity, el = $('#tab-checks');
  if (!r) { el.innerHTML = '<div class="muted">No figure yet. Checks run automatically on every version.</div>'; return; }
  const t = r.totals;
  const head = { pass: 'Every plotted point traces back to your data', warn: 'Plotted points trace back, with warnings', fail: 'The figure does not match your data' }[r.status];
  let h = `<div class="banner ${r.status}"><b>${r.status.toUpperCase()}</b> · ${head}<br>
    ${t.matched}/${t.points} points traced to a source row · ${t.unmatched} not in the data · ${t.missing} rows missing · ${t.empty_cells} empty cells</div>`;
  if (r.issues.length) h += `<ul class="issues">${r.issues.map(i => `<li class="${i.level}"><span class="lvl">${i.level === 'fail' ? '✗' : '!'}</span>${esc(i.message)}</li>`).join('')}</ul>`;
  h += '<h3>Where each series comes from</h3>' + r.series.map(s => {
    if (s.status === 'info') return `<div class="src"><b>${esc(plain(s.label))}</b> · ${esc(s.notes.join(' '))}</div>`;
    if (s.fit) { const f = s.fit;
      return `<div class="src"><span class="dot ${s.status}"></span> <b>${esc(f.name)}</b> · fitted to ${f.n_points} points of '${esc(plain(f.y_column))}' vs '${esc(plain(f.x_column))}' from ${esc(f.where || 'the file')}<br>
        ${esc(f.equation)}${f.chi2 != null ? ` · χ²/ndf = ${fmt(f.chi2)}/${f.ndf}` : f.r2 != null ? ` · R² = ${(+f.r2).toFixed(4)}` : ''}
        <br><span class="muted">${f.weighted ? 'Weighted by the error bars.' : 'Unweighted: every point counts the same.'} A line drawn over the data; the points are unchanged.</span></div>`; }
    const src = (k, a) => s[k] ? `${a} = '${esc(plain(s[k].column))}'${s[k].factor !== 1 ? ' × ' + fmt(s[k].factor) : ''}` : '';
    return `<div class="src"><span class="dot ${s.status}"></span> <b>${esc(plain(s.label))}</b> · ${s.n} points from ${esc(s.where || `sheet '${(s.y_source || {}).sheet}'`)}${s.group ? ' (' + esc(s.group) + ')' : ''}<br>
      ${[src('x_source', 'x'), src('y_source', 'y'), src('err_source', s.err_source_upper ? '− error' : '±'), src('err_source_upper', '+ error')].filter(Boolean).join(' · ')}
      ${s.notes.length ? '<br><span class="muted">' + esc(plain(s.notes.join(' '))) + '</span>' : ''}</div>`;
  }).join('');
  // the only changes the AI may make to data values: declared scaling and log axes, all listed here
  h += '<h3>Changes to the data</h3><p class="muted small" style="margin:0 0 6px">Only scaling by a factor (units or a constant from the file), log axes and fits drawn over the data are allowed.</p>' +
    ((r.data_changes || []).length
      ? '<ul class="checklist">' + r.data_changes.map(c => `<li><span class="no">!</span>${esc(c)}</li>`).join('') + '</ul>'
      : '<ul class="checklist"><li><span class="ok">✓</span>None. Every point is plotted exactly as it is in the file.</li></ul>');
  h += '<h3>Style guide checks</h3><ul class="checklist">' + (r.style_checks || []).map(c =>
    `<li><span class="${c.ok ? 'ok' : c.level === 'fail' ? 'bad' : 'no'}">${c.ok ? '✓' : '✗'}</span><span><b>${esc(c.name)}</b> · ${esc(c.detail)}</span></li>`).join('') + '</ul>';
  el.innerHTML = h;
}

function renderLog() {
  const el = $('#tab-log');
  el.innerHTML = S.log.map(e => {
    const d = e.details || {};
    const extra = [];
    if (d.diff) extra.push(`<details><summary>code diff</summary><pre>${esc(d.diff)}</pre></details>`);
    if (d.error) extra.push(`<details><summary>error</summary><pre>${esc(d.error)}</pre></details>`);
    if (d.prompt) extra.push(`<details><summary>prompt sent to Claude</summary><pre>${esc(d.prompt)}</pre></details>`);
    if (d.reply) extra.push(`<details><summary>Claude's reply</summary><pre>${esc(d.reply)}</pre></details>`);
    if (d.issues && d.issues.length) extra.push(`<details><summary>${d.issues.length} issue(s)</summary><pre>${esc(d.issues.map(i => i.level + ': ' + i.message).join('\n'))}</pre></details>`);
    if (d.answers) extra.push(`<details><summary>answers</summary><pre>${esc(JSON.stringify(d.answers, null, 1))}</pre></details>`);
    return `<div class="e"><span class="t">#${e.n} ${esc(e.time.slice(11, 19))}</span> <span class="${e.actor}">[${e.actor}]</span> <b>${esc(e.step)}</b> ${esc(e.actor === 'Human' ? e.summary : plain(e.summary))}${extra.join('')}</div>`;
  }).join('');
  el.scrollTop = el.scrollHeight;
}

// ---- spot check ---------------------------------------------------------
let SPOT = [];
async function pickSpot() {
  const j = await post(`/api/s/${SID}/spot`, { k: 3 });
  SPOT = j.points.map(p => ({ ...p, verdict: null, note: '' }));
  await api('/api/s/' + SID);
  drawSpot();
}
function drawSpot() {
  const el = $('#spotList');
  if (!SPOT.length) { el.innerHTML = ''; return; }
  el.innerHTML = SPOT.map((p, k) => {
    const src = p.source ? Object.entries(p.source.values).map(([c, v]) => `<tr><td>${esc(plain(c))}</td><td>${esc(typeof v === 'number' ? fmt(v) : v)}</td></tr>`).join('') : '<tr><td colspan=2 class="danger">No matching row in the data</td></tr>';
    const e = p.yerr ? ' ± ' + fmt((p.yerr[0] + p.yerr[1]) / 2) : '';
    return `<div class="spot"><b>${esc(plain(p.label))} · point ${p.point + 1}</b>${p.point === 1 ? ' <span class="muted">(the 2nd point)</span>' : ''}
      <a href="/s/${SID}/v/${S.current}/inspector.html?s=${p.series}&i=${p.point}" target="_blank" style="float:right;font-size:12px">show ↗</a>
      <table><tr><td>In the figure</td><td><b>x = ${fmt(p.x)}, y = ${fmt(p.y)}${e}</b></td></tr>
      ${p.y_factor && p.y_factor !== 1 ? `<tr><td>y unit factor</td><td>× ${fmt(p.y_factor)} from '${esc(plain(p.y_column))}'</td></tr>` : ''}
      <tr><td colspan=2 class="muted">${p.source ? `Source: row ${p.source.excel_row} of ${esc(p.source.where || `sheet '${p.source.sheet}'`)}` : ''}</td></tr>${src}</table>
      <div class="actions"><button data-k="${k}" data-v="ok" class="${p.verdict === 'ok' ? 'on ok' : ''}">✓ Matches my analysis</button>
      <button data-k="${k}" data-v="flag" class="${p.verdict === 'flag' ? 'on bad' : ''}">✗ Doesn't match</button></div>
      <input type="text" data-note="${k}" placeholder="Note (optional)" value="${esc(p.note)}" style="width:100%;margin-top:6px"></div>`;
  }).join('') + `<button id="spotSubmit" class="primary" style="margin-top:10px" ${SPOT.every(p => p.verdict) ? '' : 'disabled'}>Record my check</button>`;
}
$('#spotList').addEventListener('click', async e => {
  const b = e.target.closest('button'); if (!b) return;
  if (b.id === 'spotSubmit') {
    const answers = SPOT.map(p => ({ label: plain(p.label), series: p.series, point: p.point, x: p.x, y: p.y, source_row: p.source && p.source.excel_row, verdict: p.verdict, note: p.note }));
    const j = await post(`/api/s/${SID}/spot-answer`, { answers }); SPOT = []; drawSpot(); toast(j.note); showTab('log'); return;
  }
  SPOT[+b.dataset.k].verdict = b.dataset.v; drawSpot();
});
$('#spotList').addEventListener('input', e => { if (e.target.dataset.note != null) SPOT[+e.target.dataset.note].note = e.target.value; });

// ---- events -------------------------------------------------------------
function showTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.tabpane').forEach(p => p.classList.toggle('hidden', p.id !== 'tab-' + name));
}
document.querySelectorAll('.tab').forEach(t => t.onclick = () => showTab(t.dataset.tab));

$('#chatForm').onsubmit = async e => {
  e.preventDefault();
  const text = $('#chatInput').value.trim(); if (!text) return;
  if (!S.data) { toast('Upload data first (step 3).'); return; }
  $('#chatInput').value = '';
  busy(true, S.current ? 'Editing the figure…' : 'Writing the plot code…');
  try { await post(`/api/s/${SID}/${S.current ? 'edit' : 'plot'}`, { request: text }); }
  catch (err) { toast(err.message, 7000); }
  finally { busy(false); }
};
$('#chips').onclick = e => { const b = e.target.closest('button'); if (!b) return; $('#chatInput').value = b.textContent; $('#chatInput').focus(); };
$('#versions').onclick = async e => { const b = e.target.closest('button'); if (b) await post(`/api/s/${SID}/revert`, { version: +b.dataset.v }).catch(err => toast(err.message)); };
$('#openInspector').onclick = () => window.open(`/s/${SID}/v/${S.current}/inspector.html`, 'plotwise_inspector', 'width=1100,height=900');
$('#inject').onclick = async () => {
  busy(true, 'Injecting a fake AI mistake…');
  try { await post(`/api/s/${SID}/inject`); showTab('checks'); toast('A value was nudged by 5% and a row dropped inside the code. See what the checks say.', 6000); }
  catch (err) { toast(err.message); } finally { busy(false); }
};
$('#spotPick').onclick = () => pickSpot().catch(err => toast(err.message));
$('#codeCopy').onclick = async () => {
  try { await navigator.clipboard.writeText(S.code || ''); toast('Code copied.'); }
  catch (e) { const r = document.createRange(); r.selectNodeContents($('#code')); getSelection().removeAllRanges(); getSelection().addRange(r); toast('Press Ctrl+C to copy the selected code.'); }
};
$('#newSession').onclick = () => { SPOT = []; drawSpot(); newSession(); };

// saved sessions: every session lives in its own folder under sessions/, so any of them can be reopened
const when = iso => { try { return new Date(iso).toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }); } catch (e) { return iso; } };
const mySessions = () => (localStorageGet('plotwise_sids') || '').split(',').filter(Boolean);
function rememberSession(sid) {
  const l = mySessions().filter(x => x !== sid); l.unshift(sid); localStorageSet('plotwise_sids', l.slice(0, 200).join(','));
}
async function listSessions() {
  const j = await api('/api/sessions?ids=' + mySessions().join(','));
  $('#sessList').innerHTML = j.sessions.length ? j.sessions.map(x => {
    const detail = [x.name && x.data_file, x.guide, x.versions ? `${x.versions} figure version${x.versions > 1 ? 's' : ''}` : 'no figure yet',
                    x.status && x.status.toUpperCase()].filter(Boolean).join(' · ');
    return `<li><button type="button" data-sid="${esc(x.sid)}" class="${x.sid === SID ? 'cur' : ''}">
      <b>${esc(x.name || x.data_file || x.guide || 'Untitled session')}${x.sid === SID ? ' <span class="muted">(open now)</span>' : ''}</b>
      <span class="muted small">${esc(detail)}</span><span class="muted small">Last used ${esc(when(x.updated))}</span></button></li>`;
  }).join('') : '<li class="muted small">No saved sessions yet. A session is saved as soon as you upload a guide or data.</li>';
}
function closeSessions() { $('#sessPanel').hidden = true; $('#sessBtn').setAttribute('aria-expanded', 'false'); }
$('#sessBtn').onclick = async e => {
  e.stopPropagation();
  if (!$('#sessPanel').hidden) return closeSessions();
  $('#sessName').value = (S && S.name) || '';
  $('#sessPanel').hidden = false; $('#sessBtn').setAttribute('aria-expanded', 'true');
  await listSessions().catch(err => toast(err.message));
};
$('#sessPanel').onclick = e => e.stopPropagation();
document.addEventListener('click', closeSessions);
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeSessions(); });
$('#sessSave').onclick = async () => {
  try { await post(`/api/s/${SID}/rename`, { name: $('#sessName').value }); toast('Session name saved.'); await listSessions(); }
  catch (err) { toast(err.message); }
};
$('#sessName').onkeydown = e => { if (e.key === 'Enter') $('#sessSave').click(); };
$('#sessList').onclick = async e => {
  const b = e.target.closest('button[data-sid]'); if (!b) return;
  SPOT = []; drawSpot();
  try { await api('/api/s/' + b.dataset.sid); closeSessions(); toast('Opened ' + (S.name || S.data && S.data.file || 'the saved session') + '.'); }
  catch (err) { toast(err.message); }
};

async function upload(kind, file) {
  const fd = new FormData(); fd.append('file', file);
  busy(true, kind === 'style' ? 'Reading the style guide…' : 'Reading the data…');
  try { const j = await api(`/api/s/${SID}/${kind}`, { method: 'POST', body: fd }); toast(j.note || 'Uploaded ' + file.name); }
  catch (err) { toast(err.message, 6000); } finally { busy(false); }
}
for (const [kind, drop, input] of [['style', '#styleDrop', '#styleFile'], ['data', '#dataDrop', '#dataFile']]) {
  $(input).onchange = e => e.target.files[0] && upload(kind, e.target.files[0]);
  const d = $(drop);
  d.addEventListener('dragover', e => { e.preventDefault(); d.classList.add('over'); });
  d.addEventListener('dragleave', () => d.classList.remove('over'));
  d.addEventListener('drop', e => { e.preventDefault(); d.classList.remove('over'); e.dataTransfer.files[0] && upload(kind, e.dataTransfer.files[0]); });
}

// ---- format panel: change axes like Excel's "Format Axis", by hand, no AI ----------------------------
const LOCS = ['best', 'upper right', 'upper left', 'lower left', 'lower right', 'right', 'center left', 'center right', 'lower center', 'upper center', 'center'];
const LOCTEXT = { 'best': 'best place', 'upper right': 'top right', 'upper left': 'top left', 'lower left': 'bottom left', 'lower right': 'bottom right',
  'right': 'right', 'center left': 'middle left', 'center right': 'middle right', 'lower center': 'bottom centre', 'upper center': 'top centre', 'center': 'centre' };
function fillFormat(ai) {
  const f = $('#fmt'), A = (S.integrity && S.integrity.axes) || [], a = A[ai] || {}, P = S.panel || {};
  const same = (P.axes || 0) === ai;
  $('#fmtPanelWrap').hidden = A.length < 2;
  f.axes.innerHTML = A.map((_, k) => `<option value="${k}">Panel ${k + 1}</option>`).join(''); f.axes.value = ai;
  for (const k of ['x', 'y']) {
    const pk = (same && P[k]) || {}, lim = a[k + 'lim'] || [];
    f[`${k}.label`].value = a[k + 'label'] || '';
    f[`${k}.min`].value = pk.min ?? ''; f[`${k}.max`].value = pk.max ?? '';
    f[`${k}.min`].placeholder = 'auto, now ' + fmt(lim[0]); f[`${k}.max`].placeholder = 'auto, now ' + fmt(lim[1]);
    f[`${k}.step`].value = pk.step ?? '';
    f[`${k}.log`].checked = a[k + 'scale'] === 'log';
  }
  const now = a.has_legend ? 'now ' + (LOCTEXT[LOCS[a.legend_loc]] || 'shown') : 'now hidden';
  f.legend.options[0].textContent = `As the code sets it (${now})`;
  f.legend.value = same ? (P.legend || 'keep') : 'keep';
  f.grid.checked = !!a.grid;
  const series = ((S.integrity && S.integrity.series) || []).filter(s => (s.axes || 0) === ai && (s.kind === 'points'));
  f['fit.series'].innerHTML = (series.length > 1 ? '<option value="-1">All series</option>' : '') +
    (series.length ? series.map((s, k) => `<option value="${k}">${esc(s.label_text || plain(s.label) || 'Series ' + (k + 1))}</option>`).join('')
      : '<option value="0">(no data series)</option>');
  const pf = (same && P.fit) || { kind: 'none' };
  f['fit.kind'].value = pf.kind || 'none'; f['fit.degree'].value = pf.degree || 2;
  f['fit.series'].value = series.length > 1 ? (pf.series ?? -1) : 0; f['fit.eq'].checked = !!pf.equation;
  f['fit.lo'].value = (pf.range || [])[0] ?? ''; f['fit.hi'].value = (pf.range || [])[1] ?? '';
  syncFormat();
}
function syncFormat() {
  const f = $('#fmt');
  f.querySelector('[data-poly]').hidden = f['fit.kind'].value !== 'poly';
  for (const k of ['x', 'y']) {
    const v = f[`${k}.label`].value, r = readable(v);
    f.querySelector(`[data-preview="${k}"]`).textContent = r !== v ? 'Shows as: ' + r : '';
    f[`${k}.step`].disabled = f[`${k}.log`].checked;
  }
}
$('#fmt').addEventListener('input', syncFormat);
$('#fmt').axes.onchange = e => fillFormat(+e.target.value);
$('#fmtBtn').onclick = () => {
  const open = $('#fmt').hidden;
  $('#fmt').hidden = !open; $('#fmtBtn').setAttribute('aria-expanded', String(open));
  if (open) fillFormat(+(S.panel && S.panel.axes) || 0);
};
$('#fmtReset').onclick = () => fillFormat(+$('#fmt').axes.value || 0);
$('#fmt').onsubmit = async e => {
  e.preventDefault();
  const f = $('#fmt'), v = n => f[n].value.trim();
  const ax = k => ({ label: f[`${k}.label`].value, min: v(`${k}.min`), max: v(`${k}.max`), step: f[`${k}.log`].checked ? '' : v(`${k}.step`), log: f[`${k}.log`].checked });
  const settings = { axes: +f.axes.value || 0, x: ax('x'), y: ax('y'), legend: f.legend.value, grid: f.grid.checked,
    fit: { kind: f['fit.kind'].value, degree: +f['fit.degree'].value, series: +f['fit.series'].value, range: [v('fit.lo'), v('fit.hi')],
           equation: f['fit.eq'].checked } };
  busy(true, 'Applying your changes…');
  try { await post(`/api/s/${SID}/format`, { settings }); fillFormat(settings.axes); toast('Applied. Checks re-ran on the new version.'); }
  catch (err) { toast(err.message, 6000); } finally { busy(false); }
};

// ---- click a point to see its values ------------------------------------------------------------------
let PTS = { key: null, series: [] };
async function loadPoints() {
  const key = SID + '/' + S.current;
  if (PTS.key !== key) PTS = { key, series: await (await fetch(`/s/${SID}/v/${S.current}/points.json`)).json() };
  return PTS.series;
}
function closePop() { $('#pop').hidden = true; $('#ring').hidden = true; }
function placePop(img, px, py) {
  const sc = img.clientWidth / img.naturalWidth, wrap = $('#figwrap');
  const ir = img.getBoundingClientRect(), wr = wrap.getBoundingClientRect();
  const x = ir.left - wr.left + px * sc, y = ir.top - wr.top + py * sc;
  $('#ring').style.left = x + 'px'; $('#ring').style.top = y + 'px';
  const pop = $('#pop'); pop.hidden = false;
  const w = pop.offsetWidth, h = pop.offsetHeight;
  pop.style.left = Math.max(4, Math.min(x + 14, wrap.clientWidth - w - 4)) + 'px';
  pop.style.top = Math.max(4, y + 14 + h > wrap.clientHeight ? y - h - 14 : y + 14) + 'px';
}
const row = (k, v) => `<tr><td>${k}</td><td>${v}</td></tr>`;
$('#fig').addEventListener('click', async e => {
  if (!S.current) return;
  const img = e.currentTarget, sc = img.clientWidth / img.naturalWidth, nx = e.offsetX / sc, ny = e.offsetY / sc;
  const pts = await loadPoints().catch(() => []), rep = S.integrity || { series: [], axes: [] };
  let best = null;
  pts.forEach((s, si) => (s.px || []).forEach((p, i) => {
    if (!p || p[0] == null || p[1] == null) return;
    const pri = s.kind === 'points' || s.kind === 'bars' ? 0 : 1, d = Math.hypot(p[0] - nx, p[1] - ny) * sc;
    if (d <= (pri ? 7 : 14) && (!best || pri < best.pri || (pri === best.pri && d < best.d))) best = { si, i, d, pri, p };
  }));
  const pop = $('#pop');
  if (best) {
    const s = pts[best.si], r = rep.series[best.si] || {}, i = best.i, name = r.label_text || plain(s.label) || `Series ${best.si + 1}`;
    const e2 = s.yerr && s.yerr[i];
    const err = e2 ? (Math.abs((e2[0] || 0) - (e2[1] || 0)) < 1e-12 * Math.max(1, Math.abs(e2[0] || 0)) ? '± ' + fmt(e2[0]) : `−${fmt(e2[0])} / +${fmt(e2[1])}`) : null;
    let h = `<button type="button" class="x" aria-label="Close">×</button><b>${esc(name)}</b>`;
    if (r.fit) {
      h += ` <span class="muted">(fit line)</span><table>${row('x', fmt(s.x[i]))}${row('fit value y', fmt(s.y[i]))}</table><div class="muted small" style="margin-top:4px">${esc(r.fit.equation)}</div>`;
    } else {
      const src = r.excel_rows && r.excel_rows[i] != null ? `row ${r.excel_rows[i]} of ${esc(r.where || 'the file')}` : (s.kind === 'curve' ? '' : 'not found in the data');
      h += ` <span class="muted">· point ${i + 1}</span><table>${row('x', fmt(s.x[i]))}${row('y', fmt(s.y[i]))}${err ? row('error', err) : ''}
        ${r.x_source ? row('x from', `'${esc(plain(r.x_source.column))}'${r.x_source.factor !== 1 ? ' × ' + fmt(r.x_source.factor) : ''}`) : ''}
        ${r.y_source ? row('y from', `'${esc(plain(r.y_source.column))}'${r.y_source.factor !== 1 ? ' × ' + fmt(r.y_source.factor) : ''}`) : ''}
        ${src ? row('source', src) : ''}</table>` +
        (r.point_ok ? `<div class="small" style="margin-top:4px;color:var(--${r.point_ok[i] ? 'ok' : 'bad'})">${r.point_ok[i] ? '✓ matches your data' : '✗ does not match your data'}</div>` : '');
    }
    pop.innerHTML = h; $('#ring').hidden = false; placePop(img, best.p[0], best.p[1]);
    return;
  }
  // no point here: read the axes like a ruler
  const k = (rep.axes || []).findIndex(a => a.bbox_px && nx >= a.bbox_px[0] && nx <= a.bbox_px[2] && ny >= a.bbox_px[1] && ny <= a.bbox_px[3]);
  if (k < 0) { closePop(); return; }
  const a = rep.axes[k], [l, t, rr, b] = a.bbox_px;
  const val = (f, lim, scale) => scale === 'log' ? 10 ** (Math.log10(lim[0]) + f * (Math.log10(lim[1]) - Math.log10(lim[0]))) : lim[0] + f * (lim[1] - lim[0]);
  const x = val((nx - l) / (rr - l), a.xlim, a.xscale), y = val((b - ny) / (b - t), a.ylim, a.yscale);
  pop.innerHTML = `<button type="button" class="x" aria-label="Close">×</button><b>No data point here</b><table>${row('x ≈', fmt(x))}${row('y ≈', fmt(y))}</table><div class="muted small">Click closer to a point to see its values and source row.</div>`;
  $('#ring').hidden = true; placePop(img, nx, ny); $('#ring').hidden = true;
});
$('#pop').addEventListener('click', e => { if (e.target.closest('.x')) closePop(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closePop(); });

init().catch(err => toast(err.message));
