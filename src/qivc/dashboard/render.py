"""
Dashboard HTML renderer (Task 15) — matches the approved prototype design.

Reproduces the prototype's warm-dark "instrument" CSS/SVG verbatim and injects the
live `PAPER_DATA` (built in build.py) in place of the prototype's sample block. The
headline is driven by Python-precomputed values (single source of truth, tested);
charts/table/regime are drawn client-side from the live arrays. Default headline
mode is B (realized-on-close — the honest default). Empty/near-empty ledgers render
a clear "accumulating forward data" state, never a crash.
"""

from __future__ import annotations

import json
from typing import Any

_CSS = """
:root{
  --bg:#15120E;--bg2:#1B1712;--surface:#1F1A14;--surface2:#241E17;--line:#352C21;
  --line2:#473A2B;--ink:#ECE3D4;--ink-dim:#A2937C;--ink-faint:#6F6353;--amber:#D2A53A;
  --amber-dim:#8C7126;--pos:#8FA85C;--pos-dim:#5C6E39;--neg:#C9694A;--neg-dim:#7E4530;
  --mono:'IBM Plex Mono',monospace;--serif:'Newsreader',Georgia,serif;
}
*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-font-smoothing:antialiased}
body{background:
  radial-gradient(1200px 600px at 80% -10%, rgba(210,165,58,0.05), transparent 60%),
  radial-gradient(900px 500px at -5% 110%, rgba(143,168,92,0.04), transparent 55%),var(--bg);
  color:var(--ink);font-family:var(--mono);font-size:13px;line-height:1.5;padding:0 0 64px;min-height:100vh;}
.wrap{max-width:1180px;margin:0 auto;padding:0 28px}
.banner{background:repeating-linear-gradient(45deg,rgba(210,165,58,0.10) 0 14px,rgba(210,165,58,0.04) 14px 28px);
  border-bottom:1px solid var(--amber-dim);color:var(--amber);font-size:11px;letter-spacing:0.22em;
  text-transform:uppercase;text-align:center;padding:9px 16px;font-weight:500;}
.banner b{color:var(--ink);font-weight:600}
header{padding:34px 0 22px;border-bottom:1px solid var(--line)}
.eyebrow{font-size:10.5px;letter-spacing:0.34em;text-transform:uppercase;color:var(--ink-faint)}
.title{font-family:var(--serif);font-weight:400;font-size:42px;line-height:1.02;letter-spacing:-0.01em;margin:8px 0 2px}
.title em{font-style:italic;color:var(--amber)}
.sub{color:var(--ink-dim);font-size:12px;margin-top:10px;display:flex;flex-wrap:wrap;gap:6px 22px}
.sub span b{color:var(--ink);font-weight:500}
.weights{display:inline-flex;gap:1px;margin-left:2px;vertical-align:-2px}
.wseg{height:9px;display:inline-block}
.wlabel{color:var(--ink-faint);font-size:10.5px;letter-spacing:0.04em}
.section{margin-top:34px}
.sec-head{display:flex;align-items:baseline;justify-content:space-between;border-bottom:1px solid var(--line);padding-bottom:7px;margin-bottom:16px}
.sec-title{font-family:var(--serif);font-size:19px;font-weight:500}
.sec-note{color:var(--ink-faint);font-size:10.5px;letter-spacing:0.16em;text-transform:uppercase}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);border:1px solid var(--line)}
.metric{background:var(--surface);padding:18px 18px 16px;position:relative}
.metric .k{font-size:10px;letter-spacing:0.18em;text-transform:uppercase;color:var(--ink-faint);display:flex;align-items:center;gap:7px}
.metric .v{font-size:30px;font-weight:400;margin-top:9px;letter-spacing:-0.02em;font-variant-numeric:tabular-nums}
.metric .vsub{font-size:11px;color:var(--ink-dim);margin-top:3px}
.pos{color:var(--pos)} .neg{color:var(--neg)} .amb{color:var(--amber)}
.chip{font-size:9px;letter-spacing:0.12em;text-transform:uppercase;padding:2px 7px;border-radius:2px;border:1px solid;font-weight:500}
.chip.void{color:var(--neg);border-color:var(--neg-dim);background:rgba(201,105,74,0.08)}
.chip.thin{color:var(--amber);border-color:var(--amber-dim);background:rgba(210,165,58,0.07)}
.chip.ok{color:var(--pos);border-color:var(--pos-dim);background:rgba(143,168,92,0.07)}
.metric .why{font-size:10.5px;color:var(--ink-faint);margin-top:10px;line-height:1.45;border-top:1px dashed var(--line2);padding-top:9px}
.toggle{display:inline-flex;border:1px solid var(--line2);border-radius:3px;overflow:hidden}
.toggle button{background:transparent;color:var(--ink-dim);border:none;font-family:var(--mono);font-size:10.5px;letter-spacing:0.06em;padding:5px 11px;cursor:pointer;transition:all .15s}
.toggle button.on{background:var(--amber);color:#1a1407;font-weight:600}
.toggle button:not(.on):hover{color:var(--ink);background:var(--surface2)}
.grid2{display:grid;grid-template-columns:1.35fr 1fr;gap:1px;background:var(--line);border:1px solid var(--line)}
.card{background:var(--surface);padding:18px 20px 16px}
.card h4{font-family:var(--serif);font-size:15px;font-weight:500;margin-bottom:3px}
.card .cnote{color:var(--ink-faint);font-size:10.5px;margin-bottom:14px}
svg{display:block;width:100%;overflow:visible}
.grid-l{stroke:var(--line);stroke-width:1}
.tlab{fill:var(--ink-faint);font-family:var(--mono);font-size:9.5px}
.legend{display:flex;gap:16px;margin-top:12px;font-size:10.5px;color:var(--ink-dim)}
.legend i{display:inline-block;width:14px;height:3px;vertical-align:3px;margin-right:5px;border-radius:2px}
.empty{color:var(--ink-faint);font-size:11.5px;text-align:center;padding:48px 16px;line-height:1.6}
.tbl-wrap{border:1px solid var(--line);overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:11.5px;min-width:920px}
thead th{background:var(--bg2);color:var(--ink-faint);font-weight:500;font-size:9.5px;letter-spacing:0.13em;text-transform:uppercase;text-align:right;padding:10px 12px;border-bottom:1px solid var(--line2);white-space:nowrap}
thead th:first-child,tbody td:first-child{text-align:left}
thead th.l{text-align:left}
tbody td{padding:11px 12px;text-align:right;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums;white-space:nowrap}
tbody tr:hover{background:var(--surface2)}
.tk{font-weight:600;color:var(--ink);letter-spacing:0.02em}
.muted{color:var(--ink-faint)}
.status-pill{font-size:9px;letter-spacing:0.1em;text-transform:uppercase;padding:2px 6px;border-radius:2px;font-weight:500}
.s-open{color:var(--amber);background:rgba(210,165,58,0.1)}
.s-closed{color:var(--ink-dim);background:rgba(162,147,124,0.1)}
.s-delist{color:var(--neg);background:rgba(201,105,74,0.12)}
.facs{display:inline-flex;gap:2px;align-items:flex-end;height:18px}
.fac{width:5px;background:var(--line2);position:relative;border-radius:1px 1px 0 0}
.fac.zero{opacity:0.28}
.hold-bar{display:inline-block;width:54px;height:6px;background:var(--line);border-radius:3px;overflow:hidden;vertical-align:1px;margin-right:7px}
.hold-fill{display:block;height:100%;background:var(--amber)}
.factor-key{display:flex;gap:14px;margin-top:12px;font-size:10px;color:var(--ink-faint);flex-wrap:wrap}
.factor-key span i{display:inline-block;width:8px;height:8px;border-radius:1px;vertical-align:0;margin-right:5px}
.regime{display:flex;gap:1px;border:1px solid var(--line);background:var(--line)}
.rcell{flex:1;background:var(--surface);padding:12px 14px;text-align:center}
.rcell .rm{font-size:10px;letter-spacing:0.14em;text-transform:uppercase;color:var(--ink-faint)}
.rcell .rs{font-size:12px;margin-top:5px;font-weight:500}
.r-on{color:var(--pos)} .r-mid{color:var(--amber)} .r-off{color:var(--neg)}
.rcell .rdot{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:6px;vertical-align:1px}
footer{margin-top:40px;border-top:1px solid var(--line);padding-top:16px;color:var(--ink-faint);font-size:10.5px;line-height:1.6}
footer b{color:var(--ink-dim)}
.callout{background:var(--surface);border:1px solid var(--line2);border-left:2px solid var(--amber);padding:14px 18px;margin-top:18px;font-size:11.5px;color:var(--ink-dim);line-height:1.6}
.callout b{color:var(--ink)}
"""

