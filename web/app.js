// Mission Control frontend: hash router, one render function per view, SSE for live data.
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const fmtB = b => { if (b == null) return "–"; const u = ["B", "K", "M", "G", "T"]; let i = 0; while (b >= 1024 && i < 4) { b /= 1024; i++; } return b.toFixed(i ? 1 : 0) + u[i]; };
const pct = v => v == null ? "–" : v.toFixed(0) + "%";

// Tiny SVG sparkline: values in [0, max].
function spark(vals, max = 100, w = 140, h = 30) {
  if (!vals.length) return `<svg class="spark" width="${w}" height="${h}"></svg>`;
  const m = max || Math.max(...vals, 1), n = Math.max(vals.length - 1, 1);
  const pts = vals.map((v, i) => `${(i / n * w).toFixed(1)},${(h - 1 - Math.min(v / m, 1) * (h - 2)).toFixed(1)}`).join(" ");
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline points="${pts}"/></svg>`;
}
const bar = p => `<span class="bar"><i style="width:${Math.min(p ?? 0, 100)}%" class="${p > 90 ? "hot" : p > 70 ? "warm" : ""}"></i></span>`;

// Everything a view opens (streams, timers, subscriptions) registers a cleanup that runs on navigation.
let cleanups = [];
const every = (ms, fn) => { const t = setInterval(fn, ms); cleanups.push(() => clearInterval(t)); };
function stream(url, onmsg) { const es = new EventSource(url); es.onmessage = e => onmsg(JSON.parse(e.data)); cleanups.push(() => es.close()); return es; }

// Hub topics (runs, local, box:<id>) share ONE EventSource: browsers allow only 6 connections per host.
// fn(items, reset): reset=true delivers the topic's history and replaces whatever the view had.
const bus = {handlers: new Map(), es: null, timer: 0};
function busOpen() {
  clearTimeout(bus.timer);
  bus.timer = setTimeout(() => {
    bus.es?.close();
    const ts = [...bus.handlers.keys()];
    bus.es = ts.length ? new EventSource("/api/stream?" + ts.map(t => "t=" + encodeURIComponent(t)).join("&")) : null;
    if (bus.es) bus.es.onmessage = e => { const m = JSON.parse(e.data); bus.handlers.get(m.topic)?.forEach(f => f(m.items, m.reset)); };
  }, 30);  // batch the subscribe/unsubscribe burst of a navigation into one reconnect
}
function sub(topic, fn, permanent) {
  if (!bus.handlers.has(topic)) { bus.handlers.set(topic, new Set()); busOpen(); }
  bus.handlers.get(topic).add(fn);
  if (!permanent) cleanups.push(() => { const h = bus.handlers.get(topic); h.delete(fn); if (!h.size) { bus.handlers.delete(topic); busOpen(); } });
}
// History-keeping subscription: hist is reset on (re)connect, capped at max.
function subHist(topic, max, draw) {
  const hist = [];
  sub(topic, (items, reset) => { if (reset) hist.length = 0; hist.push(...items); hist.splice(0, Math.max(0, hist.length - max)); draw(hist); });
  return hist;
}

// One app-wide runs stream: feeds the header burn pill and every view that lists runs.
let RUNS = null;
const runsSubs = new Set();
function onRuns(fn) { runsSubs.add(fn); cleanups.push(() => runsSubs.delete(fn)); if (RUNS) fn(RUNS); }
sub("runs", items => { RUNS = items[items.length - 1]; burnPill(); runsSubs.forEach(f => f(RUNS)); }, true);
const liveVast = () => (RUNS?.runs || []).filter(r => r.source === "vast" && r.status === "running");
const gpuIdle = r => r.meta?.gpu_util != null && r.meta.gpu_util < 5;
function burnPill() {
  const v = liveVast(), el = $("#burn"), err = RUNS.sources?.vast && !RUNS.sources.vast.ok;
  el.hidden = !v.length && !err;
  const idle = v.filter(gpuIdle).length;
  el.innerHTML = err ? `⚠ Vast poll failing` : `● ${v.length} Vast box${v.length > 1 ? "es" : ""} · ${money(v.reduce((a, r) => a + r.dph, 0))}/h` + (idle ? ` · ${idle} at GPU 0%` : "");
  el.title = err ? RUNS.sources.vast.error : "Running Vast instances bill by the hour whether or not the GPU is used";
}

// ---------- Devices (Mac) ----------
function macCard(hist) {
  const s = hist[hist.length - 1];
  if (!s) return `<div class="card"><h3>This Mac</h3><p class="muted">waiting for first sample…</p></div>`;
  const avg = a => a.reduce((x, y) => x + y, 0) / a.length;
  const cpuH = hist.map(x => avg(x.cpu)), net = hist.filter(x => x.net);
  const b = s.battery;
  return `<div class="card"><h3>This Mac <span class="muted">load ${s.load.map(l => l.toFixed(1)).join(" ")}</span></h3>
  <div class="metrics">
    <div><label>CPU ${pct(avg(s.cpu))}</label>${spark(cpuH)}</div>
    <div><label>RAM ${pct(s.mem.pct)} <span class="muted">${fmtB(s.mem.used)}/${fmtB(s.mem.total)}</span></label>${spark(hist.map(x => x.mem.pct))}</div>
    <div><label>Swap ${pct(s.swap.pct)} <span class="muted">${fmtB(s.swap.used)}</span></label>${spark(hist.map(x => x.swap.pct))}</div>
    <div><label>Net ↓${fmtB(s.net?.rx)}/s ↑${fmtB(s.net?.tx)}/s</label>${spark(net.map(x => x.net.rx), 0)}</div>
  </div>
  <div class="cores">${s.cpu.map((c, i) => `<div title="core ${i}: ${pct(c)}"><i style="height:${c}%"></i></div>`).join("")}</div>
  <table class="kv">${s.disks.map(d => `<tr><td>${esc(d.mount)}</td><td>${bar(d.pct)}</td><td>${pct(d.pct)}</td><td class="muted">${fmtB(d.used)} / ${fmtB(d.total)}</td></tr>`).join("")}
  ${b ? `<tr><td>Battery</td><td>${bar(b.pct)}</td><td>${pct(b.pct)}</td><td class="muted">${b.plugged ? "plugged in" : b.secsleft ? `${Math.round(b.secsleft / 60)} min left` : "on battery"}</td></tr>` : ""}</table>
  <p class="muted small">No GPU or temperature readings: macOS needs sudo for those.</p></div>`;
}

function procTable(procs) {
  return `<table class="grid"><thead><tr><th>PID</th><th>Name</th><th>CPU</th><th>RSS</th><th>User</th><th>Command</th></tr></thead><tbody>
  ${procs.map(p => `<tr><td>${p.pid}</td><td>${esc(p.name)}</td><td>${p.cpu.toFixed(1)}%</td><td>${fmtB(p.rss)}</td><td>${esc(p.user)}</td><td class="cmd" title="${esc(p.cmd)}">${esc(p.cmd)}</td></tr>`).join("")}</tbody></table>`;
}

function viewDevices(el) {
  el.innerHTML = `<h2>Devices</h2><div id="mac"></div><div id="boxes" class="cards"></div>`;
  const shown = new Set();
  onRuns(() => {
    for (const r of liveVast()) if (!shown.has(r.id)) {
      shown.add(r.id);
      const d = document.createElement("div"); $("#boxes").append(d);
      boxStream(d, r.meta.id, `<a href="#/run/${r.id}">Vast ${r.meta.id}</a> <span class="muted">${esc(r.device)} · ${money(r.dph)}/h</span>`);
    }
  });
  subHist("local", 90, hist => { $("#mac").innerHTML = macCard(hist); });
}

function viewProcesses(el) {
  el.innerHTML = `<h2>Processes <span class="muted">heaviest local processes, tracked or not (2 s)</span></h2><div id="procs" class="muted">loading…</div>`;
  sub("local", items => { if (items.length) $("#procs").innerHTML = procTable(items[items.length - 1].procs); });
}

// ---------- Runs ----------
const fmtDur = s => { if (s == null) return "–"; s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60); return h ? `${h}h${String(m).padStart(2, "0")}m` : m ? `${m}m${String(s % 60).padStart(2, "0")}s` : `${s}s`; };
const fmtT = t => t ? new Date(t * 1000).toLocaleString([], {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"}) : "–";
const money = v => v == null ? "–" : "$" + v.toFixed(2);
const badge = st => `<span class="badge ${esc(st)}">${esc(st)}</span>`;
const LIVE = ["running", "queued", "loading", "created"];
const dur = r => !r.started ? "–" : r.ended ? fmtDur(r.ended - r.started) : LIVE.includes(r.status) ? fmtDur(Date.now() / 1000 - r.started) : "–";
const filt = JSON.parse(localStorage.getItem("mc.filter") || "{}");

function runsTable(rs) {
  return `<table class="grid"><thead><tr><th>Status</th><th>Run</th><th>Source</th><th>Project</th><th>Device</th><th>Started</th><th>Duration</th><th>Cost</th></tr></thead><tbody>
  ${rs.map(r => `<tr class="${r.source === "vast" && r.status === "running" ? "burning" : ""}"><td>${badge(r.status)}</td><td><a href="#/run/${encodeURIComponent(r.id)}">${esc(r.name)}</a></td><td>${esc(r.source)}</td><td>${esc(r.project || "")}</td>
  <td>${esc(r.device || "")}${r.source === "vast" && r.status === "running" && r.meta.gpu_util != null ? ` <span class="${gpuIdle(r) ? "idle" : "muted"}">GPU ${pct(r.meta.gpu_util)}</span>` : ""}</td><td>${fmtT(r.started)}</td><td>${dur(r)}</td><td>${r.cost != null ? money(r.cost) + (r.dph ? ` <span class="muted">@${money(r.dph)}/h</span>` : "") : ""}</td></tr>`).join("") || `<tr><td colspan="8" class="muted">No runs.</td></tr>`}</tbody></table>`;
}

function viewRuns(el) {
  el.innerHTML = `<h2>Runs</h2><div class="toolbar">
    <select id="fsrc"><option value="">all sources</option><option>local</option><option>vast</option><option>kaggle</option></select>
    <select id="fproj"><option value="">all projects</option></select></div><div id="runs" class="muted">loading…</div>`;
  let rs = [];
  const draw = () => {
    const projs = [...new Set(rs.map(r => r.project).filter(Boolean))];
    const sel = $("#fproj"); if (sel.options.length - 1 !== projs.length) sel.innerHTML = `<option value="">all projects</option>` + projs.map(p => `<option>${esc(p)}</option>`).join("");
    $("#fsrc").value = filt.src || ""; sel.value = filt.proj || "";
    $("#runs").innerHTML = runsTable(rs.filter(r => (!filt.src || r.source === filt.src) && (!filt.proj || r.project === filt.proj)));
  };
  for (const [id, k] of [["#fsrc", "src"], ["#fproj", "proj"]]) $(id).onchange = e => { filt[k] = e.target.value; localStorage.setItem("mc.filter", JSON.stringify(filt)); draw(); };
  onRuns(d => { rs = d.runs; draw(); });
  every(5000, draw);  // running durations tick
}

// Log viewer: last N lines on connect, auto-scroll that pauses when you scroll up, substring filter.
function logViewer(el, url) {
  el.innerHTML = `<div class="toolbar"><input id="q" type="search" placeholder="filter lines…"> <span id="lstat" class="muted"></span>
    <button id="jump" hidden>⇣ paused, jump to end</button></div><pre id="log" class="log"></pre>`;
  const pre = $("#log", el), q = $("#q", el), MAX = 20000;
  let lines = [], follow = true;
  const match = l => !q.value || l.toLowerCase().includes(q.value.toLowerCase());
  const add = ls => { const f = document.createDocumentFragment(); for (const l of ls) if (match(l)) { const d = document.createElement("div"); d.textContent = l || " "; f.append(d); } pre.append(f);
    while (pre.childElementCount > MAX) pre.firstElementChild.remove(); };
  const status = () => $("#lstat", el).textContent = `${lines.length} lines` + (q.value ? `, ${pre.childElementCount} match` : "");
  pre.onscroll = () => { follow = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30; $("#jump", el).hidden = follow; };
  $("#jump", el).onclick = () => { pre.scrollTop = pre.scrollHeight; };
  q.oninput = () => { pre.textContent = ""; add(lines); status(); pre.scrollTop = pre.scrollHeight; };
  const es = stream(url, ls => { lines.push(...ls); if (lines.length > MAX) lines = lines.slice(-MAX); add(ls); status(); if (follow) pre.scrollTop = pre.scrollHeight; });
  es.onerror = () => { $("#lstat", el).textContent = es.readyState === 2 ? "log unavailable" : "reconnecting…"; };
  es.onopen = () => { lines = []; pre.textContent = ""; };  // server replays the tail on every (re)connect
  return es;
}

function viewRun(el, id) {
  id = decodeURIComponent(id);
  el.innerHTML = `<div id="rhead"></div><div id="rbox"></div><div id="rpick" class="toolbar"></div><div id="rlog"></div>`;
  let logStarted = false, rs = [];
  const head = () => {
    const r = rs.find(x => x.id === id);
    if (!r) { $("#rhead").innerHTML = `<p>Unknown run ${esc(id)}</p>`; return; }
    if (r.source === "vast") return vastHead(r, !logStarted && (logStarted = true));
    if (r.source === "kaggle") return kaggleHead(r, !logStarted && (logStarted = true));
    $("#rhead").innerHTML = `<h2>${esc(r.name)} ${badge(r.status)} <span class="muted">${esc(r.source)}${r.project ? " · " + esc(r.project) : ""}</span></h2>
      <table class="kv"><tr><td>Started</td><td>${fmtT(r.started)}</td><td>Duration</td><td>${dur(r)}</td></tr>
      ${r.cmd ? `<tr><td>Command</td><td colspan="3"><code>${esc(r.cmd)}</code></td></tr>` : ""}
      ${r.pid ? `<tr><td>PID</td><td>${r.pid}</td><td>Exit</td><td>${r.exit_code ?? "–"}</td></tr>` : ""}</table>`;
    $("#rhead").append(actionButtons(r));
    if (!logStarted) { logStarted = true; logViewer($("#rlog"), `/api/runs/${encodeURIComponent(id)}/log`); }
  };
  onRuns(d => { rs = d.runs; head(); });
  every(5000, () => rs.length && head());
}

// ---------- Lifecycle actions (explicit confirm per exact action) ----------
async function act(r, action, warning) {
  if (!confirm(`${action.toUpperCase()} ${r.name} (${r.id})?\n\n${warning}`)) return;
  const res = await fetch(`/api/runs/${encodeURIComponent(r.id)}/${action}`, {method: "POST", headers: {"X-MC-Confirm": `${action} ${r.id}`}});
  const body = await res.json();
  alert(res.ok ? body.detail : `Failed: ${body.detail}`);
}
function actionButtons(r) {
  const b = [];
  if (r.source === "local" && r.status === "running") b.push(["kill", `Sends SIGTERM to pid ${r.pid}. The job stops, and mc records the exit.`]);
  if (r.source === "vast" && r.status === "running") b.push(["stop", "GPU billing stops; storage keeps billing and the disk is kept. You can start it again later, but its GPU may be rented out by then."]);
  if (r.source === "vast" && r.status !== "destroyed") b.push(["destroy", "Deletes the instance AND its disk. Anything not copied off the box (e.g. /root/store) is gone. Billing ends."]);
  const el = document.createElement("div"); el.className = "actions";
  for (const [a, w] of b) { const btn = document.createElement("button"); btn.className = "danger"; btn.textContent = a[0].toUpperCase() + a.slice(1) + "…"; btn.onclick = () => act(r, a, w); el.append(btn); }
  return el;
}

// ---------- Kaggle ----------
const KAGGLE_DONE = ["done", "failed", "cancelled", "unknown"];
function kaggleHead(r, first) {
  const m = r.meta, fin = KAGGLE_DONE.includes(r.status);
  $("#rhead").innerHTML = `<h2>${esc(r.name)} ${badge(r.status)} <span class="muted">kaggle${m.version ? " · v" + m.version : ""}${r.project ? " · " + esc(r.project) : ""}</span></h2>
    <table class="kv"><tr><td>Started</td><td>${fmtT(r.started)}</td><td>Duration</td><td>${r.ended ? dur(r) : fin ? "– (from the log once fetched)" : dur(r) + " so far"}</td></tr>
    <tr><td>Accelerator</td><td>${esc(r.device || "–")}</td><td>Kernel</td><td><a href="https://www.kaggle.com/code/${esc(m.ref)}" target="_blank" rel="noopener">${esc(m.ref)} ↗</a></td></tr></table>
    ${m.failure ? `<p class="bad">Kaggle: ${esc(m.failure)}</p>` : ""}${m.error ? `<p class="bad small">Status lookup failed: ${esc(m.error)}</p>` : ""}`;
  if (fin && !first && !$("#log")) first = true;  // just finished while we watched: open the log now
  if (!first) return;
  if (!fin) { $("#rlog").innerHTML = `<div class="note">Kaggle doesn't stream logs for a running kernel. Status is polled every 60 s, and the full log is fetched automatically when it finishes. No device telemetry either, only the accelerator type.</div>`; return; }
  $("#rlog").innerHTML = "";
  logViewer($("#rlog"), `/api/runs/${encodeURIComponent(r.id)}/log`);
}

