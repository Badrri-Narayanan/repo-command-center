import assert from "node:assert/strict";
import { describe, it } from "node:test";

import * as iv from "../../static/js/inspector-view.js";
import { repo } from "./fakes.js";

const PATCH = "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-<old>\n+<new>\n";
const commit = (overrides = {}) => ({
  hash: "a".repeat(40), short: "aaaaaaa", subject: "fix: thing", author: "Ada", email: "ada@x", ts: 1, rel: "1 day ago",
  refs: [], merge: false, body: "fix: thing", files: [{ path: "a.txt", status: "M" }], patch: PATCH, truncated: false, ...overrides,
});

describe("tabs and tools", () => {
  it("shows counts for changes and stashes, and marks the active tab", () => {
    const html = iv.tabsHTML("changes", repo("r", { unstaged: 2, untracked: 1, stashes: 3 }));
    assert.match(html, /class="tab on" data-itab="changes"[^>]*>Changes <b>3<\/b>/);
    assert.match(html, /Stashes <b>3<\/b>/);
    assert.doesNotMatch(iv.tabsHTML("history", { loading: true }), /<b>/);
    assert.doesNotMatch(iv.tabsHTML("history", undefined), /<b>/);
  });

  it("renders the history range picker and escapes the query", () => {
    const html = iv.historyToolsHTML("incoming", `"><x`);
    assert.match(html, /value="incoming" selected/);
    assert.match(html, /value="&quot;&gt;&lt;x"/);
    assert.match(iv.tagsToolsHTML("v1"), /value="v1"/);
    assert.match(iv.historyToolsHTML("head", "", "x@y"), /<select id="inspAuthor"[^>]*><option value="">All authors<\/option><option value="x@y" selected>/);
  });

  it("lists authors with you first and keeps an unknown selection", () => {
    const authors = [{ name: "Bob", email: "bob@x", count: 5 }, { name: "Ann", email: "Ann@x", count: 2 }];
    const html = iv.authorOptionsHTML({ authors, me: "ann@X" }, "bob@x");
    assert.match(html, /All authors \(2\)<\/option><option value="Ann@x" >Me · Ann \(2\)<\/option><option value="bob@x" selected>Bob · 5/);
    const lost = iv.authorOptionsHTML({ authors, me: null }, "gone@x");
    assert.doesNotMatch(lost, /Me ·/);
    assert.match(lost, /<option value="gone@x" selected>gone@x<\/option>$/);
    assert.equal(iv.authorOptionsHTML({ authors: [] }), `<option value="">All authors</option>`);
  });

  it("highlights search matches safely", () => {
    assert.equal(iv.highlight("Fix <b> fix", "fix"), "<mark>Fix</mark> &lt;b&gt; <mark>fix</mark>");
    assert.equal(iv.highlight("a<b", ""), "a&lt;b");
    assert.equal(iv.highlight("@ann did it", "@ann"), "@ann did it");
  });
});