_BODY = """
<div class="banner">
  ● Paper ledger — <b>not validated · not deployed · no real capital</b> — forward out-of-sample collection only
</div>
<div class="wrap">
  <header>
    <div class="eyebrow">QIVC · Quantitative Insider-Value Composite</div>
    <h1 class="title">Paper <em>Ledger</em></h1>
    <div class="sub">
      <span>strategy <b>v3.0 baseline</b></span>
      <span>config <b id="h-config">—</b></span>
      <span class="wlabel">weights <span class="weights" id="wbar"></span> <b id="wtxt">40/30/20/10/0</b></span>
      <span>started <b id="h-started">—</b></span>
      <span>as of <b id="h-asof">—</b></span>
      <span class="wlabel">auto-refresh
        <span class="toggle" id="refreshToggle" style="margin-left:4px;vertical-align:-3px">
          <button data-ms="0" class="on">off</button>
          <button data-ms="300000">5m</button>
          <button data-ms="900000">15m</button>
        </span>
        <span class="muted" id="lastUpdated" style="margin-left:8px"></span>
      </span>
    </div>
  </header>

  <div class="section">
    <div class="sec-head">
      <div class="sec-title">Standing</div>
      <div class="toggle" id="modeToggle">
        <button data-mode="A">A · live mark</button>
        <button data-mode="B" class="on">B · realized only</button>
      </div>
    </div>
    <div class="metrics">
      <div class="metric">
        <div class="k">cumulative return</div>
        <div class="v" id="m-ret">—</div>
        <div class="vsub" id="m-ret-sub"></div>
        <div class="why">The number you'll be tempted to fixate on. It means nothing on its own — read the three panels to its right before believing it.</div>
      </div>
      <div class="metric">
        <div class="k">sample size <span class="chip void" id="m-n-chip">void</span></div>
        <div class="v" id="m-n">0</div>
        <div class="vsub">closed trades</div>
        <div class="why">Below ~30 closed trades, any return figure is statistical noise. Treat everything as provisional.</div>
      </div>
      <div class="metric">
        <div class="k">vs benchmark (IWN)</div>
        <div class="v amb" id="m-bench">—</div>
        <div class="vsub">paper − Russell 2000 Value</div>
        <div class="why">"Up X%" is meaningless if the index did X% too. Edge is the gap over the benchmark, not the raw return.</div>
      </div>
      <div class="metric">
        <div class="k">top-3 concentration</div>
        <div class="v" id="m-conc">—</div>
        <div class="vsub">of positive P&amp;L from 3 names</div>
        <div class="why">This metric is what exposed the +74% backtest as luck. If a few names carry everything, you don't have a strategy — you have a lottery.</div>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="grid2">
      <div class="card">
        <h4>Equity curve</h4>
        <div class="cnote">Paper portfolio vs IWN (small-cap value) vs cash. Forward only — from the ledger's first entry.</div>
        <svg id="equity" viewBox="0 0 560 240" aria-label="equity curve"></svg>
        <div class="legend">
          <span><i style="background:var(--amber)"></i>paper</span>
          <span><i style="background:var(--ink-dim)"></i>IWN</span>
          <span><i style="background:var(--pos-dim)"></i>cash (T-bill)</span>
        </div>
      </div>
      <div class="card">
        <h4>Score → return</h4>
        <div class="cnote">The decisive diagnostic. As trades close, dots accumulate. A slope = the score predicts. A cloud = no edge.</div>
        <svg id="scatter" viewBox="0 0 360 240" aria-label="score vs return"></svg>
        <div class="callout" id="scatter-note" style="margin-top:14px;padding:11px 14px"></div>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="sec-head">
      <div class="sec-title">Positions</div>
      <div class="sec-note">factor sub-scores · both marking modes</div>
    </div>
    <div class="tbl-wrap">
      <table>
        <thead><tr>
          <th class="l">ticker</th><th class="l">status</th><th class="l">factors (I·Q·V·M·T)</th>
          <th>score</th><th>entry</th><th>entry px</th><th>mark / exit</th><th>hold (B)</th><th>return</th>
        </tr></thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
    <div class="factor-key">
      <span><i style="background:#C98A3A"></i>insider 40%</span>
      <span><i style="background:#8FA85C"></i>quality 30%</span>
      <span><i style="background:#6E8FA0"></i>valuation 20%</span>
      <span><i style="background:#B07AA0"></i>momentum 10%</span>
      <span><i style="background:#473A2B"></i>technical 0% (shelved — regime-conditional)</span>
    </div>
  </div>

  <div class="section">
    <div class="sec-head">
      <div class="sec-title">Regime context</div>
      <div class="sec-note">FRED-derived · scales sizing only</div>
    </div>
    <div class="regime" id="regime"></div>
    <div class="callout">
      Why this is here: 2025 looked great partly because it was a <b>mean-reversion year</b> — a regime that flatters this kind of strategy. Always read returns next to the regime that produced them, so a tailwind never gets mistaken for skill.
    </div>
  </div>

  <footer>
    <b>Live forward ledger.</b> This dashboard reads the real <b>qivc paper</b> JSONL ledger and marks open positions with current yfinance prices. It begins near-empty and fills only with real forward data — no backtest, PIT, or synthetic points are ever shown.<br>
    Mode <b>A</b> values open positions at the current price (live, twitchy). Mode <b>B</b> (default) books return only when a position closes per the hold rule — the strategy is judged on its decisions, not daily noise.<br>
    No broker connection. No real orders. Ever — until forward evidence justifies it, which it does not yet.
  </footer>
</div>
"""