// ---------- Vast ----------
function vastHead(r, first) {
  const m = r.meta, iid = m.id, running = r.status === "running";
  $("#rhead").innerHTML = `<h2>${esc(r.name)} ${badge(r.status)} <span class="muted">vast · ${esc(r.device)}</span></h2>
    ${running && gpuIdle(r) ? `<div class="warnbox">GPU at ${pct(m.gpu_util)} (per Vast) while billing ${money(r.dph)}/h. Fine if the job is CPU-bound; otherwise the box is paying for nothing.</div>` : ""}
    <table class="kv"><tr><td>$/h</td><td class="money">${money(r.dph)}${running ? "" : " (storage)"}</td><td>Accrued ≈</td><td>${money(r.cost)}</td></tr>
    <tr><td>Started</td><td>${fmtT(r.started)}</td><td>Uptime</td><td>${dur(r)}</td></tr>
    <tr><td>Where</td><td>${esc(m.geolocation)}</td><td>Image</td><td><code>${esc(m.image_uuid)}</code></td></tr>
    <tr><td>Status</td><td colspan="3" class="muted">${esc(m.status_msg)}</td></tr></table>
    <p class="muted small">Accrued is uptime × current $/h: an estimate, not Vast's invoice.</p>`;
  $("#rhead").append(actionButtons(r));
  if (!first) return;
  if (!running) { $("#rlog").innerHTML = `<p class="muted">Not running: logs and live metrics need a running box (ssh).</p>`; return; }
  boxStream($("#rbox"), iid, "Live box metrics");
  fetch(`/api/runs/${encodeURIComponent(r.id)}/logs`).then(x => x.ok ? x.json() : x.json().then(e => Promise.reject(e.detail))).then(names => {
    $("#rpick").innerHTML = `<label>Log <select id="lname">${names.map(n => `<option>${esc(n)}</option>`).join("")}</select></label>
      <span class="muted small">queue = /root/queue.log (follows across jobs); others = /root/logs/&lt;job&gt;.log</span>`;
    let es;
    const open = () => { es?.close(); es = logViewer($("#rlog"), `/api/runs/${encodeURIComponent(r.id)}/log?name=${encodeURIComponent($("#lname").value)}`); };
    $("#lname").onchange = open; open();
  }).catch(e => { $("#rlog").innerHTML = `<p class="bad">Can't list logs: ${esc(e)}</p>`; });
}

