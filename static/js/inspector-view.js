import { changeKey, groupChanges, parsePatch, totals } from "./diff.js";
import { forgeObjectUrl } from "./forge.js";
import { esc } from "./util.js";

export const TABS = [
  { key: "history", label: "History", hint: "1" },
  { key: "changes", label: "Changes", hint: "2" },
  { key: "stashes", label: "Stashes", hint: "3" },
  { key: "tags", label: "Tags", hint: "4" },
];
export const RANGES = [
  { key: "head", label: "This branch" },
  { key: "all", label: "All branches" },
  { key: "incoming", label: "↓ Incoming (what Pull brings)" },
  { key: "outgoing", label: "↑ Outgoing (unpushed)" },
];
const MAX_DIFF_LINES = 6000;
const STATUS_NAMES = { A: "added", M: "modified", D: "deleted", R: "renamed", C: "copied", T: "type changed", U: "conflict", "?": "untracked" };

const tabCount = (key, status) => {
  if (!status || status.loading || status.error) return 0;
  if (key === "changes") return status.staged + status.unstaged + status.untracked + status.conflicts;
  if (key === "stashes") return status.stashes;
  return 0;
};

export function tabsHTML(active, status) {
  return TABS.map(({ key, label, hint }) => {
    const count = tabCount(key, status);
    return `<button class="tab ${key === active ? "on" : ""}" data-itab="${key}" title="${label} (${hint})">${label}${count ? ` <b>${count}</b>` : ""}</button>`;
  }).join("");
}

export function emptyHTML(message) {
  return `<div class="iempty">${esc(message)}</div>`;
}

export const LOADING_HTML = `<div class="iempty"><div class="spinner"></div></div>`;

export function historyToolsHTML(range, query, author = "") {
  const options = RANGES.map(r => `<option value="${r.key}" ${r.key === range ? "selected" : ""}>${esc(r.label)}</option>`).join("");
  return `<select id="inspRange" title="Which commits to list">${options}</select>
    <select id="inspAuthor" title="Only commits by this author">${authorOptionsHTML({ authors: [] }, author)}</select>
    <input type="search" id="inspQuery" value="${esc(query)}" placeholder="Search messages · @author   ( / )" spellcheck="false">`;
}

/** "All authors", then you (from git config user.email), then everyone else by commit count. */
export function authorOptionsHTML({ authors, me }, selected = "") {
  const lower = email => (email || "").toLowerCase();
  const mine = authors.find(a => lower(a.email) === lower(me));
  const others = authors.filter(a => a !== mine);
  const option = (a, label) => `<option value="${esc(a.email)}" ${lower(a.email) === lower(selected) ? "selected" : ""}>${esc(label)}</option>`;
  const known = authors.some(a => lower(a.email) === lower(selected));
  return [
    `<option value="">All authors${authors.length ? ` (${authors.length})` : ""}</option>`,
    mine ? option(mine, `Me · ${mine.name} (${mine.count})`) : "",
    ...others.map(a => option(a, `${a.name} · ${a.count}`)),
    selected && !known ? option({ email: selected }, selected) : "",
  ].join("");
}

/** Escapes `text` and wraps case-insensitive matches of `query` in <mark>. `@author` queries aren't highlighted. */
export function highlight(text, query) {
  const needle = (query || "").trim().toLowerCase();
  if (!needle || needle.startsWith("@")) return esc(text);
  const hay = text.toLowerCase();
  let out = "";
  let at = 0;
  for (let hit = hay.indexOf(needle); hit >= 0; hit = hay.indexOf(needle, at)) {
    out += `${esc(text.slice(at, hit))}<mark>${esc(text.slice(hit, hit + needle.length))}</mark>`;
    at = hit + needle.length;
  }
  return out + esc(text.slice(at));
}

const authorLink = (name, email) =>
  `<span class="alink" data-author="${esc(email)}" title="Only commits by ${esc(name)} &lt;${esc(email)}&gt;">${esc(name)}</span>`;

function refChips(refs) {
  return refs.map(ref => {
    const kind = ref.startsWith("tag: ") ? "tag" : ref.startsWith("HEAD") ? "head" : ref.includes("/") ? "remote" : "local";
    return `<span class="ref ${kind}">${esc(ref)}</span>`;
  }).join("");
}

export function commitListHTML({ commits, more, note }, selected, { query = "", author = "" } = {}) {
  const who = author && commits.find(c => c.email.toLowerCase() === author.toLowerCase());
  const banner = author ? `<div class="ifilter">Commits by <b>${esc(who ? who.author : author)}</b>
      <button class="btn" data-author="" title="Show every author">✕ Clear</button></div>` : "";
  if (!commits.length) return banner + emptyHTML(note || "No commits match.");
  const items = commits.map(c => `<button class="irow ${c.hash === selected ? "on" : ""}" data-key="${esc(c.hash)}">
      <span class="isub">${c.merge ? `<span class="merge" title="Merge commit">⑂</span>` : ""}${highlight(c.subject, query)}</span>
      <span class="imeta"><code>${esc(c.short)}</code> · ${authorLink(c.author, c.email)} · ${esc(c.rel)}</span>
      ${c.refs.length ? `<span class="irefs">${refChips(c.refs)}</span>` : ""}</button>`).join("");
  const moreButton = more ? `<button class="btn imore" data-more>Load more…</button>` : "";
  return banner + items + moreButton;
}

