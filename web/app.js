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

// Live streams owned by the current view; closed on navigation.
let streams = [], timers = [];
const every = (ms, fn) => timers.push(setInterval(fn, ms));  // cleared on navigation
function stream(url, onmsg) { const es = new EventSource(url); es.onmessage = e => onmsg(JSON.parse(e.data)); streams.push(es); return es; }

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
  el.innerHTML = `<h2>Devices</h2><div id="mac"></div>`;
  const hist = [];
  stream("/api/local/stream", s => { hist.push(s); if (hist.length > 90) hist.shift(); $("#mac").innerHTML = macCard(hist); });
}

function viewProcesses(el) {
  el.innerHTML = `<h2>Processes <span class="muted">heaviest local processes, tracked or not (2 s)</span></h2><div id="procs" class="muted">loading…</div>`;
  stream("/api/local/stream", s => { $("#procs").innerHTML = procTable(s.procs); });
}

// ---------- Runs ----------
const fmtDur = s => { if (s == null) return "–"; s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60); return h ? `${h}h${String(m).padStart(2, "0")}m` : m ? `${m}m${String(s % 60).padStart(2, "0")}s` : `${s}s`; };
const fmtT = t => t ? new Date(t * 1000).toLocaleString([], {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"}) : "–";
const money = v => v == null ? "–" : "$" + v.toFixed(2);
const badge = st => `<span class="badge ${esc(st)}">${esc(st)}</span>`;
const dur = r => r.started ? fmtDur((r.ended || Date.now() / 1000) - r.started) : "–";
const filt = JSON.parse(localStorage.getItem("mc.filter") || "{}");

function runsTable(rs) {
  return `<table class="grid"><thead><tr><th>Status</th><th>Run</th><th>Source</th><th>Project</th><th>Device</th><th>Started</th><th>Duration</th><th>Cost</th></tr></thead><tbody>
  ${rs.map(r => `<tr><td>${badge(r.status)}</td><td><a href="#/run/${encodeURIComponent(r.id)}">${esc(r.name)}</a></td><td>${esc(r.source)}</td><td>${esc(r.project || "")}</td>
  <td>${esc(r.device || "")}</td><td>${fmtT(r.started)}</td><td>${dur(r)}</td><td>${r.cost != null ? money(r.cost) + (r.dph ? ` <span class="muted">@${money(r.dph)}/h</span>` : "") : ""}</td></tr>`).join("") || `<tr><td colspan="8" class="muted">No runs.</td></tr>`}</tbody></table>`;
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
  stream("/api/runs/stream", d => { rs = d; draw(); });
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
  el.innerHTML = `<div id="rhead"></div><div id="rlog"></div>`;
  let logStarted = false, rs = [];
  const head = () => {
    const r = rs.find(x => x.id === id);
    if (!r) { $("#rhead").innerHTML = `<p>Unknown run ${esc(id)}</p>`; return; }
    $("#rhead").innerHTML = `<h2>${esc(r.name)} ${badge(r.status)} <span class="muted">${esc(r.source)}${r.project ? " · " + esc(r.project) : ""}</span></h2>
      <table class="kv"><tr><td>Started</td><td>${fmtT(r.started)}</td><td>Duration</td><td>${dur(r)}</td></tr>
      ${r.cmd ? `<tr><td>Command</td><td colspan="3"><code>${esc(r.cmd)}</code></td></tr>` : ""}
      ${r.pid ? `<tr><td>PID</td><td>${r.pid}</td><td>Exit</td><td>${r.exit_code ?? "–"}</td></tr>` : ""}</table>`;
    if (!logStarted) { logStarted = true; logViewer($("#rlog"), `/api/runs/${encodeURIComponent(id)}/log`); }
  };
  stream("/api/runs/stream", d => { rs = d; head(); });
  every(5000, () => rs.length && head());
}

// ---------- Router ----------
const routes = [
  [/^#\/runs$/, viewRuns],
  [/^#\/run\/(.+)$/, viewRun],
  [/^#\/devices$/, viewDevices],
  [/^#\/processes$/, viewProcesses],
  [/^#?\/?$/, viewDevices],  // overview lands in slice (g)
];
function route() {
  streams.forEach(s => s.close()); streams = []; timers.forEach(clearInterval); timers = [];
  const h = location.hash || "#/";
  for (const a of document.querySelectorAll("#nav a")) a.classList.toggle("on", a.getAttribute("href") === h.split("?")[0]);
  for (const [re, fn] of routes) { const m = h.match(re); if (m) return fn($("#view"), ...m.slice(1)); }
  $("#view").innerHTML = `<p>Not found: ${esc(h)}</p>`;
}
addEventListener("hashchange", route);
route();
