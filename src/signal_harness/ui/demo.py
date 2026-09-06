"""Single-page Golden Demo UI served by the local FastAPI service."""

from __future__ import annotations


def render_demo_page() -> str:
    """Return the dependency-free interview demo page."""

    return r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SignalHarness Golden Demo</title>
<style>
:root {
  --paper:#eef2f7; --panel:#ffffff; --ink:#172033; --muted:#66738a;
  --line:#c8d2e1; --line-strong:#91a1ba; --blue:#315bd8; --blue-soft:#e8edff;
  --orange:#e66a32; --orange-soft:#fff0e8; --mint:#147d68; --mint-soft:#e3f5ef;
  --red:#b43a46; --red-soft:#fdebed; --navy:#10213c; --shadow:0 18px 48px rgba(16,33,60,.08);
}
*{box-sizing:border-box} html{scroll-behavior:smooth}
body{margin:0;background:var(--paper);color:var(--ink);font-family:"Avenir Next",Avenir,"Segoe UI",sans-serif}
button,select{font:inherit} button:focus-visible,select:focus-visible,.stage:focus-visible{outline:3px solid rgba(49,91,216,.28);outline-offset:3px}
.shell{max-width:1460px;margin:0 auto;padding:28px 32px 56px}
.topbar{display:flex;align-items:center;justify-content:space-between;gap:24px;border-bottom:1px solid var(--line);padding-bottom:18px}
.brand{display:flex;gap:14px;align-items:center}.mark{width:38px;height:38px;border-radius:9px;background:var(--navy);display:grid;place-items:center;color:white;font-weight:800;letter-spacing:-.04em}
.brand h1{font-size:19px;margin:0;letter-spacing:-.02em}.brand p{margin:2px 0 0;color:var(--muted);font-size:13px}
.rail{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}.badge{border:1px solid var(--line);background:rgba(255,255,255,.72);padding:7px 10px;border-radius:7px;font-size:12px;color:var(--muted)}
.badge strong{color:var(--ink);font-weight:700}.badge.live{border-color:#9dc8bc;background:var(--mint-soft);color:#0d5c4d}
.hero{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(330px,.7fr);gap:24px;padding:34px 0 24px}
.hero-copy{padding:12px 0}.kicker{font-size:13px;color:var(--blue);font-weight:700;margin-bottom:10px}.hero h2{font-size:clamp(34px,5vw,68px);line-height:.98;letter-spacing:-.055em;margin:0;max-width:870px}
.hero p{font-size:17px;line-height:1.65;color:var(--muted);max-width:720px;margin:22px 0 0}
.control-panel{background:var(--navy);color:white;border-radius:16px;padding:22px;box-shadow:var(--shadow);align-self:end}
.control-panel h3{font-size:14px;margin:0 0 16px;color:#d9e2f2;font-weight:600}.controls{display:grid;grid-template-columns:1fr auto;gap:10px}
select{width:100%;background:#172d50;color:white;border:1px solid #385274;border-radius:9px;padding:12px 13px}
.run-btn{border:0;background:#fff;color:var(--navy);font-weight:750;border-radius:9px;padding:12px 18px;cursor:pointer}.run-btn:hover{background:#eaf0ff}.run-btn:disabled{opacity:.5;cursor:not-allowed}
.connection{display:flex;align-items:center;gap:8px;margin-top:15px;color:#aebbd0;font-size:12px}.dot{width:8px;height:8px;border-radius:50%;background:#6f7d92}.dot.connected{background:#4fd0ad;box-shadow:0 0 0 4px rgba(79,208,173,.12)}.dot.error{background:#ff7b87}
.statusline{margin-top:10px;font-family:"SFMono-Regular",Menlo,monospace;font-size:11px;color:#8fa2bf;word-break:break-all}
.main-grid{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(360px,.55fr);gap:22px;align-items:start}
.board,.side-panel,.ledger,.evidence-strip{background:var(--panel);border:1px solid var(--line);box-shadow:var(--shadow)}
.board{border-radius:18px;padding:24px}.section-head{display:flex;justify-content:space-between;align-items:end;gap:20px;margin-bottom:18px}.section-head h3{margin:0;font-size:18px;letter-spacing:-.025em}.section-head p{margin:4px 0 0;color:var(--muted);font-size:13px}.mini{font-size:11px;color:var(--muted);font-family:"SFMono-Regular",Menlo,monospace}
.pipeline{position:relative;display:grid;gap:10px}.pipeline:before{content:"";position:absolute;left:22px;top:26px;bottom:26px;width:2px;background:var(--line)}
.stage{position:relative;display:grid;grid-template-columns:46px minmax(0,1fr) auto;align-items:center;gap:13px;border:1px solid var(--line);background:#fbfcfe;border-radius:11px;padding:12px 14px 12px 10px;cursor:pointer;text-align:left;color:inherit;transition:border-color .15s,background .15s,transform .15s}
.stage:hover{border-color:var(--line-strong);transform:translateX(2px)}.stage.selected{border-color:var(--blue);background:#f7f9ff}.stage.running{border-color:#9fb4f6;background:var(--blue-soft)}.stage.success{border-color:#9dc8bc;background:#f8fffc}.stage.warn{border-color:#f0b28e;background:#fffaf7}.stage.error{border-color:#e6a1aa;background:#fff8f9}
.stage-icon{position:relative;z-index:2;width:28px;height:28px;border-radius:50%;display:grid;place-items:center;background:#e5eaf2;border:4px solid var(--panel);font-size:11px;font-weight:800;color:var(--muted)}
.stage.running .stage-icon{background:var(--blue);color:white}.stage.success .stage-icon{background:var(--mint);color:white}.stage.warn .stage-icon{background:var(--orange);color:white}.stage.error .stage-icon{background:var(--red);color:white}
.stage-copy{display:grid;grid-template-columns:1fr;row-gap:3px;min-width:0}.stage-title{display:block;font-weight:750;font-size:14px;line-height:1.2}.stage-sub{display:block;font-size:12px;color:var(--muted);margin-top:2px}.stage-meta{font-family:"SFMono-Regular",Menlo,monospace;font-size:11px;color:var(--muted);text-align:right}.stage-meta b{display:block;color:var(--ink);font-family:inherit}
.side-panel{border-radius:18px;overflow:hidden}.side-head{padding:20px 20px 14px;border-bottom:1px solid var(--line)}.side-head h3{margin:0;font-size:18px}.side-head p{margin:5px 0 0;color:var(--muted);font-size:12px}.detail{padding:18px 20px;min-height:270px}.empty{color:var(--muted);line-height:1.55;font-size:14px}.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin:14px 0}.detail-kv{border:1px solid var(--line);border-radius:8px;padding:10px}.detail-kv span{display:block;color:var(--muted);font-size:10px;margin-bottom:3px}.detail-kv strong{font-family:"SFMono-Regular",Menlo,monospace;font-size:12px}.tool-list{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.tool{font-family:"SFMono-Regular",Menlo,monospace;font-size:10px;border:1px solid var(--line);border-radius:6px;padding:5px 7px;background:#f8fafc}.tool.blocked{border-color:#efb08f;background:var(--orange-soft)}
.results{margin-top:16px;display:grid;gap:10px}.decision{border:1px solid var(--line);border-radius:10px;padding:13px;background:#fff}.decision-top{display:flex;justify-content:space-between;gap:12px}.decision-title{font-weight:750;font-size:13px}.decision-score{font-family:"SFMono-Regular",Menlo,monospace;font-size:14px;font-weight:800}.decision-tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.tag{font-size:10px;border-radius:5px;padding:4px 6px;background:#eef2f7;color:#536078}.tag.alert,.tag.action_required{background:var(--orange-soft);color:#a8471d}.tag.save{background:var(--mint-soft);color:#0c6b58}
.evidence-strip{margin-top:22px;border-radius:16px;padding:18px 20px;display:grid;grid-template-columns:1.1fr .9fr;gap:24px}.metrics{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}.metric{border-left:2px solid var(--blue);padding:5px 10px}.metric b{display:block;font-size:18px;letter-spacing:-.03em}.metric span{font-size:10px;color:var(--muted)}.evidence-copy h3{margin:0 0 8px;font-size:15px}.evidence-copy p{margin:0;color:var(--muted);font-size:12px;line-height:1.55}
.mcp{display:grid;grid-template-columns:auto 1fr;gap:14px;align-items:start}.mcp-state{width:54px;height:54px;border-radius:12px;background:var(--mint-soft);display:grid;place-items:center;color:var(--mint);font-weight:800;font-size:12px}.mcp-tools{display:flex;gap:6px;flex-wrap:wrap}.mcp-tools .tool{background:#fff}
.ledger{margin-top:22px;border-radius:16px;overflow:hidden}.ledger-head{padding:18px 20px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between}.ledger-head h3{margin:0;font-size:16px}.trace-wrap{max-height:420px;overflow:auto}.trace-table{width:100%;border-collapse:collapse;font-family:"SFMono-Regular",Menlo,monospace;font-size:11px}.trace-table th,.trace-table td{padding:9px 12px;border-bottom:1px solid #e5eaf1;text-align:left;vertical-align:top}.trace-table th{position:sticky;top:0;background:#f7f9fc;color:var(--muted);font-weight:600;z-index:2}.trace-table td:nth-child(1){width:48px;color:var(--muted)}.trace-table td:nth-child(5){color:var(--muted)}
.health-grid{display:grid;grid-template-columns:repeat(6,1fr);gap:1px;background:var(--line);border-top:1px solid var(--line)}.health-item{background:#f8fafc;padding:11px 12px}.health-item span{font-size:9px;color:var(--muted);display:block}.health-item b{font-family:"SFMono-Regular",Menlo,monospace;font-size:12px}
.footer{margin-top:20px;color:var(--muted);font-size:11px;display:flex;justify-content:space-between;gap:20px}
@media(max-width:1000px){.hero,.main-grid,.evidence-strip{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(3,1fr)}}
@media(max-width:650px){.shell{padding:20px 14px 42px}.topbar{align-items:flex-start;flex-direction:column}.rail{justify-content:flex-start}.hero{padding-top:22px}.hero h2{font-size:42px}.controls{grid-template-columns:1fr}.metrics,.health-grid{grid-template-columns:repeat(2,1fr)}.stage{grid-template-columns:42px 1fr}.stage-meta{grid-column:2;text-align:left}.detail-grid{grid-template-columns:1fr}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
</style>
</head>
<body>
<div class="shell">
  <header class="topbar">
    <div class="brand"><div class="mark">SH</div><div><h1>SignalHarness Flight Deck</h1><p>Golden Demo · live runtime evidence, not a simulated animation</p></div></div>
    <div class="rail" id="badges"><div class="badge"><strong>5</strong> Agents</div><div class="badge"><strong>5</strong> MCP tools</div><div class="badge"><strong>40</strong> regression cases</div><div class="badge live">SSE ready</div></div>
  </header>

  <section class="hero">
    <div class="hero-copy"><div class="kicker">Auditable agent execution</div><h2>Watch the harness make a decision, step by step.</h2><p>One run shows the real route from signal collection through five Agents, controlled tools, guarded scoring, and review-only learning. The page consumes the same trace that is written for audit.</p></div>
    <div class="control-panel">
      <h3>Start a bounded run</h3>
      <div class="controls"><select id="mode"><option value="mock-agent" selected>mock-agent · offline five-Agent path</option><option value="demo">demo · deterministic fallback</option><option value="agent">agent · real provider from environment</option></select><button class="run-btn" id="runBtn">Run Golden Demo</button></div>
      <div class="connection"><span class="dot" id="dot"></span><span id="connectionText">Idle</span></div>
      <div class="statusline" id="runId">No active run</div>
    </div>
  </section>

  <main class="main-grid">
    <section class="board">
      <div class="section-head"><div><h3>Runtime pipeline</h3><p>Stages change only when real trace events arrive.</p></div><div class="mini" id="eventCounter">0 SSE events</div></div>
      <div class="pipeline" id="pipeline"></div>
    </section>
    <aside class="side-panel">
      <div class="side-head"><h3 id="detailTitle">Stage inspector</h3><p>Click a stage to inspect schema, tools, permissions, fallback, and timing.</p></div>
      <div class="detail" id="detail"><div class="empty">Run the demo, then select a completed stage. SignalHarness will show the actual trace payload rather than a separate UI state model.</div></div>
      <div class="side-head"><h3>Final decisions</h3><p>Guarded score and decision produced by the runtime.</p></div>
      <div class="detail"><div class="results" id="results"><div class="empty">No decisions yet.</div></div></div>
    </aside>
  </main>

  <section class="evidence-strip">
    <div><div class="section-head"><div><h3>Regression evidence</h3><p id="regressionLabel">Loading committed resume-v1 evidence…</p></div></div><div class="metrics" id="metrics"></div></div>
    <div class="mcp"><div class="mcp-state">MCP</div><div><div class="section-head"><div><h3>Read-only integration surface</h3><p id="mcpLabel">Loading MCP metadata…</p></div></div><div class="mcp-tools" id="mcpTools"></div></div></div>
  </section>

  <section class="ledger">
    <div class="ledger-head"><h3>Live trace ledger</h3><div class="mini" id="traceCount">0 trace records</div></div>
    <div class="trace-wrap"><table class="trace-table"><thead><tr><th>#</th><th>Step / Agent</th><th>Status</th><th>Duration</th><th>Tools / detail</th></tr></thead><tbody id="traceBody"><tr><td colspan="5" class="empty">Trace events will appear here as the workflow executes.</td></tr></tbody></table></div>
    <div class="health-grid" id="health"></div>
  </section>
  <div class="footer"><span>In-process streaming demo · not a durable queue or distributed worker system.</span><span>CLI · REST · SSE · MCP share the same SignalHarness core.</span></div>
</div>
<script>
const STAGES=[
  {id:'collect',title:'Collect + normalize',sub:'source input · dedupe · noise · cluster',code:'01'},
  {id:'supervisor',title:'SignalSupervisorAgent',sub:'classification + route selection',code:'S'},
  {id:'evidence',title:'ContextEvidenceAgent',sub:'evidence plan + synthesis',code:'E'},
  {id:'guard',title:'Python Tool Guard',sub:'allowlist · permissions · budgets',code:'G'},
  {id:'impact',title:'ImpactAnalystAgent',sub:'semantic relevance + affected modules',code:'I'},
  {id:'action',title:'ActionPlannerAgent',sub:'bounded reversible actions',code:'A'},
  {id:'learning',title:'LearningPolicyAgent',sub:'review-only proposals',code:'L'},
  {id:'decision',title:'Guarded decision',sub:'Python-owned score + outputs',code:'✓'}
];
const state={source:null,runId:null,eventCount:0,traces:new Map(),stageTrace:new Map(),results:[],signals:[]};
const $=id=>document.getElementById(id);
function stageHtml(s){return `<button class="stage" id="stage-${s.id}" data-stage="${s.id}"><span class="stage-icon">${s.code}</span><span class="stage-copy"><span class="stage-title">${s.title}</span><span class="stage-sub">${s.sub}</span></span><span class="stage-meta"><b id="stage-status-${s.id}">pending</b><span id="stage-meta-${s.id}">—</span></span></button>`}
$('pipeline').innerHTML=STAGES.map(stageHtml).join('');
document.querySelectorAll('.stage').forEach(el=>el.addEventListener('click',()=>showStage(el.dataset.stage)));
function setStage(id,status,meta=''){const el=$(`stage-${id}`); if(!el)return; el.classList.remove('running','success','warn','error'); if(status!=='pending')el.classList.add(status); $(`stage-status-${id}`).textContent=status; $(`stage-meta-${id}`).textContent=meta||'—';}
function reset(){state.eventCount=0;state.traces.clear();state.stageTrace.clear();state.results=[];state.signals=[];$('eventCounter').textContent='0 SSE events';$('traceCount').textContent='0 trace records';$('traceBody').innerHTML='<tr><td colspan="5" class="empty">Waiting for runtime trace…</td></tr>';$('results').innerHTML='<div class="empty">No decisions yet.</div>';$('detail').innerHTML='<div class="empty">Select a stage as trace events arrive.</div>';STAGES.forEach(s=>setStage(s.id,'pending'));renderHealth();}
function setConnection(text,kind=''){ $('connectionText').textContent=text; $('dot').className=`dot ${kind}`; }
function mapStage(t){const agent=t.agent_name||t.agent||''; if(['load_config','collect_signals','normalize','deduplicate','noise_filter','cluster_signals'].includes(t.step))return'collect'; if(agent.includes('SignalSupervisor'))return'supervisor'; if(agent.includes('ContextEvidence'))return'evidence'; if(agent.includes('ImpactAnalyst'))return'impact'; if(agent.includes('ActionPlanner'))return'action'; if(agent.includes('LearningPolicy'))return'learning'; if(t.tools_requested?.length||t.tools_executed?.length||t.blocked_tools?.length||t.tool_errors?.length)return'guard'; if(['write_radar_digest','write_run_summary','write_json_outputs'].includes(t.step))return'decision'; return null;}
function nextStage(id){return {collect:'supervisor',supervisor:'evidence',evidence:'guard',guard:'impact',impact:'action',action:'learning',learning:'decision'}[id]}
function ingestTrace(payload){const t=payload.trace,index=payload.index; state.traces.set(index,t); const sid=mapStage(t); if(sid){state.stageTrace.set(sid,t); const bad=t.status==='error'||t.fallback_used; const warn=!bad&&(t.retry_count||t.tool_errors?.length||t.blocked_tools?.length); setStage(sid,bad?'error':warn?'warn':'success',`${t.duration_ms??0} ms`); const next=nextStage(sid); if(next&&$(`stage-status-${next}`).textContent==='pending')setStage(next,'running','live'); if((t.tools_requested?.length||t.tools_executed?.length||t.blocked_tools?.length||t.tool_errors?.length)){state.stageTrace.set('guard',t); const badTool=t.tool_errors?.length||t.blocked_tools?.length; setStage('guard',badTool?'warn':'success',`${t.tools_executed?.length||0} executed`);}}
  renderTrace();renderHealth(); if(document.querySelector('.stage.selected'))showStage(document.querySelector('.stage.selected').dataset.stage);
}
function renderTrace(){const rows=[...state.traces.entries()].sort((a,b)=>a[0]-b[0]);$('traceCount').textContent=`${rows.length} trace records`;if(!rows.length)return; $('traceBody').innerHTML=rows.map(([i,t])=>{const name=t.agent_name||t.agent||t.step;const tool=`${(t.tools_executed||[]).length}/${(t.tools_requested||[]).length} tools`;const detail=(t.detail||'').slice(0,110);return `<tr><td>${i+1}</td><td>${esc(name)}<br><span class="mini">${esc(t.step||'')}</span></td><td>${esc(t.status||'')}</td><td>${t.duration_ms??0} ms</td><td>${tool}${detail?` · ${esc(detail)}`:''}</td></tr>`}).join('');}
function showStage(id){document.querySelectorAll('.stage').forEach(el=>el.classList.toggle('selected',el.dataset.stage===id));const s=STAGES.find(x=>x.id===id);$('detailTitle').textContent=s?.title||'Stage inspector';const t=state.stageTrace.get(id);if(!t){$('detail').innerHTML='<div class="empty">No trace record for this stage yet.</div>';return;}const req=t.tools_requested||[],exe=t.tools_executed||[],blocked=t.blocked_tools||[],checks=t.permission_checks||[];$('detail').innerHTML=`<div class="detail-grid"><div class="detail-kv"><span>Schema</span><strong>${t.schema_valid===false?'invalid':t.schema_valid===true?'valid':'n/a'}</strong></div><div class="detail-kv"><span>Fallback</span><strong>${t.fallback_used?'yes':'no'}</strong></div><div class="detail-kv"><span>Duration</span><strong>${t.duration_ms??0} ms</strong></div><div class="detail-kv"><span>Retry</span><strong>${t.retry_count||0}</strong></div></div><div class="mini">Requested tools</div><div class="tool-list">${req.length?req.map(x=>`<span class="tool">${esc(x)}</span>`).join(''):'<span class="tool">none</span>'}</div><div class="mini" style="margin-top:13px">Executed / blocked</div><div class="tool-list">${exe.map(x=>`<span class="tool">${esc(x)}</span>`).join('')}${blocked.map(x=>`<span class="tool blocked">${esc(x)}</span>`).join('')||(!exe.length?'<span class="tool">none</span>':'')}</div><div class="mini" style="margin-top:13px">Permission checks</div><div class="tool-list">${checks.length?checks.map(x=>`<span class="tool">${esc(x)}</span>`).join(''):'<span class="tool">none recorded</span>'}</div>`;}
function renderResults(){if(!state.results.length){$('results').innerHTML='<div class="empty">No decisions yet.</div>';return;}const byId=new Map(state.signals.map(x=>[x.event_id,x]));const sorted=[...state.results].sort((a,b)=>(b.impact_score||0)-(a.impact_score||0));$('results').innerHTML=sorted.map(a=>{const e=byId.get(a.event_id)||{};return `<div class="decision"><div class="decision-top"><div class="decision-title">${esc(e.title||a.event_id)}</div><div class="decision-score">${Number(a.impact_score||0).toFixed(1)}</div></div><div class="decision-tags"><span class="tag ${esc(a.decision||'')}">${esc(a.decision||'unknown')}</span><span class="tag">${esc(a.category||'unknown')}</span></div></div>`}).join('');}
function renderHealth(){const traces=[...state.traces.values()].filter(t=>t.step==='llm_agent_call');const schema=traces.filter(t=>t.schema_valid===false).length;const fallback=traces.filter(t=>t.fallback_used).length;const errors=traces.reduce((n,t)=>n+(t.tool_errors||[]).length,0);const blocked=traces.reduce((n,t)=>n+(t.blocked_tools||[]).length,0);const tokens=traces.reduce((n,t)=>n+(t.total_tokens||0),0);const avg=traces.length?traces.reduce((n,t)=>n+(t.duration_ms||0),0)/traces.length:0;const vals=[['Schema failures',schema],['Fallbacks',fallback],['Tool errors',errors],['Blocked tools',blocked],['Tokens',tokens||'—'],['Avg LLM latency',traces.length?`${avg.toFixed(1)} ms`:'—']];$('health').innerHTML=vals.map(([k,v])=>`<div class="health-item"><span>${k}</span><b>${v}</b></div>`).join('');}
function onEvent(ev){state.eventCount++;$('eventCounter').textContent=`${state.eventCount} SSE events`;let data={};try{data=JSON.parse(ev.data)}catch{};if(ev.type==='run.started'){setConnection('Streaming live trace','connected');setStage('collect','running','live');}if(ev.type==='trace.step'||ev.type==='trace.step.updated')ingestTrace(data);if(ev.type==='run.completed'){state.signals=data.signals||[];state.results=data.assessments||[];setStage('decision','success',`${state.results.length} decisions`);renderResults();setConnection('Run complete','connected');$('runBtn').disabled=false;if(state.source){state.source.close();state.source=null;}}if(ev.type==='run.failed'){setConnection(`Run failed: ${data.run?.error_class||'error'}`,'error');$('runBtn').disabled=false;if(state.source){state.source.close();state.source=null;}}}
async function startRun(){if(state.source)state.source.close();reset();$('runBtn').disabled=true;setConnection('Creating run…');const mode=$('mode').value;try{const res=await fetch('/stream-runs',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({mode})});const run=await res.json();if(!res.ok)throw new Error(run.detail||`HTTP ${res.status}`);state.runId=run.run_id;$('runId').textContent=run.run_id;const source=new EventSource(run.events_url);state.source=source;['run.created','run.started','trace.step','trace.step.updated','run.completed','run.failed'].forEach(name=>source.addEventListener(name,onEvent));source.onopen=()=>setConnection('SSE connected','connected');source.onerror=()=>{if(state.source) setConnection('SSE reconnecting…','error');};}catch(err){setConnection(String(err),'error');$('runBtn').disabled=false;}}
async function loadMeta(){try{const m=await fetch('/demo/meta').then(r=>r.json());const r=m.regression;$('regressionLabel').textContent=`${r.suite} · current committed CI evidence · project-specific, not a general LLM benchmark`;const vals=[['Cases',r.cases],['Decision',pct(r.decision_accuracy)],['Category',pct(r.category_accuracy)],['Precision',pct(r.priority_precision)],['Recall',pct(r.priority_recall)]];$('metrics').innerHTML=vals.map(([k,v])=>`<div class="metric"><b>${v}</b><span>${k}</span></div>`).join('');$('mcpLabel').textContent=`${m.mcp.tool_count} tools · read-only · idempotent · permission guarded`;$('mcpTools').innerHTML=m.mcp.tools.map(x=>`<span class="tool">${esc(x.replace('signalharness_',''))}</span>`).join('');}catch{$('regressionLabel').textContent='Metadata unavailable';}}
function pct(v){return `${Math.round(Number(v)*100)}%`} function esc(v){return String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]))}
$('runBtn').addEventListener('click',startRun);renderHealth();loadMeta();
</script>
</body>
</html>'''