export function commitDetailHTML(commit, webUrl) {
  const url = forgeObjectUrl(webUrl, "commit", commit.hash);
  const body = commit.body && commit.body !== commit.subject ? commit.body.split("\n").slice(1).join("\n").trim() : "";
  const date = new Date(commit.ts * 1000).toLocaleString();
  return `<div class="dhead">
      <h3>${esc(commit.subject)}</h3>
      ${body ? `<pre class="dbody">${esc(body)}</pre>` : ""}
      <p class="imeta"><code title="${esc(commit.hash)}">${esc(commit.short)}</code> · ${authorLink(commit.author, commit.email)} &lt;${esc(commit.email)}&gt; · <span title="${esc(date)}">${esc(commit.rel)}</span>${commit.merge ? " · merge (diff vs first parent)" : ""}</p>
      ${commit.refs.length ? `<p class="irefs">${refChips(commit.refs)}</p>` : ""}
      <p class="dactions"><button class="btn" data-copy="${esc(commit.hash)}">Copy hash</button><button class="btn" data-author="${esc(commit.email)}">All commits by ${esc(commit.author)}</button>${
        url ? `<button class="btn" data-url="${esc(url)}">Open on web ↗</button>` : ""}</p>
    </div>${diffHTML(commit, { rev: commit.hash })}`;
}

/** `bundle` is {files, patch, truncated} from the server. `rev` lets "View file" show that version. */
export function diffHTML(bundle, { rev = "", worktree = false, viewable = true } = {}) {
  const view = { rev, worktree, viewable };
  const files = parsePatch(bundle.patch);
  if (!files.length) {
    const names = (bundle.files || []).map(f => fileHeaderHTML({ ...f, add: 0, del: 0, hunks: [] }, view)).join("");
    return names ? `<div class="dfiles">${names}</div>${emptyHTML("No textual changes (binary, mode or empty).")}` : emptyHTML("No changes.");
  }
  const sum = totals(files);
  let budget = MAX_DIFF_LINES;
  const sections = files.map((file, i) => {
    const html = fileSectionHTML(file, i, view, budget);
    budget -= file.hunks.reduce((n, h) => n + h.lines.length, 0);
    return html;
  }).join("");
  const summary = `<div class="dsummary">${files.length} file(s) · <span class="add">+${sum.add}</span> <span class="del">−${sum.del}</span>
      <span class="spacer"></span><button class="btn" data-copy-patch>Copy diff</button></div>`;
  const index = files.length > 1 ? `<div class="dindex">${files.map((f, i) =>
    `<button class="dlink" data-jump="${i}"><span class="dstat ${statusClass(f)}">${statusLetter(f)}</span>${esc(f.path)}<small><span class="add">+${f.add}</span> <span class="del">−${f.del}</span></small></button>`).join("")}</div>` : "";
  const cut = bundle.truncated ? `<div class="tipline">Diff truncated: it's very large. Open the repo in your editor for the rest.</div>` : "";
  return summary + index + sections + cut;
}

const statusLetter = file => (file.binary && file.status === "M" ? "B" : file.status);
const statusClass = file => ({ A: "a", D: "d", R: "r", U: "u", "?": "a" }[file.status] || "m");

function fileHeaderHTML(file, { rev, worktree, viewable }) {
  const canView = viewable && file.status !== "D";
  const view = canView ? `<button class="btn" data-view-file="${esc(file.path)}" data-rev="${esc(worktree ? "" : rev)}">View file</button>` : "";
  const renamed = file.oldPath || file.orig ? `<small>${esc(file.oldPath || file.orig)} →</small> ` : "";
  return `<header class="dfhead"><span class="dstat ${statusClass(file)}" title="${STATUS_NAMES[file.status] || ""}">${statusLetter(file)}</span>
    <span class="dpath">${renamed}${esc(file.path)}</span>
    <small><span class="add">+${file.add}</span> <span class="del">−${file.del}</span></small>${view}</header>`;
}

function fileSectionHTML(file, index, view, budget) {
  let body;
  if (file.binary) body = emptyHTML("Binary file");
  else if (budget <= 0) body = emptyHTML("Not shown: diff too long. Use View file.");
  else body = `<table class="dtable">${file.hunks.map(hunkHTML).join("")}</table>`;
  return `<section class="dfile" id="dfile-${index}">${fileHeaderHTML(file, view)}${body}</section>`;
}