_SCRIPT = r"""
const FAC_COLORS=['#C98A3A','#8FA85C','#6E8FA0','#B07AA0','#7A6648'];
const fmt=v=>(v>=0?'+':'')+Number(v).toFixed(1)+'%';
const M=PAPER_DATA.meta||{}, H=PAPER_DATA.headline||{};

/* auto-refresh control — default OFF (this is a months-long test, not a ticker).
   Choice persists in localStorage; on interval the page reloads, and in --serve
   mode the server re-reads the ledger + re-marks prices on that load. */
(()=>{
  const KEY='qivc_refresh_ms';
  const upd=document.getElementById('lastUpdated');
  if(upd) upd.textContent='updated '+new Date().toLocaleTimeString();
  const btns=document.querySelectorAll('#refreshToggle button');
  let ms=parseInt(localStorage.getItem(KEY)||'0',10);
  let timer=null;
  const schedule=()=>{ if(timer) clearTimeout(timer); if(ms>0) timer=setTimeout(()=>location.reload(),ms); };
  btns.forEach(b=>{
    b.classList.toggle('on', parseInt(b.dataset.ms,10)===ms);
    b.addEventListener('click',()=>{ ms=parseInt(b.dataset.ms,10); localStorage.setItem(KEY,ms);
      btns.forEach(x=>x.classList.remove('on')); b.classList.add('on'); schedule(); });
  });
  schedule();
})();

/* header fill */
document.getElementById('h-config').textContent=M.config||'—';
document.getElementById('h-started').textContent=M.started||'(no entries yet)';
document.getElementById('h-asof').textContent=M.asOf||'—';
document.getElementById('wtxt').textContent=(M.weights||[]).join('/');
(()=>{const w=M.weights||[],tot=w.reduce((a,b)=>a+b,0)||1,el=document.getElementById('wbar');
  w.forEach((seg,i)=>{const d=document.createElement('span');d.className='wseg';
    d.style.width=Math.max(seg/tot*120,2)+'px';d.style.background=seg===0?'var(--line2)':FAC_COLORS[i];
    d.style.opacity=seg===0?0.4:1;el.appendChild(d);});})();

/* headline — precomputed (Python is source of truth), mode-aware */
function renderHeadline(mode){
  const h=H[mode]||{ret:0,bench:0,sub:''};
  const rEl=document.getElementById('m-ret');
  rEl.textContent=fmt(h.ret); rEl.className='v '+(h.ret>=0?'pos':'neg');
  document.getElementById('m-ret-sub').textContent=h.sub;
  document.getElementById('m-n').textContent=H.n;
  const chip=document.getElementById('m-n-chip'); chip.textContent=H.nLabel; chip.className='chip '+H.nChip;
  const bEl=document.getElementById('m-bench'); bEl.textContent=fmt(h.bench);
  bEl.className='v '+(Math.abs(h.bench)<1.0?'amb':(h.bench>0?'pos':'neg'));
  document.getElementById('m-conc').textContent=(H.n? Math.round(H.conc)+'%':'—');
}
renderHeadline('B');
document.querySelectorAll('#modeToggle button').forEach(b=>{
  b.addEventListener('click',()=>{document.querySelectorAll('#modeToggle button').forEach(x=>x.classList.remove('on'));
    b.classList.add('on');renderHeadline(b.dataset.mode);});});

/* positions table */
(()=>{
  const tb=document.getElementById('rows');
  const w=M.weights||[40,30,20,10,0];
  const facBars=f=>'<span class="facs">'+(f||[]).map((v,i)=>
    `<span class="fac ${w[i]===0?'zero':''}" style="height:${4+v*14}px;background:${FAC_COLORS[i]}"></span>`).join('')+'</span>';
  if(!PAPER_DATA.closed.length && !PAPER_DATA.open.length){
    tb.innerHTML='<tr><td colspan="9" class="empty">No positions yet — the ledger is accumulating forward data. Run <b>qivc paper</b> to record signals.</td></tr>';return;}
  PAPER_DATA.closed.forEach(t=>{
    const del=t.delisted?`<span class="status-pill s-delist">delisted·${t.delisted}</span> `:'';
    tb.insertAdjacentHTML('beforeend',`<tr>
      <td class="tk">${t.ticker}</td>
      <td class="l">${del}<span class="status-pill s-closed">closed</span></td>
      <td class="l">${facBars(t.f)}</td>
      <td>${t.score.toFixed(2)}</td><td class="muted">${t.entry}</td>
      <td>${t.entryPx??'—'}</td><td>${t.exitPx??'—'}</td>
      <td class="muted">${t.hold}d ✓</td>
      <td class="${t.ret>=0?'pos':'neg'}">${fmt(t.ret)}</td></tr>`);});
  PAPER_DATA.open.forEach(o=>{
    const pct=Math.min(Math.round(o.holdDay/o.holdTgt*100),100);
    const del=o.delisted?`<span class="status-pill s-delist">delisted·${o.delisted}</span> `:'';
    tb.insertAdjacentHTML('beforeend',`<tr>
      <td class="tk">${o.ticker}</td>
      <td class="l">${del}<span class="status-pill s-open">open</span></td>
      <td class="l">${facBars(o.f)}</td>
      <td>${o.score.toFixed(2)}</td><td class="muted">${o.entry}</td>
      <td>${o.entryPx??'—'}</td><td>${o.mark??'—'}</td>
      <td><span class="hold-bar"><span class="hold-fill" style="width:${pct}%"></span></span><span class="muted">${o.holdDay}/${o.holdTgt}</span></td>
      <td class="${o.ret>=0?'pos':'neg'}">${fmt(o.ret)}<span class="muted" style="font-size:9px"> ·unrl</span></td></tr>`);});
})();

/* equity curve */
(()=>{
  const d=PAPER_DATA.equity, svg=document.getElementById('equity');
  if(d.length<2){svg.innerHTML='<text class="tlab" x="280" y="120" text-anchor="middle">accumulating forward data — curve begins at the first ledger entry</text>';return;}
  const W=560,H2=240,P={t:16,r:16,b:28,l:34},xs=W-P.l-P.r,ys=H2-P.t-P.b;
  const all=d.flatMap(p=>[p.paper,p.iwn,p.cash]);
  let lo=Math.min(...all),hi=Math.max(...all);lo=Math.min(lo,0);const pad=(hi-lo)*0.15||1;lo-=pad;hi+=pad;
  const X=i=>P.l+i/(d.length-1)*xs, Y=v=>P.t+(1-(v-lo)/(hi-lo))*ys;
  let h='';
  [hi,(hi+lo)/2,lo].forEach(g=>{h+=`<line class="grid-l" x1="${P.l}" y1="${Y(g)}" x2="${W-P.r}" y2="${Y(g)}"/>`;
    h+=`<text class="tlab" x="${P.l-6}" y="${Y(g)+3}" text-anchor="end">${g>=0?'+':''}${g.toFixed(1)}</text>`;});
  h+=`<line class="grid-l" x1="${P.l}" y1="${Y(0)}" x2="${W-P.r}" y2="${Y(0)}" stroke="var(--line2)"/>`;
  const path=(key,col,wdt,dash)=>{let p=d.map((pt,i)=>`${i?'L':'M'}${X(i).toFixed(1)} ${Y(pt[key]).toFixed(1)}`).join(' ');
    h+=`<path d="${p}" fill="none" stroke="${col}" stroke-width="${wdt}" ${dash?`stroke-dasharray="${dash}"`:''} stroke-linejoin="round"/>`;};
  let area=d.map((pt,i)=>`${i?'L':'M'}${X(i).toFixed(1)} ${Y(pt.paper).toFixed(1)}`).join(' ');
  area+=`L${X(d.length-1)} ${Y(0)} L${X(0)} ${Y(0)} Z`;
  h+=`<path d="${area}" fill="rgba(210,165,58,0.08)"/>`;
  path('cash','var(--pos-dim)',1.5,'3 3');path('iwn','var(--ink-dim)',1.5);path('paper','var(--amber)',2.2);
  d.forEach((pt,i)=>{if(i%2===0||i===d.length-1)h+=`<text class="tlab" x="${X(i)}" y="${H2-10}" text-anchor="middle">${pt.date}</text>`;});
  h+=`<circle cx="${X(d.length-1)}" cy="${Y(d[d.length-1].paper)}" r="3.2" fill="var(--amber)"/>`;
  svg.innerHTML=h;
})();

/* scatter: score vs return (closed only) */
(()=>{
  const pts=PAPER_DATA.closed.map(t=>({x:t.score,y:t.ret})), svg=document.getElementById('scatter');
  const note=document.getElementById('scatter-note'), n=pts.length;
  if(n<1){svg.innerHTML='<text class="tlab" x="180" y="120" text-anchor="middle">no closed trades yet</text>';
    note.innerHTML='<b>n = 0.</b> No closed trades yet — the scatter fills as positions close. Needs ~30+ to read a slope.';return;}
  const W=360,H2=240,P={t:16,r:14,b:30,l:36},xs=W-P.l-P.r,ys=H2-P.t-P.b,xlo=0.55,xhi=0.80;
  let ylo=Math.min(...pts.map(p=>p.y),0),yhi=Math.max(...pts.map(p=>p.y),0);const pad=(yhi-ylo)*0.18||1;ylo-=pad;yhi+=pad;
  const X=v=>P.l+(v-xlo)/(xhi-xlo)*xs, Y=v=>P.t+(1-(v-ylo)/(yhi-ylo))*ys;
  let h='';
  [yhi,(yhi+ylo)/2,0,ylo].forEach(g=>{h+=`<line class="grid-l" x1="${P.l}" y1="${Y(g)}" x2="${W-P.r}" y2="${Y(g)}" ${Math.abs(g)<0.001?'stroke="var(--line2)"':''}/>`;
    h+=`<text class="tlab" x="${P.l-6}" y="${Y(g)+3}" text-anchor="end">${g>=0?'+':''}${g.toFixed(0)}%</text>`;});
  [0.60,0.65,0.70,0.75].forEach(t=>{h+=`<text class="tlab" x="${X(t)}" y="${H2-12}" text-anchor="middle">${t.toFixed(2)}</text>`;});
  h+=`<text class="tlab" x="${P.l+xs/2}" y="${H2+2}" text-anchor="middle" style="letter-spacing:.1em">composite score at entry →</text>`;
  let r2='—';
  if(n>=2){const sx=pts.reduce((a,p)=>a+p.x,0),sy=pts.reduce((a,p)=>a+p.y,0),
    sxx=pts.reduce((a,p)=>a+p.x*p.x,0),sxy=pts.reduce((a,p)=>a+p.x*p.y,0),
    syy=pts.reduce((a,p)=>a+p.y*p.y,0);
    const den=(n*sxx-sx*sx);
    if(den!==0){const slope=(n*sxy-sx*sy)/den,inter=(sy-slope*sx)/n;
      h+=`<line x1="${X(xlo)}" y1="${Y(slope*xlo+inter)}" x2="${X(xhi)}" y2="${Y(slope*xhi+inter)}" stroke="var(--ink-faint)" stroke-width="1.2" stroke-dasharray="4 4"/>`;
      const r=(n*sxy-sx*sy)/Math.sqrt(den*(n*syy-sy*sy)||1);r2=(r*r).toFixed(3);}}
  pts.forEach(p=>{h+=`<circle cx="${X(p.x)}" cy="${Y(p.y)}" r="4" fill="${p.y>=0?'var(--pos)':'var(--neg)'}" fill-opacity="0.85" stroke="var(--bg)" stroke-width="1"/>`;});
  svg.innerHTML=h;
  note.innerHTML=`<b>n = ${n}.</b> ${n<30?'Too few to read a slope (need ~30+).':'Read the slope with care.'} Running R² = ${r2}. Backtesting found R²≈0.004 (a cloud, no edge) — watch whether forward data ever changes that.`;
})();

/* regime strip */
(()=>{
  const map={'risk-on':['r-on','RISK-ON','var(--pos)'],'risk-mid':['r-mid','MID','var(--amber)'],
    'mid':['r-mid','MID','var(--amber)'],'risk-off':['r-off','RISK-OFF','var(--neg)']};
  const el=document.getElementById('regime');
  if(!PAPER_DATA.regime.length){el.innerHTML='<div class="rcell"><div class="rm">—</div><div class="rs muted">no months yet</div></div>';return;}
  el.innerHTML=PAPER_DATA.regime.map(r=>{const m=map[r.state]||['r-mid',r.state.toUpperCase(),'var(--amber)'];
    return `<div class="rcell"><div class="rm">${r.month}</div><div class="rs ${m[0]}"><span class="rdot" style="background:${m[2]}"></span>${m[1]}</div></div>`;}).join('');
})();
"""


def render_html(data: dict[str, Any]) -> str:
    """Render the self-contained dashboard HTML from the live PAPER_DATA dict."""
    payload = json.dumps(data, separators=(",", ":"))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QIVC · Paper Ledger</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;1,6..72,400;1,6..72,500&family=IBM+Plex+Mono:ital,wght@0,300;0,400;0,500;0,600;1,400&display=swap" rel="stylesheet">
<style>{_CSS}</style>
</head>
<body>
{_BODY}
<script>
const PAPER_DATA = {payload};
{_SCRIPT}
</script>
</body>
</html>
"""
