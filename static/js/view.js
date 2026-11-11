import { forgeFor, forgeLinks } from "./forge.js";
import { trackedChanges } from "./model.js";
import { esc, hostOf, timeAgo } from "./util.js";

const BUSY_VEIL = `<div class="busy-veil"><div class="spinner"></div></div>`;
const BRANCH_LIST_LIMIT = 80;

export function tileHTML(status, store) {
  const name = esc(status.name);
  const header = tileHeader(status, store);
  if (status.loading) return `<article class="tile" data-name="${name}">${header}<div class="skeleton"></div></article>`;
  const now = store.now();
  return `<article class="${tileClasses(status, store)}" data-name="${name}">${header}
    <div class="row">${branchPill(status)}${badges(status).join("")}</div>
    ${commitLines(status, now)}${resultLine(store.results.get(status.name), store)}
    ${tileActions(status, store.openers)}
    ${store.busy.has(status.name) ? BUSY_VEIL : ""}</article>`;
}

function tileHeader(status, store) {
  const name = esc(status.name);
  const pinned = store.pins.has(status.name);
  return `<header><input type="checkbox" class="sel" ${store.selected.has(status.name) ? "checked" : ""} aria-label="Select ${name}">
    <h3 title="${esc(status.path || status.name)}">${name}</h3>
    <button class="pin ${pinned ? "on" : ""}" data-act="pin" title="Pin to top">${pinned ? "★" : "☆"}</button></header>`;
}

export function tileClasses(status, store) {
  const classes = ["tile"];
  if (store.selected.has(status.name)) classes.push("selected");
  if (status.error || status.conflicts) classes.push("bad");
  else if (status.behind || trackedChanges(status)) classes.push("attn");
  return classes.join(" ");
}

export function badges(status) {
  if (status.error) return [`<span class="badge err" title="${esc(status.error)}">error</span>`];
  const upstream = esc(status.upstream);
  const changed = status.staged + status.unstaged;
  const list = [
    status.conflicts && `<span class="badge err">⚠ ${status.conflicts} conflicts</span>`,
    status.behind && `<span class="badge behind" title="Behind ${upstream}">↓ ${status.behind}</span>`,
    status.ahead && `<span class="badge ahead" title="Ahead of ${upstream} (unpushed)">↑ ${status.ahead}</span>`,
    changed && `<span class="badge dirty" title="${status.staged} staged, ${status.unstaged} unstaged">± ${changed}</span>`,
    status.untracked && `<span class="badge" title="Untracked files">? ${status.untracked}</span>`,
    status.stashes && `<span class="badge stash" title="Stash entries">⧉ ${status.stashes}</span>`,
    !status.upstream && !status.detached && `<span class="badge" title="Branch has no upstream">no upstream</span>`,
  ].filter(Boolean);
  return list.length ? list : [`<span class="badge clean">✓ clean</span>`];
}

export function branchPill(status) {
  if (status.error) return "";
  const kind = status.detached ? "detached" : status.branch === status.default ? "default" : "other";
  const label = status.detached ? "detached HEAD" : status.branch;
  return `<span class="branch ${kind}" title="default: ${esc(status.default)}">⎇ ${esc(label)}</span>`;
}

function commitLines(status, nowMs) {
  const commit = status.commit;
  if (!commit) return `<p class="meta">${esc(status.error || "no commits")}</p>`;
  return `<p class="commit" title="${esc(commit.subject)}">${esc(commit.subject)}</p>
    <p class="meta"><code>${esc(commit.hash)}</code> · ${esc(commit.author)} · ${esc(commit.rel)} · fetched ${timeAgo(status.fetchedAt, nowMs)}</p>`;
}

function resultLine(result, store) {
  if (!result) return "";
  const text = `${esc(store.opLabel(result.op))}: ${esc(result.message)}`;
  return `<div class="result ${esc(result.kind)}" title="${esc(result.message)}">${text}</div>`;
}

function tileActions(status, openers) {
  const defaultBranch = esc(status.default || "main");
  const editors = openers.filter(o => o.kind === "editor")
    .map(o => `<button class="icon" data-op="open_${esc(o.id)}" title="Open in ${esc(o.label)}">${esc(o.short)}</button>`);
  const web = status.web
    ? `<button class="icon" data-act="web" title="Open on ${esc(hostOf(status.web))}">${forgeFor(status.web).short}</button>` : "";
  return `<footer class="actions">
      <button class="btn" data-op="pull" title="git pull --ff-only">Pull</button>
      <button class="btn" data-op="switch_default" title="git switch ${defaultBranch}">→ ${defaultBranch}</button>
      <button class="btn" data-op="fetch">Fetch</button>
      <button class="btn" data-act="branches" data-pop>Branch ▾</button>
      <span class="spacer"></span>${editors.join("")}${web}
      <button class="icon" data-act="more" data-pop title="More actions">⋯</button>
    </footer>`;
}