function boxCard(hist, title) {
  const ok = hist.filter(x => !x.error), s = ok[ok.length - 1], last = hist[hist.length - 1];
  const err = last?.error ? `<p class="bad small">ssh: ${esc(last.error)}</p>` : "";
  if (!s) return `<div class="card"><h3>${title}</h3>${err || `<p class="muted">connecting over ssh…</p>`}</div>`;
  const idle = ok.length >= 6 && ok.slice(-6).every(x => x.gpus.every(g => (g.util ?? 0) < 5));
  return `<div class="card ${idle ? "idlecard" : ""}"><h3>${title} ${idle ? `<span class="idle">GPU 0% for 30 s+</span>` : ""}</h3>${err}
  ${s.gpus.map(g => `<div class="metrics">
    <div><label>GPU${g.index} util ${pct(g.util)} <span class="muted">${esc(g.name.replace("NVIDIA GeForce ", ""))}</span></label>${spark(ok.map(x => x.gpus[g.index]?.util ?? 0))}</div>
    <div><label>VRAM ${fmtB(g.mem_used * 2 ** 20)}/${fmtB(g.mem_total * 2 ** 20)}</label>${spark(ok.map(x => { const q = x.gpus[g.index]; return q ? 100 * q.mem_used / q.mem_total : 0; }))}</div>
    <div><label>Temp ${g.temp ?? "–"}°C</label>${spark(ok.map(x => x.gpus[g.index]?.temp ?? 0), 100)}</div>
    <div><label>Power ${g.power?.toFixed(0) ?? "–"}/${g.power_limit?.toFixed(0) ?? "–"} W</label>${spark(ok.map(x => x.gpus[g.index]?.power ?? 0), g.power_limit || 0)}</div></div>`).join("")}
  <div class="metrics">
    <div><label>CPU ${pct(s.cpu)} <span class="muted">${s.nproc} cores</span></label>${spark(ok.filter(x => x.cpu != null).map(x => x.cpu))}</div>
    <div><label>RAM ${fmtB(s.mem.used)}/${fmtB(s.mem.total)}</label>${spark(ok.map(x => 100 * x.mem.used / x.mem.total))}</div>
    <div><label>Disk ${fmtB(s.disk.used)}/${fmtB(s.disk.total)}</label>${bar(100 * s.disk.used / s.disk.total)}</div></div></div>`;
}

