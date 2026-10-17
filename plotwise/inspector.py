"""Standalone HTML inspector: the real figure, with every plotted point hoverable and traced to its source row."""
import base64
import html
import json

from .texttools import readable


def inspector_html(png_path, series, report, state, version):
    img = base64.b64encode(open(png_path, "rb").read()).decode()
    pts = []
    for si, (s, r) in enumerate(zip(series, report["series"])):
        if (s["kind"] == "curve" and r["status"] == "info") or s["kind"] == "fit":
            continue  # fits are described in the issues list, not point by point
        for i in range(s["n"]):
            px = s["px"][i] if i < len(s["px"]) else None
            row = r["rows"][i] if i < len(r["rows"]) else None
            pts.append({"s": si, "label": html.escape(readable(r["label"]) or f"series {si + 1}"), "i": i, "curve": s["kind"] == "curve", "x": s["x"][i], "y": s["y"][i],
                        "err": (s["yerr"][i] if s.get("yerr") else None),
                        "px": px, "ok": r["point_ok"][i] if i < len(r["point_ok"]) else False,
                        "row": (r.get("excel_rows") or [None] * len(r["rows"]))[i] if row is not None else None,
                        "sheet": html.escape(str(r.get("where") or (r.get("y_source") or {}).get("sheet"))),
                        "xcol": (r.get("x_source") or {}).get("column"), "ycol": html.escape(readable((r.get("y_source") or {}).get("column")) or ""),
                        "xf": (r.get("x_source") or {}).get("factor"), "yf": (r.get("y_source") or {}).get("factor")})
    issues = "".join(f'<li class="{html.escape(i["level"])}">{html.escape(i["message"])}</li>' for i in report["issues"]) \
        or '<li class="pass">No problems found.</li>'
    t = report["totals"]
    meta = {"pts": pts}
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Plotwise Inspector v{version}</title>
<style>
:root{{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#6b6b66;--card:#fff;--line:#e2e1dc;--ok:#1a7f37;--bad:#c62828;--warn:#9a6700;--acc:#2f5bd3}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16171a;--fg:#e9e9e6;--muted:#a1a19b;--card:#202226;--line:#33353a;--ok:#4cc26b;--bad:#ff6b6b;--warn:#e3b341;--acc:#7aa2ff}}}}
body{{margin:0;font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--fg)}}
main{{max-width:1100px;margin:0 auto;padding:16px}}
h1{{font-size:18px;margin:0 0 4px}} .muted{{color:var(--muted)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin:12px 0}}
.fig{{position:relative;display:inline-block;max-width:100%;background:#fff;border-radius:6px}}
.fig img{{display:block;max-width:100%;height:auto}}
.fig svg{{position:absolute;inset:0;width:100%;height:100%}}
circle.hit{{fill:transparent;stroke:transparent;stroke-width:2;cursor:pointer}}
circle.hit:hover,circle.hit.sel{{stroke:var(--acc);fill:rgba(47,91,211,.15)}}
circle.hit.bad{{stroke:#c62828;stroke-width:2.5}}
#tip{{position:fixed;pointer-events:none;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 10px;
box-shadow:0 4px 16px rgba(0,0,0,.15);font-size:13px;display:none;z-index:5;max-width:320px}}
table{{border-collapse:collapse;width:100%;font-size:13px}} th,td{{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line)}}
tr{{cursor:pointer}} tr:hover,tr.sel{{background:rgba(47,91,211,.08)}}
.badge{{display:inline-block;padding:1px 8px;border-radius:99px;font-weight:600;font-size:12px}}
.pass{{color:var(--ok)}} .fail{{color:var(--bad)}} .warn{{color:var(--warn)}}
ul{{margin:6px 0;padding-left:18px}} .tablewrap{{overflow-x:auto}}
</style></head><body><main>
<h1>Plotwise Inspector · figure v{version}</h1>
<div class="muted">Data: {html.escape(str(state.get('data_file')))} · SHA-256 {html.escape(str(state.get('data_sha256'))[:16])}… · Style: {html.escape(str(state.get('style_source')))}</div>
<div class="card"><b>Integrity: <span class="{report['status']}">{report['status'].upper()}</span></b> ·
{t['matched']}/{t['points']} plotted points traced to a source row · {t['unmatched']} not in the data · {t['missing']} rows missing · {t['empty_cells']} empty cells
<ul>{issues}</ul></div>
<div class="card"><div class="muted" style="margin-bottom:6px">Hover or click a point. Red rings mark points that don't match the data.</div>
<div class="fig"><img id="im" src="data:image/png;base64,{img}" alt="figure v{version}"><svg id="ov"></svg></div></div>
<div class="card tablewrap"><table><thead><tr><th>Series</th><th>#</th><th>x (plotted)</th><th>y (plotted)</th><th>± y</th><th>Source</th><th>Check</th></tr></thead><tbody id="tb"></tbody></table></div>
<div id="tip"></div>
<script>
const M={json.dumps(meta)};
const im=document.getElementById('im'),ov=document.getElementById('ov'),tip=document.getElementById('tip'),tb=document.getElementById('tb');
const f=v=>v==null?'–':(Math.abs(v)>=1e4||(Math.abs(v)<1e-3&&v!==0)?v.toExponential(4):(+v.toPrecision(6)).toString());
const errs=e=>e?(Math.abs(e[0]-e[1])<1e-12*Math.max(1,Math.abs(e[0]))?'± '+f(e[0]):'−'+f(e[0])+' / +'+f(e[1])):'–';
function src(p){{return p.row?`row ${{p.row}} of ${{p.sheet}}`:'not found in data'}}
function build(){{
 ov.setAttribute('viewBox',`0 0 ${{im.naturalWidth}} ${{im.naturalHeight}}`);ov.innerHTML='';
 const order=M.pts.map((p,k)=>k).sort((a,b)=>(M.pts[b].curve?1:0)-(M.pts[a].curve?1:0)); // data points on top
 order.forEach(k=>{{ const p=M.pts[k]; if(!p.px||p.px[0]==null)return;
  const c=document.createElementNS('http://www.w3.org/2000/svg','circle');
  c.setAttribute('cx',p.px[0]);c.setAttribute('cy',p.px[1]);c.setAttribute('r',p.curve?3:7);
  c.setAttribute('class','hit'+(p.ok?'':' bad'));c.dataset.k=k;ov.appendChild(c);
  c.addEventListener('mousemove',e=>show(e,p));c.addEventListener('mouseleave',()=>tip.style.display='none');
  c.addEventListener('click',()=>select(k,true));}});
}}
function show(e,p){{tip.innerHTML=`<b>${{p.label}}</b> · point ${{p.i+1}}<br>x = ${{f(p.x)}}<br>y = ${{f(p.y)}} ${{p.err?'('+errs(p.err)+')':''}}<br>
 <span class="muted">${{src(p)}}${{p.ycol?' · '+p.ycol+(p.yf&&p.yf!=1?' × '+p.yf:''):''}}</span><br>
 <b class="${{p.ok?'pass':'fail'}}">${{p.ok?'matches the data':'does NOT match the data'}}</b>`;
 tip.style.display='block';tip.style.left=Math.min(e.clientX+14,innerWidth-330)+'px';tip.style.top=(e.clientY+14)+'px';}}
function select(k,scroll){{document.querySelectorAll('.sel').forEach(n=>n.classList.remove('sel'));
 const c=ov.querySelector(`[data-k="${{k}}"]`);if(c)c.classList.add('sel');const r=tb.children[k];r.classList.add('sel');if(scroll)r.scrollIntoView({{block:'nearest'}});}}
M.pts.forEach((p,k)=>{{const tr=document.createElement('tr');
 tr.innerHTML=`<td>${{p.label}}</td><td>${{p.i+1}}</td><td>${{f(p.x)}}</td><td>${{f(p.y)}}</td><td>${{errs(p.err)}}</td><td>${{src(p)}}</td><td class="${{p.ok?'pass':'fail'}}">${{p.ok?'✓':'✗'}}</td>`;
 tr.onclick=()=>select(k,false);tb.appendChild(tr);}});
if(im.complete)build();else im.onload=build;
const q=new URLSearchParams(location.search);if(q.has('s')&&q.has('i')){{const k=M.pts.findIndex(p=>p.s==q.get('s')&&p.i==q.get('i'));if(k>=0)setTimeout(()=>select(k,true),50);}}
</script></main></body></html>"""