describe("history", () => {
  it("lists commits with refs, merges and a load-more button", () => {
    const html = iv.commitListHTML({ commits: [commit({ refs: ["HEAD -> main", "origin/main", "tag: v1", "dev"], merge: true })], more: true }, "a".repeat(40));
    assert.match(html, /class="irow on"/);
    assert.match(html, /ref head">HEAD -&gt; main/);
    assert.match(html, /ref remote">origin\/main/);
    assert.match(html, /ref tag">tag: v1/);
    assert.match(html, /ref local">dev/);
    assert.match(html, /class="merge"/);
    assert.match(html, /data-more/);
  });

  it("explains an empty list", () => {
    assert.match(iv.commitListHTML({ commits: [], more: false, note: "this branch has no upstream" }), /no upstream/);
    assert.match(iv.commitListHTML({ commits: [], more: false }), /No commits match/);
  });

  it("links authors, highlights the query and shows the author filter", () => {
    const html = iv.commitListHTML({ commits: [commit()], more: false }, null, { query: "thing", author: "ADA@x" });
    assert.match(html, /Commits by <b>Ada<\/b>/);
    assert.match(html, /data-author="" /);
    assert.match(html, /<span class="alink" data-author="ada@x"[^>]*>Ada<\/span>/);
    assert.match(html, /fix: <mark>thing<\/mark>/);
    const none = iv.commitListHTML({ commits: [], more: false }, null, { author: "who@x" });
    assert.match(none, /Commits by <b>who@x<\/b>.*No commits match/s);
  });

  it("renders commit details with body, web link and an escaped diff", () => {
    const html = iv.commitDetailHTML(commit({ body: "fix: thing\n\nWhy it broke.", merge: true, refs: ["tag: v2"] }), "https://github.com/acme/api");
    assert.match(html, /Why it broke\./);
    assert.match(html, /data-url="https:\/\/github.com\/acme\/api\/commit\/a{40}"/);
    assert.match(html, /diff vs first parent/);
    assert.match(html, /data-author="ada@x">All commits by Ada/);
    assert.match(html, /&lt;new&gt;/);
    assert.doesNotMatch(html, /<new>/);
    assert.match(html, /data-view-file="a.txt" data-rev="a{40}"/);
    const plain = iv.commitDetailHTML(commit(), "https://git.internal/x");
    assert.doesNotMatch(plain, /Open on web|dbody/);
  });
});

describe("diffHTML", () => {
  it("adds a file index for several files and a truncation note", () => {
    const two = PATCH + PATCH.replaceAll("a.txt", "b.txt");
    const html = iv.diffHTML({ patch: two, truncated: true });
    assert.match(html, /2 file\(s\)/);
    assert.match(html, /data-jump="1"/);
    assert.match(html, /Diff truncated/);
    assert.doesNotMatch(iv.diffHTML({ patch: PATCH }), /data-jump/);
  });

  it("handles binaries, deleted files, worktree and non-viewable diffs", () => {
    const binary = iv.diffHTML({ patch: "diff --git a/x.png b/x.png\nBinary files differ\n" });
    assert.match(binary, /dstat m"[^>]*>B</);
    assert.match(binary, /Binary file/);
    const deleted = iv.diffHTML({ patch: "diff --git a/g b/g\ndeleted file mode 1\n@@ -1 +0,0 @@\n-x\n" });
    assert.doesNotMatch(deleted, /View file/);
    assert.match(iv.diffHTML({ patch: PATCH }, { worktree: true, rev: "abc" }), /data-rev=""/);
    assert.doesNotMatch(iv.diffHTML({ patch: PATCH }, { viewable: false }), /View file/);
    const renamed = iv.diffHTML({ patch: "diff --git a/o b/n\nrename from o\nrename to n\n" });
    assert.match(renamed, /o →/);
  });

  it("falls back to the file list when there is no textual patch", () => {
    assert.match(iv.diffHTML({ patch: "", files: [{ path: "m.sh", status: "M" }] }), /m\.sh[\s\S]*No textual changes/);
    assert.match(iv.diffHTML({ patch: "" }), /No changes/);
  });

  it("stops rendering lines past the budget", () => {
    const big = "diff --git a/a b/a\n@@ -1 +1,6001 @@\n" + "+x\n".repeat(6001) + PATCH;
    assert.match(iv.diffHTML({ patch: big }), /Not shown: diff too long/);
  });
});

describe("fileViewHTML", () => {
  it("numbers lines and says where the file comes from", () => {
    const html = iv.fileViewHTML({ path: "a.py", rev: "abcdef1234567", content: "one\n<two>\n", binary: false, truncated: true });
    assert.match(html, /at <code>abcdef1234<\/code>/);
    assert.match(html, /<td class="ln">2<\/td><td class="code">&lt;two&gt;/);
    assert.doesNotMatch(html, /<td class="ln">3/);
    assert.match(html, /File truncated/);
    assert.match(iv.fileViewHTML({ path: "a", rev: "", content: "", binary: true }), /in working tree[\s\S]*Binary file/);
  });
});

describe("changes", () => {
  const files = [{ path: "src/app.py", x: "M", y: "M" }, { path: "top.txt", x: "?", y: "?" }];

  it("groups files and selects by key", () => {
    const html = iv.changesListHTML(files, "w:src/app.py");
    assert.match(html, /Staged <b>1<\/b>[\s\S]*Not staged <b>1<\/b>[\s\S]*Untracked <b>1<\/b>/);
    assert.match(html, /class="irow file on" data-key="w:src\/app.py"/);
    assert.match(html, /<span class="imeta">src<\/span>/);
    assert.match(iv.changesListHTML([], null), /Working tree clean/);
  });

  it("finds a change by key", () => {
    assert.equal(iv.findChange(files, "s:src/app.py").staged, true);
    assert.equal(iv.findChange(files, "s:nope"), null);
  });
});

describe("stashes", () => {
  const stashes = [{ index: 0, ref: "stash@{0}", message: "On main: wip", branch: "main", rel: "now" },
    { index: 1, ref: "stash@{1}", message: "odd", branch: null, rel: "1d" }];

  it("lists entries and renders the detail with apply/drop", () => {
    const list = iv.stashListHTML(stashes, "1");
    assert.match(list, /class="irow on" data-key="1"/);
    assert.match(list, /⎇ main/);
    assert.match(iv.stashListHTML([], null), /No stash entries/);
    const detail = iv.stashDetailHTML({ patch: PATCH }, stashes[0]);
    assert.match(detail, /data-stash-op="stash_apply"[\s\S]*data-stash-op="stash_drop"/);
    assert.match(detail, /made on ⎇ main/);
    assert.doesNotMatch(detail, /View file/);
    assert.doesNotMatch(iv.stashDetailHTML({ patch: "" }, stashes[1]), /made on/);
  });
});

describe("tags", () => {
  const data = { latest: "v2", tags: [
    { name: "v2", annotated: true, commit: "b".repeat(40), rel: "now", subject: "Release 2" },
    { name: "v1", annotated: false, commit: "c".repeat(40), rel: "1y", subject: "" }] };

  it("lists, filters and marks the latest tag", () => {
    const html = iv.tagListHTML(data, "v1");
    assert.match(html, /latest on HEAD/);
    assert.match(html, /annotated · <code>bbbbbbb/);
    assert.match(html, /class="irow on" data-key="v1"/);
    assert.doesNotMatch(iv.tagListHTML(data, null, "release"), /data-key="v1"/);
    assert.match(iv.tagListHTML(data, null, "zzz"), /No tags match/);
    assert.match(iv.tagListHTML({ tags: [], latest: null }, null), /No tags in this repo/);
  });

  it("renders the tag header with a forge link when known", () => {
    assert.match(iv.tagHeaderHTML(data.tags[0], "https://gitlab.com/a/b"), /annotated tag: Release 2[\s\S]*\/-\/tags\/v2/);
    assert.doesNotMatch(iv.tagHeaderHTML(data.tags[1], null), /Open on web/);
  });
});