function boxStream(el, iid, title) {
  el.innerHTML = boxCard([], title);
  subHist(`box:${iid}`, 60, hist => { el.innerHTML = boxCard(hist, title); });
}

// ---------- Projects ----------
let PROJECTS = [];
let GH = null;  // GitHub repos, loaded the first time the menu opens (gh call is ~1-2 s, cached server-side 10 min)
const isoAgo = s => s ? ago(Date.parse(s) / 1000) : "–";
// Each project once: tracked (projects.yaml) and local (auto-found git repos) open the full page,
// with their GitHub repo merged in; GitHub-only repos (no clone on the T7) open a GitHub page.
function drawProjMenu() {
  const ghOf = p => GH && !GH.error ? GH.find(r => r.full.toLowerCase() === p.github) : null;
  const projLink = p => { const r = ghOf(p); return `<a href="#/project/${p.id}"><b>${esc(p.name)}</b>
      ${p.github ? `<span class="tag">${r?.private ? "🔒 " : ""}GitHub</span>` : `<span class="tag">local only</span>`}
      <span class="muted small right">${r ? isoAgo(r.pushed) : ""}</span></a>`; };
  const ghOnly = GH === null ? `<div class="muted small pad">loading GitHub repos…</div>`
    : GH.error ? `<div class="bad small pad">${esc(GH.error)}</div>`
    : GH.filter(r => !r.local).map(r => `<a href="#/gh/${r.full}">${r.private ? "🔒 " : ""}${esc(r.full.split("/")[1])}<span class="muted small right">${isoAgo(r.pushed)}</span></a>`).join("") || `<div class="muted small pad">none</div>`;
  const group = (title, items) => items ? `<div class="menuhead">${title}</div>${items}` : "";
  $("#projnav").innerHTML = group("Tracked", PROJECTS.filter(p => !p.auto).map(projLink).join(""))
    + group("Local, found on the T7", PROJECTS.filter(p => p.auto).map(projLink).join(""))
    + group("GitHub only (not cloned)", ghOnly);
}
const projReady = fetch("/api/projects").then(r => r.json()).then(ps => { PROJECTS = ps; drawProjMenu(); });
let ghP;  // one shared request; the server caches the list for 10 min
const loadGH = () => ghP ??= fetch("/api/github/repos").then(r => r.ok ? r.json() : r.json().then(x => ({error: x.detail})))
  .then(d => { GH = d; projReady.then(drawProjMenu); });