function hunkHTML(hunk) {
  const rows = hunk.lines.map(line =>
    `<tr class="${line.kind}"><td class="ln">${line.a ?? ""}</td><td class="ln">${line.b ?? ""}</td><td class="code">${esc(line.text) || " "}</td></tr>`);
  return `<tr class="hunk"><td class="ln"></td><td class="ln"></td><td class="code">${esc(hunk.header)}</td></tr>${rows.join("")}`;
}

export function fileViewHTML(file) {
  const where = file.rev ? `at <code>${esc(file.rev.slice(0, 10))}</code>` : "in working tree";
  const head = `<div class="dsummary"><button class="btn" data-back>← Back to diff</button><span class="dpath">${esc(file.path)}</span><small>${where} · read-only</small></div>`;
  if (file.binary) return head + emptyHTML("Binary file: not shown.");
  const lines = file.content.replace(/\n$/, "").split("\n");
  const rows = lines.map((text, i) => `<tr><td class="ln">${i + 1}</td><td class="code">${esc(text) || " "}</td></tr>`).join("");
  const cut = file.truncated ? `<div class="tipline">File truncated: it's larger than 1 MB.</div>` : "";
  return `${head}<table class="dtable ftable">${rows}</table>${cut}`;
}

const GROUP_LABELS = { conflicts: "Conflicts", staged: "Staged", unstaged: "Not staged", untracked: "Untracked" };

export function changesListHTML(files, selected) {
  const groups = groupChanges(files);
  const html = Object.entries(groups).filter(([, list]) => list.length).map(([group, list]) =>
    `<div class="igroup">${GROUP_LABELS[group]} <b>${list.length}</b></div>${list.map(file => {
      const key = changeKey(file);
      const slash = file.path.lastIndexOf("/");
      return `<button class="irow file ${key === selected ? "on" : ""}" data-key="${esc(key)}" title="${esc(file.path)}">
        <span class="isub"><span class="dstat ${statusClass(file)}">${esc(file.status)}</span>${esc(file.path.slice(slash + 1))}</span>
        ${slash > 0 ? `<span class="imeta">${esc(file.path.slice(0, slash))}</span>` : ""}</button>`;
    }).join("")}`).join("");
  return html || emptyHTML("Working tree clean. Nothing to show.");
}

export function findChange(files, key) {
  const groups = groupChanges(files);
  return Object.values(groups).flat().find(file => changeKey(file) === key) || null;
}

export function stashListHTML(stashes, selected) {
  if (!stashes.length) return emptyHTML("No stash entries.");
  return stashes.map(s => `<button class="irow ${String(s.index) === String(selected) ? "on" : ""}" data-key="${s.index}">
      <span class="isub">${esc(s.message)}</span>
      <span class="imeta"><code>${esc(s.ref)}</code>${s.branch ? ` · ⎇ ${esc(s.branch)}` : ""} · ${esc(s.rel)}</span></button>`).join("");
}

export function stashDetailHTML(stash, entry) {
  return `<div class="dhead"><h3>${esc(entry.message)}</h3>
      <p class="imeta"><code>${esc(entry.ref)}</code>${entry.branch ? ` · made on ⎇ ${esc(entry.branch)}` : ""} · ${esc(entry.rel)}</p>
      <p class="dactions">
        <button class="btn" data-stash-op="stash_apply" title="git stash apply: restore and keep the entry">Apply</button>
        <button class="btn danger" data-stash-op="stash_drop" title="git stash drop: delete this entry">Drop…</button>
      </p></div>${diffHTML(stash, { viewable: false })}`;
}

export function tagListHTML({ tags, latest }, selected, query = "") {
  const needle = query.toLowerCase();
  const shown = tags.filter(t => t.name.toLowerCase().includes(needle) || t.subject.toLowerCase().includes(needle));
  if (!shown.length) return emptyHTML(tags.length ? "No tags match." : "No tags in this repo.");
  return shown.map(t => `<button class="irow ${t.name === selected ? "on" : ""}" data-key="${esc(t.name)}">
      <span class="isub"><span class="ref tag">${esc(t.name)}</span>${t.name === latest ? `<span class="ref head" title="Most recent tag reachable from HEAD">latest on HEAD</span>` : ""}</span>
      <span class="imeta">${t.annotated ? "annotated" : "lightweight"} · <code>${esc(t.commit.slice(0, 7))}</code> · ${esc(t.rel)}</span>
      ${t.subject ? `<span class="imeta">${esc(t.subject)}</span>` : ""}</button>`).join("");
}

export function tagsToolsHTML(query) {
  return `<input type="search" id="inspQuery" value="${esc(query)}" placeholder="Filter tags…" spellcheck="false">`;
}

export function tagHeaderHTML(tag, webUrl) {
  const url = forgeObjectUrl(webUrl, "tag", tag.name);
  return `<div class="dhead tagged"><p><span class="ref tag">${esc(tag.name)}</span> ${tag.annotated ? `annotated tag: ${esc(tag.subject)}` : "lightweight tag"}${
    url ? ` <button class="btn" data-url="${esc(url)}">Open on web ↗</button>` : ""}</p></div>`;
}
