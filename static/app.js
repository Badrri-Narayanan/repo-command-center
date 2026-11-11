const TOKEN = document.querySelector('meta[name="rcc-token"]').content;
const $ = (s, el = document) => el.querySelector(s);
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ls = { get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
             set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} } };

const OP_LABEL = { fetch: "Fetch", pull: "Pull", switch_default: "Switch to default", checkout: "Checkout",
  sync: "Morning sync", stash: "Stash", stash_pop: "Stash pop", prune_merged: "Prune merged branches" };
const opLabel = op => OP_LABEL[op] || (op.startsWith("open_")
  ? "Open in " + (state.openers.find(o => "open_" + o.id === op)?.label || op.slice(5)) : op);
const CONFIRM = { stash: true, prune_merged: true, stash_pop: true };

const state = {
  root: "", names: [], repos: new Map(), selected: new Set(), busy: new Set(), last: new Map(),
  openers: [], depth: 1, filter: "all", q: "", sort: ls.get("rcc.sort", "attention"), pins: new Set(ls.get("rcc.pins", [])), loadedAt: 0,
};

async function api(path, body) {
  const r = await fetch(path, { method: body ? "POST" : "GET", headers: { "X-RCC-Token": TOKEN, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}
async function pool(items, limit, fn) {
  const q = [...items];
  await Promise.all(Array.from({ length: Math.min(limit, q.length) }, async () => { while (q.length) await fn(q.shift()); }));
}
function ago(ts) {
  if (!ts) return "never";
  const s = Date.now() / 1000 - ts;
  for (const [n, u] of [[86400 * 365, "y"], [86400 * 30, "mo"], [86400, "d"], [3600, "h"], [60, "m"]]) if (s >= n) return `${Math.floor(s / n)}${u} ago`;
  return "just now";
}

const host = u => { try { return new URL(u).host; } catch { return ""; } };
function forge(web) {  // per-host links for the GH/GL/BB button and the ⋯ menu
  const h = host(web);
  if (h.includes("github")) return { short: "GH", links: [["Pull requests", "/pulls"], ["Actions", "/actions"],
    ["Open PR for this branch", b => `/compare/${b}?expand=1`]] };
  if (h.includes("gitlab")) return { short: "GL", links: [["Merge requests", "/-/merge_requests"], ["Pipelines", "/-/pipelines"],
    ["Open MR for this branch", b => `/-/merge_requests/new?merge_request%5Bsource_branch%5D=${b}`]] };
  if (h.includes("bitbucket")) return { short: "BB", links: [["Pull requests", "/pull-requests"], ["Pipelines", "/pipelines"],
    ["Open PR for this branch", b => `/pull-requests/new?source=${b}`]] };
  return { short: "↗", links: [] };
}

const ok = s => s && !s.loading && !s.error;
const changes = s => s.staged + s.unstaged + s.conflicts;
const isDirty = s => ok(s) && changes(s) + s.untracked > 0;
const offDefault = s => ok(s) && s.branch !== s.default;
const FILTERS = {
  all: ["All", () => true],
  selected: ["Selected", s => state.selected.has(s.name)],
  pinned: ["★ Pinned", s => state.pins.has(s.name)],
  dirty: ["Uncommitted", isDirty],
  behind: ["Behind", s => ok(s) && s.behind > 0],
  ahead: ["Unpushed", s => ok(s) && s.ahead > 0],
  offdefault: ["Off default", offDefault],
  stash: ["Has stash", s => ok(s) && s.stashes > 0],
  stale: ["Not fetched 24h+", s => ok(s) && (!s.fetchedAt || Date.now() / 1000 - s.fetchedAt > 86400)],
  error: ["Errors", s => s && !!s.error],
};
function attention(s) {
  if (!s || s.loading) return 0;
  if (s.error) return 1000;
  return s.conflicts * 100 + (s.behind ? 40 : 0) + (changes(s) ? 20 : 0) + (s.ahead ? 15 : 0) + (offDefault(s) ? 10 : 0) + (s.untracked ? 2 : 0);
}

function visible() {
  const q = state.q.toLowerCase(), pred = FILTERS[state.filter][1];
  const list = state.names.map(n => state.repos.get(n)).filter(s =>
    pred(s) && (!q || s.name.toLowerCase().includes(q) || (s.branch || "").toLowerCase().includes(q)));
  const key = { name: () => 0, recent: (a, b) => (b.commit?.ts || 0) - (a.commit?.ts || 0), attention: (a, b) => attention(b) - attention(a) }[state.sort];
  return list.sort((a, b) => (state.pins.has(b.name) - state.pins.has(a.name)) || key(a, b) || a.name.localeCompare(b.name));
}

function tileHTML(s) {
  const n = s.name, sel = state.selected.has(n);
  const head = `<header><input type="checkbox" class="sel" ${sel ? "checked" : ""} aria-label="Select ${esc(n)}">
    <h3 title="${esc(s.path || n)}">${esc(n)}</h3>
    <button class="pin ${state.pins.has(n) ? "on" : ""}" data-act="pin" title="Pin to top">${state.pins.has(n) ? "★" : "☆"}</button></header>`;
  if (s.loading) return `<article class="tile" data-name="${esc(n)}">${head}<div class="skeleton"></div></article>`;

  const badges = [];
  if (s.error) badges.push(`<span class="badge err" title="${esc(s.error)}">error</span>`);
  else {
    if (s.conflicts) badges.push(`<span class="badge err">⚠ ${s.conflicts} conflicts</span>`);
    if (s.behind) badges.push(`<span class="badge behind" title="Behind ${esc(s.upstream)}">↓ ${s.behind}</span>`);
    if (s.ahead) badges.push(`<span class="badge ahead" title="Ahead of ${esc(s.upstream)} (unpushed)">↑ ${s.ahead}</span>`);
    if (s.staged + s.unstaged) badges.push(`<span class="badge dirty" title="${s.staged} staged, ${s.unstaged} unstaged">± ${s.staged + s.unstaged}</span>`);
    if (s.untracked) badges.push(`<span class="badge" title="Untracked files">? ${s.untracked}</span>`);
    if (s.stashes) badges.push(`<span class="badge stash" title="Stash entries">⧉ ${s.stashes}</span>`);
    if (!s.upstream && !s.detached) badges.push(`<span class="badge" title="Branch has no upstream">no upstream</span>`);
    if (!badges.length) badges.push(`<span class="badge clean">✓ clean</span>`);
  }
  const bcls = s.detached ? "detached" : s.branch === s.default ? "default" : "other";
  const branch = s.error ? "" : `<span class="branch ${bcls}" title="default: ${esc(s.default)}">⎇ ${esc(s.detached ? "detached HEAD" : s.branch)}</span>`;
  const c = s.commit;
  const commit = c ? `<p class="commit" title="${esc(c.subject)}">${esc(c.subject)}</p>
    <p class="meta"><code>${esc(c.hash)}</code> · ${esc(c.author)} · ${esc(c.rel)} · fetched ${ago(s.fetchedAt)}</p>`
    : `<p class="meta">${esc(s.error || "no commits")}</p>`;
  const r = state.last.get(n);
  const res = r ? `<div class="result ${r.kind}" title="${esc(r.msg)}">${esc(opLabel(r.op))}: ${esc(r.msg)}</div>` : "";
  const cls = ["tile", sel && "selected", s.error || s.conflicts ? "bad" : (s.behind || changes(s)) && "attn"].filter(Boolean).join(" ");
  return `<article class="${cls}" data-name="${esc(n)}">${head}
    <div class="row">${branch}${badges.join("")}</div>${commit}${res}
    <footer class="actions">
      <button class="btn" data-op="pull" title="git pull --ff-only">Pull</button>
      <button class="btn" data-op="switch_default" title="git switch ${esc(s.default)}">→ ${esc(s.default || "main")}</button>
      <button class="btn" data-op="fetch">Fetch</button>
      <button class="btn" data-act="branches" data-pop>Branch ▾</button>
      <span class="spacer"></span>
      ${state.openers.filter(o => o.kind === "editor").map(o =>
        `<button class="icon" data-op="open_${esc(o.id)}" title="Open in ${esc(o.label)}">${esc(o.short)}</button>`).join("")}
      ${s.web ? `<button class="icon" data-act="web" title="Open on ${esc(host(s.web))}">${forge(s.web).short}</button>` : ""}
      <button class="icon" data-act="more" data-pop title="More actions">⋯</button>
    </footer>
    ${state.busy.has(n) ? `<div class="busy-veil"><div class="spinner"></div></div>` : ""}</article>`;
}

const grid = $("#grid");
function render() {
  const v = visible();
  grid.innerHTML = v.length ? v.map(tileHTML).join("") : `<div class="empty">${state.names.length
    ? "No repos match this view."
    : `No git repositories found in <code>${esc(state.root)}</code> (searched ${state.depth} folder level(s) deep).<br>
       Click the folder path at the top to pick another folder or search deeper.`}</div>`;
  renderChrome();
}
function updateTile(n) {
  const el = grid.querySelector(`[data-name="${CSS.escape(n)}"]`);
  if (el) el.outerHTML = tileHTML(state.repos.get(n));
  renderChrome();
}
function renderChrome() {
  const all = state.names.map(n => state.repos.get(n));
  $("#chips").innerHTML = Object.entries(FILTERS).map(([k, [label, pred]]) => {
    const count = all.filter(pred).length;
    if (count === 0 && !["all", state.filter].includes(k)) return "";
    return `<button class="chip ${state.filter === k ? "on" : ""}" data-filter="${k}">${label}<b>${count}</b></button>`;
  }).join("");
  const v = visible(), selVisible = v.filter(s => state.selected.has(s.name)).length;
  const sa = $("#selAll");
  sa.checked = v.length > 0 && selVisible === v.length;
  sa.indeterminate = selVisible > 0 && selVisible < v.length;
  $("#selCount").textContent = state.selected.size;
  document.querySelectorAll("#bulk .btn").forEach(b => b.disabled = state.selected.size === 0);
}

async function refreshOne(n) {
  try { state.repos.set(n, await api(`/api/status?repo=${encodeURIComponent(n)}`)); }
  catch (e) { state.repos.set(n, { name: n, error: e.message }); }
  updateTile(n);
}
async function load() {
  const { root, repos, openers, depth, home } = await api("/api/repos");
  Object.assign(state, { root, openers, depth });
  const pretty = home && (root + "/").startsWith(home + "/") ? "~" + root.slice(home.length) : root;
  $("#root").textContent = `${pretty} · ${repos.length} repos`;
  $("#root").title = `${root}: click to change folder`;
  ls.set("rcc.roots", [root, ...ls.get("rcc.roots", []).filter(x => x !== root)].slice(0, 8));
  for (const n of repos) if (!state.repos.has(n)) state.repos.set(n, { name: n, loading: true });
  state.names = repos;
  state.selected = new Set([...state.selected].filter(n => repos.includes(n)));
  render();
  await pool(repos, 10, refreshOne);
  state.loadedAt = Date.now();
  render(); // re-sort once everything is in
}

async function runOp(op, names, extra = {}) {
  names = names.filter(n => !state.busy.has(n));
  if (!names.length) return;
  const label = opLabel(op);
  if (CONFIRM[op] && !confirm(`${label} on ${names.length} repo(s)?\n\n${names.slice(0, 12).join("\n")}${names.length > 12 ? "\n…" : ""}`)) return;
  const tally = { ok: 0, skipped: 0, error: 0 };
  await pool(names, op.startsWith("open_") ? 2 : 6, async n => {
    state.busy.add(n); updateTile(n);
    try {
      const r = await api("/api/op", { op, repo: n, ...extra });
      if (r.status) state.repos.set(n, r.status);
      tally[r.result]++; record(n, op, r.result, r.message, r.output);
    } catch (e) { tally.error++; record(n, op, "error", e.message); }
    state.busy.delete(n); updateTile(n);
  });
  if (names.length > 1 || tally.error) toast(`${label}: ${tally.ok} ok · ${tally.skipped} skipped · ${tally.error} failed`);
}
function record(n, op, kind, msg, output) {
  if (!op.startsWith("open_")) state.last.set(n, { op, kind, msg });
  const li = document.createElement("li");
  li.className = kind;
  li.innerHTML = `<time>${new Date().toLocaleTimeString()}</time><b>${esc(n)}</b><span class="op">${esc(opLabel(op))}</span><span class="msg">${esc(msg)}</span>` +
    (output ? `<details><summary>output</summary><pre>${esc(output)}</pre></details>` : "");
  const list = $("#logList"); list.prepend(li);
  while (list.children.length > 400) list.lastChild.remove();
  $("#logLast").textContent = `— ${n}: ${msg}`;
}
let toastTimer;
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => t.hidden = true, 4000); }

const pop = $("#popover");
let popAnchor = null;
function openPop(anchor, html, width = 290) {
  pop.innerHTML = html; pop.hidden = false; pop.onclick = null; popAnchor = anchor;
  pop.style.width = `min(${width}px, calc(100vw - 16px))`; placePop();
}
function placePop() {  // open below the anchor, or above it when there is more room there
  const r = popAnchor.getBoundingClientRect(), below = innerHeight - r.bottom - 14, above = r.top - 14;
  const up = below < 240 && above > below;
  pop.style.maxHeight = Math.min(360, up ? above : below) + "px";
  pop.style.left = Math.max(8, Math.min(r.left, innerWidth - pop.offsetWidth - 8)) + "px";
  pop.style.top = (up ? r.top - pop.offsetHeight - 6 : r.bottom + 6) + "px";
}
const closePop = () => { pop.hidden = true; };
const item = (attrs, label, hint = "", cls = "") => `<button class="pop-item ${cls}" ${attrs}><span>${label}</span><small>${hint}</small></button>`;

async function branchPop(anchor, n) {
  openPop(anchor, `<input class="pop-filter" placeholder="Filter branches…"><div class="pop-list"><div class="muted">Loading…</div></div>`);
  const input = $(".pop-filter", pop); input.focus();
  try {
    const { current, branches } = await api(`/api/branches?repo=${encodeURIComponent(n)}`);
    const draw = () => {
      const q = input.value.toLowerCase();
      const m = branches.filter(b => b.name.toLowerCase().includes(q)).slice(0, 80);
      $(".pop-list", pop).innerHTML = m.map(b => item(`data-branch="${esc(b.name)}" title="${esc(b.name)}"`, (b.name === current ? "● " : "") + esc(b.name),
        `${b.local ? "" : "remote · "}${esc(b.rel)}`)).join("") || `<div class="muted">No match</div>`;
    };
    draw(); placePop(); input.oninput = draw;
    input.onkeydown = e => { if (e.key === "Enter") $("[data-branch]", pop)?.click(); };
    pop.onclick = e => { const b = e.target.closest("[data-branch]"); if (b) { closePop(); runOp("checkout", [n], { branch: b.dataset.branch }); } };
  } catch (e) { $(".pop-list", pop).innerHTML = `<div class="muted">${esc(e.message)}</div>`; }
}
function morePop(anchor, names, single) {
  const s = single && state.repos.get(names[0]);
  openPop(anchor, `<div class="pop-list">
    ${item('data-op="stash"', "Stash changes", "incl. untracked")}
    ${item('data-op="stash_pop"', "Pop latest stash")}
    ${item('data-op="prune_merged"', "Prune merged local branches", "git branch -d", "danger")}
    <div class="pop-sep"></div>
    ${state.openers.map(o => item(`data-op="open_${esc(o.id)}"`, `Open in ${esc(o.label)}`)).join("")}
    ${s ? `<div class="pop-sep"></div>
      ${item('data-copy="path"', "Copy path")}
      ${s.web ? forge(s.web).links.filter(([, u]) => typeof u === "string" || s.branch)
        .map(([label, u]) => item(`data-url="${esc(typeof u === "string" ? u : u(encodeURI(s.branch)))}"`, label + " ↗")).join("") : ""}
      ${item('data-op="refresh"', "Refresh status")}` : ""}
  </div>`);
  pop.onclick = async e => {
    const b = e.target.closest("button"); if (!b) return;
    closePop();
    if (b.dataset.op === "refresh") return refreshOne(names[0]);
    if (b.dataset.op) return runOp(b.dataset.op, names);
    if (b.dataset.copy) { await navigator.clipboard.writeText(s.path); toast("Path copied"); }
    if (b.dataset.url) window.open(s.web + b.dataset.url, "_blank", "noopener");
  };
}
function checkoutPop(anchor) {
  openPop(anchor, `<input class="pop-filter" placeholder="Branch name, e.g. develop or feat/X-1"><button class="btn primary" id="doCheckout">Checkout on ${state.selected.size} repo(s)</button>
    <div class="muted">Repos without that branch are skipped; repos with uncommitted changes are skipped.</div>`);
  const input = $(".pop-filter", pop); input.focus();
  const go = () => { const br = input.value.trim(); if (br) { closePop(); runOp("checkout", [...state.selected], { branch: br }); } };
  $("#doCheckout").onclick = go; input.onkeydown = e => e.key === "Enter" && go();
}

function rootPop(anchor) {
  const recent = ls.get("rcc.roots", []).filter(x => x !== state.root);
  openPop(anchor, `<input class="pop-filter" value="${esc(state.root)}" placeholder="/path/to/folder or ~/code" spellcheck="false">
    <label class="pop-row">Look <select id="depthSel">${[1, 2, 3, 4].map(d =>
      `<option value="${d}" ${d === state.depth ? "selected" : ""}>${d}</option>`).join("")}</select> folder level(s) deep for repos</label>
    <button class="btn primary" id="goRoot">Open folder</button>
    ${recent.length ? `<div class="pop-sep"></div><div class="muted">Recent</div><div class="pop-list">${recent.map(x => item(`data-root="${esc(x)}" title="${esc(x)}"`, esc(x))).join("")}</div>` : ""}`, 460);
  const input = $(".pop-filter", pop); input.focus(); input.select();
  const go = async path => {
    try {
      await api("/api/root", { path, depth: +$("#depthSel").value });
      closePop();
      Object.assign(state, { repos: new Map(), selected: new Set(), last: new Map(), busy: new Set(), filter: "all" });
      await load();
    } catch (e) { toast(e.message); }
  };
  $("#goRoot").onclick = () => go(input.value.trim());
  input.onkeydown = e => { if (e.key === "Enter") go(input.value.trim()); };
  pop.onclick = e => { const x = e.target.closest("[data-root]"); if (x) go(x.dataset.root); };
}

grid.addEventListener("click", e => {
  const tile = e.target.closest(".tile"); if (!tile) return;
  const n = tile.dataset.name, s = state.repos.get(n);
  const btn = e.target.closest("button, input.sel");
  if (!btn) return;
  if (btn.matches("input.sel")) { btn.checked ? state.selected.add(n) : state.selected.delete(n); return updateTile(n); }
  if (btn.dataset.op) return runOp(btn.dataset.op, [n]);
  switch (btn.dataset.act) {
    case "pin": state.pins.has(n) ? state.pins.delete(n) : state.pins.add(n); ls.set("rcc.pins", [...state.pins]); return render();
    case "branches": return branchPop(btn, n);
    case "more": return morePop(btn, [n], true);
    case "web": return window.open(s.web, "_blank", "noopener");
  }
});
grid.addEventListener("dblclick", e => {  // double-click empty tile space to toggle selection
  const tile = e.target.closest(".tile");
  if (!tile || e.target.closest("button, input")) return;
  const n = tile.dataset.name; state.selected.has(n) ? state.selected.delete(n) : state.selected.add(n); updateTile(n);
});
$("#chips").addEventListener("click", e => { const c = e.target.closest("[data-filter]"); if (c) { state.filter = c.dataset.filter; render(); } });
$("#q").addEventListener("input", e => { state.q = e.target.value; render(); });
$("#sort").value = state.sort;
$("#sort").addEventListener("change", e => { state.sort = e.target.value; ls.set("rcc.sort", state.sort); render(); });
$("#selAll").addEventListener("change", e => { for (const s of visible()) e.target.checked ? state.selected.add(s.name) : state.selected.delete(s.name); render(); });
$("#refreshAll").addEventListener("click", () => load());
$("#root").addEventListener("click", e => rootPop(e.currentTarget));
$("#fetchAll").addEventListener("click", () => runOp("fetch", state.names));
$("#bulk").addEventListener("click", e => {
  const b = e.target.closest("[data-bulk]"); if (!b || b.disabled) return;
  const names = [...state.selected];
  if (b.dataset.bulk === "checkout") return checkoutPop(b);
  if (b.dataset.bulk === "more") return morePop(b, names, false);
  runOp(b.dataset.bulk, names);
});
document.addEventListener("click", e => { if (!pop.hidden && !pop.contains(e.target) && !e.target.closest("[data-pop]")) closePop(); });
const help = $("#help");
function openHelp(tab) {
  closePop();
  if (tab) showTab(tab);
  $("#helpAgain").checked = ls.get("rcc.helpSeen", false);
  if (!help.open) help.showModal();
}
function showTab(t) {
  help.querySelectorAll(".tab").forEach(b => b.classList.toggle("on", b.dataset.tab === t));
  help.querySelectorAll("[data-panel]").forEach(p => p.hidden = p.dataset.panel !== t);
}
$("#helpBtn").addEventListener("click", () => openHelp());
help.addEventListener("click", e => {
  if (e.target === help || e.target.closest("[data-close]")) return help.close(); // backdrop click or Close
  const t = e.target.closest("[data-tab]"); if (t) showTab(t.dataset.tab);
});
$("#helpAgain").addEventListener("change", e => ls.set("rcc.helpSeen", e.target.checked));
if (!ls.get("rcc.helpSeen", false)) setTimeout(() => openHelp("start"), 300);

document.addEventListener("keydown", e => {
  if (help.open) return; // the dialog handles its own Esc
  const typing = e.target.matches("input, select, textarea");
  if (e.key === "Escape") { if (!pop.hidden) return closePop(); if (typing) return e.target.blur(); state.selected.clear(); return render(); }
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "?") openHelp();
  else if (e.key === "/") { e.preventDefault(); $("#q").focus(); }
  else if (e.key === "a") { visible().forEach(s => state.selected.add(s.name)); render(); }
  else if (e.key === "r") load();
});
// Re-read status when you come back to the tab (cheap, local-only - no network fetch).
window.addEventListener("focus", () => { if (state.loadedAt && Date.now() - state.loadedAt > 30000 && !state.busy.size) load(); });

load().catch(e => { grid.innerHTML = `<div class="empty">Failed to load: ${esc(e.message)}</div>`; });