$("#projmenu").addEventListener("toggle", e => e.target.open && loadGH());
document.addEventListener("click", e => { if (!e.target.closest("#projmenu")) $("#projmenu").open = false; });
const slug = s => s.toLowerCase().trim().replace(/<[^>]+>/g, "").replace(/[^\w\s-]/g, "").replace(/\s+/g, "-");
marked.use({renderer: {heading(text, level, raw) { return `<h${level} id="${slug(raw)}">${text}</h${level}>\n`; }}});
const relTo = (base, href) => decodeURIComponent(new URL(href, "http://x/" + base).pathname.slice(1));
const fileUrl = (pid, path) => `/api/projects/${pid}/file?path=${encodeURIComponent(path)}`;
const ago = t => { const s = Date.now() / 1000 - t; return s < 3600 ? `${Math.round(s / 60)}m ago` : s < 86400 ? `${Math.round(s / 3600)}h ago` : `${Math.round(s / 86400)}d ago`; };

let mermaidP;
async function renderMermaid(el) {
  const blocks = el.querySelectorAll("pre code.language-mermaid");
  if (!blocks.length) return;
  mermaidP ??= new Promise((ok, err) => { const s = document.createElement("script"); s.src = "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"; s.onload = () => { mermaid.initialize({startOnLoad: false, theme: matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "default"}); ok(); }; s.onerror = err; document.head.append(s); });
  await mermaidP;
  for (const b of blocks) { const d = document.createElement("div"); d.className = "mermaid"; d.textContent = b.textContent; b.parentElement.replaceWith(d); }
  await mermaid.run({nodes: el.querySelectorAll(".mermaid")});
}