export function emptyStateHTML(store) {
  if (store.names.length) return `<div class="empty">No repos match this view.</div>`;
  return `<div class="empty">No git repositories found in <code>${esc(store.root)}</code> (searched ${store.depth} folder level(s) deep).<br>
    Click the folder path at the top to pick another folder or search deeper.</div>`;
}

export function chipsHTML(chips) {
  return chips.map(chip =>
    `<button class="chip ${chip.active ? "on" : ""}" data-filter="${chip.key}">${chip.label}<b>${chip.count}</b></button>`).join("");
}

export function popItem(attrs, label, hint = "", cls = "") {
  return `<button class="pop-item ${cls}" ${attrs}><span>${label}</span><small>${hint}</small></button>`;
}

export const BRANCH_PICKER_HTML =
  `<input class="pop-filter" placeholder="Filter branches…"><div class="pop-list"><div class="muted">Loading…</div></div>`;

export function branchListHTML(branches, current, query = "") {
  const needle = query.toLowerCase();
  const items = branches.filter(b => b.name.toLowerCase().includes(needle)).slice(0, BRANCH_LIST_LIMIT)
    .map(b => popItem(`data-branch="${esc(b.name)}" title="${esc(b.name)}"`,
      (b.name === current ? "● " : "") + esc(b.name), `${b.local ? "" : "remote · "}${esc(b.rel)}`));
  return items.join("") || `<div class="muted">No match</div>`;
}

/** The ⋯ menu. `status` is the repo for a single tile, or null for a bulk selection. */
export function moreMenuHTML(openers, status) {
  const open = openers.map(o => popItem(`data-op="open_${esc(o.id)}"`, `Open in ${esc(o.label)}`)).join("");
  return `<div class="pop-list">
    ${popItem('data-op="stash"', "Stash changes", "incl. untracked")}
    ${popItem('data-op="stash_pop"', "Pop latest stash")}
    ${popItem('data-op="prune_merged"', "Prune merged local branches", "git branch -d", "danger")}
    <div class="pop-sep"></div>${open}${status ? singleRepoItems(status) : ""}
  </div>`;
}

function singleRepoItems(status) {
  const links = status.web ? forgeLinks(status.web, status.branch)
    .map(link => popItem(`data-url="${esc(link.url)}"`, `${link.label} ↗`)).join("") : "";
  return `<div class="pop-sep"></div>${popItem('data-copy="path"', "Copy path")}${links}${popItem('data-op="refresh"', "Refresh status")}`;
}

export function checkoutFormHTML(count) {
  return `<input class="pop-filter" placeholder="Branch name, e.g. develop or feat/X-1">
    <button class="btn primary" id="doCheckout">Checkout on ${count} repo(s)</button>
    <div class="muted">Repos without that branch are skipped; repos with uncommitted changes are skipped.</div>`;
}

export function rootPickerHTML(root, depth, recent, maxDepth = 4) {
  const options = Array.from({ length: maxDepth }, (_, i) => i + 1)
    .map(d => `<option value="${d}" ${d === depth ? "selected" : ""}>${d}</option>`).join("");
  const recentList = recent.length
    ? `<div class="pop-sep"></div><div class="muted">Recent</div><div class="pop-list">${
      recent.map(r => popItem(`data-root="${esc(r)}" title="${esc(r)}"`, esc(r))).join("")}</div>` : "";
  return `<input class="pop-filter" value="${esc(root)}" placeholder="/path/to/folder or ~/code" spellcheck="false">
    <label class="pop-row">Look <select id="depthSel">${options}</select> folder level(s) deep for repos</label>
    <button class="btn primary" id="goRoot">Open folder</button>${recentList}`;
}

export function logEntryHTML(entry, label, time) {
  const output = entry.output ? `<details><summary>output</summary><pre>${esc(entry.output)}</pre></details>` : "";
  return `<time>${esc(time)}</time><b>${esc(entry.name)}</b><span class="op">${esc(label)}</span><span class="msg">${esc(entry.message)}</span>${output}`;
}
