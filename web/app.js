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
let streams = [];
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

// ---------- Router ----------
const routes = [
  [/^#\/devices$/, viewDevices],
  [/^#\/processes$/, viewProcesses],
  [/^#?\/?$/, viewDevices],  // overview lands in slice (g)
];
function route() {
  streams.forEach(s => s.close()); streams = [];
  const h = location.hash || "#/";
  for (const a of document.querySelectorAll("#nav a")) a.classList.toggle("on", a.getAttribute("href") === h.split("?")[0]);
  for (const [re, fn] of routes) { const m = h.match(re); if (m) return fn($("#view"), ...m.slice(1)); }
  $("#view").innerHTML = `<p>Not found: ${esc(h)}</p>`;
}
addEventListener("hashchange", route);
route();