async function showDoc(el, p, path, keepScroll) {
  const r = await fetch(fileUrl(p.id, path));
  if (!r.ok) { el.innerHTML = `<p class="bad">${esc((await r.json()).detail)}</p>`; return; }
  const y = scrollY;
  el.innerHTML = marked.parse(await r.text());
  for (const img of el.querySelectorAll("img[src]")) if (!/^(https?:|data:)/.test(img.getAttribute("src"))) img.src = fileUrl(p.id, relTo(path, img.getAttribute("src")));
  for (const a of el.querySelectorAll("a[href]")) {
    const h = a.getAttribute("href");
    if (h.startsWith("#")) a.onclick = e => { e.preventDefault(); document.getElementById(decodeURIComponent(h.slice(1)))?.scrollIntoView(); };
    else if (/^https?:/.test(h)) { a.target = "_blank"; a.rel = "noopener"; }
    else { const t = relTo(path, h.split("#")[0]); a.href = p.docs.includes(t) ? `#/project/${p.id}/doc/${encodeURIComponent(t)}` : fileUrl(p.id, t); }
  }
  renderMermaid(el).catch(e => el.insertAdjacentHTML("afterbegin", `<p class="bad small">mermaid failed: ${esc(e)}</p>`));
  if (keepScroll) scrollTo(0, y);
}

async function gitPanel(el, pid) {
  const r = await fetch(`/api/projects/${pid}/git`);
  if (!r.ok) { el.innerHTML = `<p class="bad small">${esc((await r.json()).detail)}</p>`; return; }
  const g = await r.json();
  el.innerHTML = `<h3>Git <span class="muted small">${esc(g.branch)}</span></h3>
    <p class="small">${g.files.length ? `<details><summary>${g.files.length} uncommitted change${g.files.length > 1 ? "s" : ""}</summary>${g.files.map(f => `<div><code>${esc(f.xy)}</code> ${esc(f.path)}</div>`).join("")}</details>` : `<span class="muted">working tree clean</span>`}</p>
    <ul class="commits">${g.commits.map(c => `<li><code>${esc(c.hash)}</code> ${esc(c.subject)} <span class="muted small">${ago(+c.t)}</span></li>`).join("")}</ul>`;
}

