(() => {
  "use strict";
  const $ = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = String(text); return e; };
  const root = document.getElementById("dashboard");
  let timer;
  const num = v => Number.isFinite(Number(v)) ? Number(v) : 0;
  const money = v => new Intl.NumberFormat("en-US",{style:"currency",currency:"USD"}).format(num(v));
  const pct = v => `${num(v)>=0?"+":""}${num(v).toFixed(3)}%`;
  const time = v => { if(!v)return "—"; const d=new Date(v); return Number.isNaN(d) ? String(v) : d.toLocaleString(undefined,{month:"short",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit"}); };
  const status = (v) => $("span",`status ${String(v||"").toLowerCase()}`, String(v||"unknown").replaceAll("_"," "));
  const panel = (title, caption, wide=false) => { const p=$("section",`panel${wide?" wide":""}`), h=$("div","head"), x=$("div"); x.append($("h2","",title)); if(caption)x.append($("p","caption",caption)); h.append(x); p.append(h); return p; };
  const metric = (name, val, klass="") => {const m=$("div","metric");m.append($("label","",name),$("b",klass,val));return m;};
  const table = (headers, rows) => {const wrap=$("div","table-wrap"), t=$("table"), head=$("thead"), hr=$("tr");headers.forEach(h=>hr.append($("th","",h)));head.append(hr);t.append(head);const b=$("tbody"); rows.forEach(row=>{const tr=$("tr");row.forEach(cell=>{const td=$("td"); if(cell instanceof Node)td.append(cell);else td.textContent=cell ?? "—";tr.append(td)});b.append(tr)});t.append(b);wrap.append(t);return wrap;};
  const empty = text => $("div","empty",text);
  const json = obj => $("pre","json",JSON.stringify(obj ?? {},null,2));
  function chart(points) {
    if(!points?.length)return empty("Alpaca has not returned portfolio history points yet.");
    const vals=points.map(x=>num(x.equity)), min=Math.min(...vals), max=Math.max(...vals), range=max-min||1;
    const pointsStr=vals.map((v,i)=>`${(i/(Math.max(vals.length-1,1))*100).toFixed(1)},${(94-(v-min)/range*82).toFixed(1)}`).join(" ");
    const c=$("div","chart"), svg=document.createElementNS("http://www.w3.org/2000/svg","svg"); svg.setAttribute("viewBox","0 0 100 100");svg.setAttribute("preserveAspectRatio","none");
    const area=document.createElementNS(svg.namespaceURI,"polygon");area.setAttribute("class","area");area.setAttribute("points",`0,100 ${pointsStr} 100,100`); const line=document.createElementNS(svg.namespaceURI,"polyline");line.setAttribute("points",pointsStr);svg.append(area,line);c.append(svg); return c;
  }
  function readiness(d){
    const p=panel("Readiness ledger","A startup claim is only as strong as its proof.");
    const r=d.readiness;
    if(!r){p.append(empty("Readiness evidence has not been published yet."));return p}
    const meta=$("div","small",`Account ••••${r.account_suffix||"—"} · ${r.account_phase||"unknown"} · market ${r.market_open?"open":"closed"}`); p.querySelector(".head").append(status(r.ready?"ready":"attention"));p.append(meta);
    const list=$("ul","checks");(r.checks||[]).forEach(x=>{const li=$("li");li.append(status(x.status),$("span","",x.label||"unnamed check"),$("span","evidence",x.evidence||"No evidence"));list.append(li)});p.append(list);return p;
  }
  function performance(d){
    const p=panel("Paper performance","Alpaca-sourced equity, fill history, and reconciliation.",true), v=d.performance;
    if(!v){p.append(empty("Performance snapshot unavailable until Alpaca returns account history."));return p}
    const ms=$("div","metrics");ms.append(metric("Starting equity",money(v.starting_equity)),metric("Current equity",money(v.equity),num(v.total_pl)>=0?"positive":"negative"),metric("Total P&L",money(v.total_pl),num(v.total_pl)>=0?"positive":"negative"),metric("Total return",pct(v.total_return_pct),num(v.total_return_pct)>=0?"positive":"negative"),metric("Realized P&L",money(v.realized_pl),num(v.realized_pl)>=0?"positive":"negative"),metric("Unrealized P&L",money(v.unrealized_pl),num(v.unrealized_pl)>=0?"positive":"negative"),metric("Reconciliation",money(v.reconciliation_delta)));p.append(ms,chart(v.equity_points),$("p","caption",`${v.fill_count||0} fills retained · Equity is broker history baseline to current equity.`));
    const fills=v.fills||[];p.append($("h3","small","FILL LEDGER"),fills.length?table(["Time","Symbol","Side","Qty","Price","Status"],fills.slice().reverse().map(f=>[time(f.at),f.symbol,f.side,f.qty,money(f.price),f.order_status])):empty("No fills yet. A fresh paper account should start here."));return p;
  }
  function positions(d){
    const p=panel("Position monitoring","Attributed exposure, live broker legs, and exit evidence."), x=d.positions||{tracked:[],live_legs:[]};if(x.has_unmanaged)p.append($("div","errorbox","Unmanaged broker exposure detected. It is excluded from thesis-level invalidation and exit claims."));
    const rows=[...(x.tracked||[]).map(a=>[a.thesis_id,a.spread,a.entry_order,a.attribution,a.position,money(a.market_value),money(a.unrealized_pl),a.exit_status,a.invalidation]),...(x.live_legs||[]).filter(l=>!l.linked).map(l=>["—",l.symbol,"unknown","unlinked",`open broker leg (${l.side||"—"})`,money(l.market_value),money(l.unrealized_pl),"unmanaged","No stored thesis/order provenance"])];
    p.append(rows.length?table(["Thesis","Spread","Entry","Attribution","Position","Value","U/P&L","Exit","Invalidation"],rows):empty("No thesis-linked position is open or awaiting entry."));
    const legs=x.live_legs||[];p.append($("h3","small","LIVE ALPACA LEGS"),legs.length?table(["Symbol","Linked","Side","Qty","Entry","Current","Value","U/P&L","U/P&L %"],legs.map(l=>[l.symbol,l.linked?"yes":"no",l.side,l.qty,money(l.avg_entry_price),money(l.current_price),money(l.market_value),money(l.unrealized_pl),pct(l.unrealized_pl_pct)])):empty("No open broker legs."));return p;
  }
  function scout(d){
    const latest=(d.cycles||[]).find(c=>Array.isArray(c?.thesis?.leaderboard)||Array.isArray(c?.leaderboard));
    const rows=latest?.thesis?.leaderboard||latest?.leaderboard||[];
    const p=panel("Opportunity scout","Market breadth collapses through deterministic ranking before any candidate reaches Grok.",true);
    if(!rows.length){p.append(empty("No scout leaderboard was retained for the latest decision cycle."));return p}
    const observed=rows.length, probed=rows.filter(r=>r.probed).length, advanced=rows.filter(r=>(r.feasible_sides||[]).length).slice(0,3).length;
    const funnel=$("div","scout-funnel");[["Observed",observed],["Options probed",probed],["To Grok",advanced]].forEach((x,i)=>{const n=$("div","funnel-step");n.append($("span","",x[0]),$("b","",x[1]));funnel.append(n);if(i<2)funnel.append($("span","funnel-arrow","→"))});p.append(funnel);
    const body=rows.map((r,i)=>{const factor=r.factors||{}, details=$("details","scout-details"), summary=$("summary","",`Factors · trend distance ${factor.trend_distance??"—"} · momentum 5d ${factor.momentum_5d??"—"}`);details.append(summary,$("p","small",`Trend alignment: ${factor.trend_alignment??"—"} · trend: ${factor.trend??"—"} · volatility fit: ${factor.volatility_fit??"—"} · calls: ${r.call_count??"—"} · puts: ${r.put_count??"—"} · regime: ${r.regime??"—"}`));return [i+1,r.symbol||"—",r.stock_score??r.stock_rank??"—",status(r.status||"not_probed"),(r.feasible_sides||[]).join(" / ")||"—",r.options_score??"—",r.total_score??"—",r.reason||"No reason retained.",details]});
    p.append(table(["Rank","Symbol","Stock","Options status","Feasible","Options","Total","Reason","Evidence"],body));return p;
  }
  function detailCard(name, rows) {const c=$("section","evidence-card"), h=$("h3","",name);c.append(h);if(!rows?.length)c.append($("p","small","No record retained."));else rows.forEach(r=>{const q=$("p");q.append($("strong","",r.a||"Record"),document.createTextNode(` · ${r.b||"—"}`));c.append(q)});return c}
  function cycleElement(c){
    const wrap=$("article","cycle"), b=$("button"), t=c.thesis||{}, gates=c.gates||[], pass=gates.filter(g=>String(g.status||"").toLowerCase()==="pass").length;
    b.type="button";b.setAttribute("aria-expanded","false"); b.append($("span","cycle-time",time(c.at)),$("span","",t.underlying||c.underlying||"—"),$("span","cycle-title",String(c.decision||"record").toUpperCase()+` · ${pass}/${gates.length} gates`),$("span","chev","+")); wrap.append(b);
    const d=$("div","detail"), why=$("div","evidence-card");why.append($("h3","","Decision evidence"),$("p","",c.decision_reason||c.gate_summary||t.notes||"No decision rationale retained.")); const thesis=detailCard("1 / Grok thesis",[{a:`${t.side||"—"} · conviction ${t.conviction??"—"} · ${t.regime||"—"}`,b:t.setup||"No setup recorded."},{a:"Invalidation",b:t.invalidation||"Not recorded"},{a:"Horizon / expected move",b:`${t.horizon||"—"} / ${t.expected_move_pct??"—"}% · ${t.iv_note||"No IV note"}`}]);
    const gatesCard=detailCard("2 / Deterministic gate",(gates||[]).map(g=>({a:`${String(g.status||"unknown").toUpperCase()} · ${g.name||"gate"} · ${time(g.at)}`,b:g.evidence||"No evidence"})));
    const traces=detailCard("3 / Tool trace",(c.traces||[]).map(q=>({a:`${q.tool||"tool"} / ${q.step||"step"} / ${String(q.status||"unknown").toUpperCase()} · ${time(q.at)}`,b:q.evidence||"No evidence"})));
    const orderData=c.order||t.order||{}, history=c.order_history||[], monitor=c.monitoring||t.monitoring||{};
    const order=detailCard("4 / Order & fills",[{a:`${orderData.status||c.order_status||"not submitted"}`,b:orderData.order_id||orderData.id||"No broker order was submitted."},...history.map(o=>({a:`${o.status||"observed"} · ${time(o.observed_at||o.at)}`,b:`filled ${o.filled_qty??"—"} @ ${o.filled_avg_price??"—"}`}))]);
    const mon=detailCard("5 / Monitoring & exit",[{a:monitor.position_status||"not opened",b:monitor.exit_status||c.exit_status||"not applicable"},{a:"Evidence",b:monitor.exit_reason||c.exit_reason||"No exit was required."}]);
    const grid=$("div","detail-grid");grid.append(why,thesis,gatesCard,traces,order,mon);const raw=$("details","");raw.append($("summary","small","Market snapshots & sanitized cycle record"),json(c));d.append(grid,raw);wrap.append(d);
    b.addEventListener("click",()=>{const open=wrap.classList.toggle("open");b.setAttribute("aria-expanded",String(open));});return wrap;
  }
  function cycles(d){const p=panel("Decision history","Expand any record to inspect the complete proposal → gate → tool → monitor path.",true), cs=d.cycles||[];if(!cs.length){p.append(empty("No cycle yet. Run the cycle runner to publish the first auditable decision."));return p}cs.forEach(c=>p.append(cycleElement(c)));return p}
  function render(d){root.replaceChildren();document.getElementById("banner").textContent=`${d.banner||"Read-only audit dashboard. Broker writes are never initiated here."} Execution ${d.execution_enabled?"is enabled for the cycle runner; this page remains read-only.":"is disabled on this dashboard host."}`;const s=String(d.status||"loading");document.getElementById("stampStatus").textContent=`${s.toUpperCase()}\nRECORD`;root.append(readiness(d),scout(d),performance(d),positions(d),cycles(d));if(d.error)root.prepend(Object.assign($("div","errorbox",d.error),{style:"grid-column:1/-1"}));document.getElementById("updated").textContent=`Generated ${time(d.generated_at)} · last attempt ${time(d.last_attempt_at)}`;document.getElementById("poll").textContent=`Refresh cadence: ${d.refresh_interval_seconds||"—"} seconds`;clearTimeout(timer);timer=setTimeout(load,Math.max(5,num(d.refresh_interval_seconds)||20)*1000)}
  async function load(){try{const [dash,health]=await Promise.all([fetch("/api/dashboard",{headers:{Accept:"application/json"}}),fetch("/api/health",{headers:{Accept:"application/json"}})]);if(!dash.ok)throw new Error(`Dashboard request failed (${dash.status})`);const data=await dash.json(), h=health.ok?await health.json():{};document.getElementById("healthText").textContent=h.ok?`${h.status||"healthy"} · ${time(h.generated_at)}`:"health unavailable";document.getElementById("healthDot").className=`dot${h.ok?"":" bad"}`;render(data)}catch(e){root.replaceChildren();const p=panel("Audit feed unavailable","No paper trade action was attempted.");p.append($("div","errorbox",e.message),empty("Retrying automatically. Check the dashboard service and broker-read configuration."));root.append(p);document.getElementById("healthText").textContent="health unavailable";document.getElementById("healthDot").className="dot bad";clearTimeout(timer);timer=setTimeout(load,10000)}}load();
})();