async function viewProject(el, pid, kind, arg) {
  await projReady;
  const p = PROJECTS.find(x => x.id === pid);
  if (!p) { el.innerHTML = `<p>Unknown project ${esc(pid)}</p>`; return; }
  kind ||= "doc"; arg = arg ? decodeURIComponent(arg) : p.docs[0];
  const tab = (href, label, on) => `<a href="#/project/${p.id}/${href}" class="${on ? "on" : ""}">${esc(label)}</a>`;
  el.innerHTML = `<h2>${esc(p.name)} <span class="muted small">${esc(p.path)}</span>
      ${p.github ? `<a class="small" href="https://github.com/${esc(p.github)}" target="_blank" rel="noopener">GitHub ↗</a>` : `<span class="tag">local only</span>`}</h2>
    ${p.auto ? `<details class="note small"><summary>Found automatically (a git repo on the T7). Showing its top-level .md files. To pick docs, an architecture page, a roadmap or a Kaggle prefix, add it to projects.yaml.</summary><pre>  - id: ${esc(p.id)}
    name: ${esc(p.name)}
    path: ${esc(p.path)}
    docs: [${p.docs.map(esc).join(", ")}]
    roadmap: docs/ROADMAP.md</pre></details>` : ""}
    <div class="tabs">${p.docs.map(d => tab("doc/" + encodeURIComponent(d), d.split("/").pop().replace(/\.md$/, ""), kind === "doc" && d === arg)).join("")}
      ${p.architecture ? tab("arch", "Architecture", kind === "arch") : ""}${p.roadmap ? tab("roadmap", "Roadmap", kind === "roadmap") : ""}</div>
    <div class="${kind === "roadmap" ? "" : "projgrid"}"><div id="pmain"></div><aside id="pgit" class="card" ${kind === "roadmap" ? "hidden" : ""}></aside></div>`;
  const main = $("#pmain"), current = kind === "doc" ? arg : kind === "arch" ? p.architecture : p.roadmap;
  const render = keep => kind === "doc" ? showDoc(main, p, arg, keep)
    : kind === "arch" ? (main.innerHTML = `<iframe class="arch" sandbox="allow-scripts allow-popups" src="${fileUrl(p.id, p.architecture)}"></iframe>`)
    : showRoadmap(main, p);
  main.className = kind === "doc" ? "card md" : "";
  render(false);
  gitPanel($("#pgit"), p.id);
  let first = true;
  stream(`/api/projects/${p.id}/watch`, changed => {
    if (first) { first = false; return; }  // initial snapshot
    if (current in changed) render(true);
    gitPanel($("#pgit"), p.id);
  });
  every(30000, () => gitPanel($("#pgit"), p.id));
}
const roadmapItem = it => `<li class="${it.done ? "done" : ""}"><span class="chk">${it.done ? "✓" : "○"}</span><div>${esc(it.text)}${it.date ? ` <span class="date">${esc(it.date)}</span>` : ""}${it.note ? `<div class="muted small">${esc(it.note)}</div>` : ""}</div></li>`;
async function showRoadmap(el, p) {
  const r = await (await fetch(`/api/projects/${p.id}/roadmap`)).json();
  if (!r.exists) { el.innerHTML = `<div class="note">No <code>${esc(r.path || "docs/ROADMAP.md")}</code> in this repo yet. Format: four headings <code>## Done</code>, <code>## Now</code>, <code>## Planned</code>, <code>## Ideas</code> with <code>- [ ] item — note (2026-10-09)</code> lines.</div>`; return; }
  el.innerHTML = `${r.problems.length ? `<div class="warnbox small"><b>${r.problems.length} line(s) don't fit the roadmap format</b> (shown here instead of silently dropped):<br>${r.problems.map(esc).join("<br>")}</div>` : ""}
    <div class="board">${Object.entries(r.columns).map(([c, items]) => `<section class="card"><h3>${c} <span class="muted">${items.length}</span></h3><ul class="items">${items.map(roadmapItem).join("") || `<li class="muted">—</li>`}</ul></section>`).join("")}</div>
    <p class="muted small">Source: <a href="${fileUrl(p.id, r.path)}" target="_blank">${esc(r.path)}</a> (re-read live on change)</p>`;
}

// ---------- Overview ----------
const seenKey = "mc.lastSeen";
const lastSeen = () => { try { return +localStorage.getItem(seenKey) || Date.now() / 1000 - 86400; } catch { return Date.now() / 1000 - 86400; } };

function viewOverview(el) {
  el.innerHTML = `<div id="ovburn"></div><div class="ovgrid"><div><h3>Running now</h3><div id="ovlive"></div>
    <h3 class="gap">Finished since you last looked <span class="muted small" id="ovseen"></span> <button id="markseen">Mark seen</button></h3><div id="ovnew"></div></div>
    <div><h3>Now, per project</h3><div id="ovnow" class="muted">loading…</div><div id="ovsrc"></div></div></div>`;
  $("#markseen").onclick = () => { try { localStorage.setItem(seenKey, Date.now() / 1000); } catch {} draw(); };
  const draw = () => {
    if (!RUNS) return;
    const rs = RUNS.runs, v = liveVast(), since = lastSeen();
    const burn = v.reduce((a, r) => a + r.dph, 0), accrued = v.reduce((a, r) => a + (r.cost || 0), 0);
    $("#ovburn").innerHTML = v.length ? `<div class="card burncard"><h2>${v.length} Vast box${v.length > 1 ? "es" : ""} running · <span class="money">${money(burn)}/h</span> <span class="muted">· ≈${money(accrued)} accrued · ≈${money(burn * 24)}/day if left on</span></h2>
      <table class="grid">${v.map(r => `<tr><td><a href="#/run/${r.id}">${esc(r.name)}</a></td><td>${esc(r.device)}</td><td class="money">${money(r.dph)}/h</td><td>up ${dur(r)}</td>
      <td class="${gpuIdle(r) ? "idle" : ""}">GPU ${pct(r.meta.gpu_util)}</td><td class="muted">${esc(r.meta.geolocation || "")}</td></tr>`).join("")}</table>
      <p class="muted small">Billing runs while the box exists, GPU busy or not. Stop/destroy stays manual for now.</p></div>`
      : `<div class="card"><h3>No Vast boxes running <span class="muted">· $0/h</span></h3></div>`;
    const live = rs.filter(r => LIVE.includes(r.status) && r.source !== "vast");
    $("#ovlive").innerHTML = live.length ? runsTable(live) : `<p class="muted">Nothing running locally or on Kaggle.</p>`;
    const fresh = rs.filter(r => r.ended && r.ended > since && !LIVE.includes(r.status)).sort((a, b) => b.ended - a.ended);
    $("#ovseen").textContent = `(since ${fmtT(since)})`;
    $("#ovnew").innerHTML = fresh.length ? runsTable(fresh) : `<p class="muted">Nothing new.</p>`;
    const bad = Object.entries(RUNS.sources || {}).filter(([, x]) => !x.ok);
    $("#ovsrc").innerHTML = bad.map(([k, x]) => `<p class="bad small">${esc(k)} poll failing: ${esc(x.error)}</p>`).join("");
  };
  onRuns(draw);
  every(5000, draw);
  projReady.then(() => Promise.all(PROJECTS.map(p => fetch(`/api/projects/${p.id}/roadmap`).then(r => r.json()).then(r => [p, r])))).then(prs => {
    $("#ovnow").classList.remove("muted");
    $("#ovnow").innerHTML = prs.map(([p, r]) => `<div class="card"><h3><a href="#/project/${p.id}/roadmap">${esc(p.name)}</a></h3>
      ${!r.exists ? `<p class="muted small">No roadmap file yet.</p>` : `<ul class="items">${r.columns.Now.map(roadmapItem).join("") || `<li class="muted">Nothing in Now.</li>`}</ul>`}</div>`).join("");
  });
}

// ---------- GitHub repo (not tracked) ----------
async function viewGitHub(el, full) {
  el.innerHTML = `<p class="muted">loading ${esc(full)} from GitHub…</p>`;
  const r = await fetch(`/api/github/${full}`);
  const d = await r.json();
  if (!r.ok) { el.innerHTML = `<p class="bad">${esc(d.detail)}</p>`; return; }
  const id = d.full.split("/")[1].toLowerCase().replace(/[^a-z0-9-]/g, "-");
  el.innerHTML = `<h2>${d.private ? "🔒 " : ""}${esc(d.full)} <span class="muted small">pushed ${isoAgo(d.pushed)}${d.branch ? " · " + esc(d.branch) : ""}</span>
      <a class="small" href="${esc(d.url)}" target="_blank" rel="noopener">GitHub ↗</a></h2>
    ${d.description ? `<p class="muted">${esc(d.description)}</p>` : ""}
    ${d.local ? `<div class="note">Cloned locally at <code>${esc(d.local)}</code> but not tracked. To give it docs, roadmap and runs here, add this to
      <code>/Volumes/T7/mission-control/projects.yaml</code> and restart the server:<pre>  - id: ${esc(id)}
    name: ${esc(d.full.split("/")[1])}
    path: ${esc(d.local)}
    docs: [README.md]
    roadmap: docs/ROADMAP.md</pre></div>` : `<div class="note">Not cloned on the T7, so this page shows GitHub's copy only.</div>`}
    <div class="projgrid gap"><div class="card md">${d.readme_html || `<p class="muted">No README.</p>`}</div>
    <aside class="card"><h3>Commits <span class="muted small">${esc(d.branch || "")}</span></h3>
      <ul class="commits">${d.commits.map(c => `<li><code>${esc(c.sha)}</code> ${esc(c.subject)} <span class="muted small">${isoAgo(c.date)}</span></li>`).join("") || `<li class="muted">No commits.</li>`}</ul></aside></div>`;
  // README HTML comes from GitHub with repo-relative links; point them at GitHub, never at this dashboard.
  const base = `${d.url}/blob/${d.branch || "HEAD"}/`;
  for (const x of el.querySelectorAll(".md a[href]")) {
    const h = x.getAttribute("href");
    if (h.startsWith("#")) continue;
    x.href = new URL(h, base).href; x.target = "_blank"; x.rel = "noopener";
  }
  for (const img of el.querySelectorAll(".md img[src]")) if (!/^(https?:|data:)/.test(img.getAttribute("src"))) img.src = new URL(img.getAttribute("src"), `${d.url}/raw/${d.branch || "HEAD"}/`).href;
}

// ---------- Router ----------
const routes = [
  [/^#\/runs$/, viewRuns],
  [/^#\/run\/(.+)$/, viewRun],
  [/^#\/devices$/, viewDevices],
  [/^#\/gh\/([\w.-]+\/[\w.-]+)$/, viewGitHub],
  [/^#\/project\/([\w-]+)(?:\/(doc|arch|roadmap)(?:\/(.+))?)?$/, viewProject],
  [/^#\/processes$/, viewProcesses],
  [/^#?\/?$/, viewOverview],
];
function route() {
  cleanups.forEach(f => f()); cleanups = [];
  const h = location.hash || "#/";
  for (const a of document.querySelectorAll("#nav > a")) a.classList.toggle("on", a.getAttribute("href") === h);
  $("#projmenu").classList.toggle("on", /^#\/(project|gh)\//.test(h));
  $("#projmenu").open = false;
  loadGH();
  for (const [re, fn] of routes) { const m = h.match(re); if (m) return fn($("#view"), ...m.slice(1)); }
  $("#view").innerHTML = `<p>Not found: ${esc(h)}</p>`;
}
addEventListener("hashchange", route);
route();
