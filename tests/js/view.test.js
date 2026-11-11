import assert from "node:assert/strict";
import { describe, it } from "node:test";

import * as view from "../../static/js/view.js";
import { makeStore, repo } from "./fakes.js";

const EDITORS = [{ id: "vscode", label: "VS Code", short: "VS", kind: "editor" },
  { id: "terminal", label: "Terminal", short: ">_", kind: "system" }];

describe("tileHTML", () => {
  it("renders a loading skeleton", () => {
    const html = view.tileHTML({ name: "a", loading: true }, makeStore());
    assert.match(html, /class="skeleton"/);
    assert.doesNotMatch(html, /data-op="pull"/);
  });

  it("renders branch, commit, actions, editors and forge button", () => {
    const store = makeStore([repo("svc")], { openers: EDITORS });
    const html = view.tileHTML(store.get("svc"), store);
    assert.match(html, /class="branch default"[^>]*>⎇ main</);
    assert.match(html, /work on svc/);
    assert.match(html, /fetched 1h ago/);
    assert.match(html, /data-op="open_vscode"[^>]*>VS</);
    assert.doesNotMatch(html, /open_terminal/);
    assert.match(html, /data-act="web"[^>]*>GH</);
    assert.match(html, /→ main/);
  });

  it("escapes repo data so it cannot inject markup", () => {
    const evil = repo(`<img src=x onerror=alert(1)>`, { branch: `"><script>`, commit: { hash: "h", subject: "<b>", author: "a", rel: "now", ts: 1 } });
    const store = makeStore([evil]);
    const html = view.tileHTML(store.get(evil.name), store);
    assert.doesNotMatch(html, /<img|<script|<b>/);
  });

  it("reflects selection, pin, busy state and the last result", () => {
    const store = makeStore([repo("a")]);
    store.toggleSelected("a");
    store.togglePin("a");
    store.busy.add("a");
    store.recordResult("a", "sync", "skipped", "skipped: 2 uncommitted change(s)");
    const html = view.tileHTML(store.get("a"), store);
    assert.match(html, /class="tile selected"/);
    assert.match(html, /checked/);
    assert.match(html, /class="pin on"[^>]*>★/);
    assert.match(html, /busy-veil/);
    assert.match(html, /class="result skipped"[^>]*>Morning sync: skipped: 2 uncommitted/);
  });

  it("handles repos without commits or remotes", () => {
    const store = makeStore([repo("bare", { commit: null, web: null })]);
    const html = view.tileHTML(store.get("bare"), store);
    assert.match(html, /no commits/);
    assert.doesNotMatch(html, /data-act="web"/);
  });
});

describe("tileClasses", () => {
  const store = makeStore();
  it("flags errors and conflicts as bad, behind or changes as attention", () => {
    assert.equal(view.tileClasses(repo("a", { error: "x" }), store), "tile bad");
    assert.equal(view.tileClasses(repo("a", { conflicts: 1 }), store), "tile bad");
    assert.equal(view.tileClasses(repo("a", { behind: 1 }), store), "tile attn");
    assert.equal(view.tileClasses(repo("a", { staged: 1 }), store), "tile attn");
    assert.equal(view.tileClasses(repo("a"), store), "tile");
  });
});

describe("badges", () => {
  it("shows clean when nothing is going on", () => {
    assert.deepEqual(view.badges(repo("a")).length, 1);
    assert.match(view.badges(repo("a"))[0], /✓ clean/);
  });

  it("shows only the error badge for errored repos", () => {
    const list = view.badges(repo("a", { error: "fatal", behind: 3 }));
    assert.equal(list.length, 1);
    assert.match(list[0], /title="fatal"/);
  });

  it("shows every applicable badge", () => {
    const list = view.badges(repo("a", {
      conflicts: 1, behind: 2, ahead: 3, staged: 1, unstaged: 1, untracked: 4, stashes: 5, upstream: null,
    })).join("");
    for (const text of ["⚠ 1 conflicts", "↓ 2", "↑ 3", "± 2", "? 4", "⧉ 5", "no upstream"]) {
      assert.ok(list.includes(text), text);
    }
  });

  it("does not flag a missing upstream on a detached HEAD", () => {
    assert.doesNotMatch(view.badges(repo("a", { detached: true, upstream: null })).join(""), /no upstream/);
  });
});

describe("branchPill", () => {
  it("distinguishes default, other and detached", () => {
    assert.match(view.branchPill(repo("a")), /branch default/);
    assert.match(view.branchPill(repo("a", { branch: "feat" })), /branch other/);
    assert.match(view.branchPill(repo("a", { detached: true, branch: null })), /branch detached[^>]*>⎇ detached HEAD/);
    assert.equal(view.branchPill(repo("a", { error: "x" })), "");
  });
});

describe("page fragments", () => {
  it("explains an empty folder versus an empty filter", () => {
    const store = makeStore();
    assert.match(view.emptyStateHTML(store), /No git repositories found in <code>\/code<\/code>/);
    assert.match(view.emptyStateHTML(makeStore([repo("a")])), /No repos match this view/);
  });

  it("renders chips with active state", () => {
    const html = view.chipsHTML([{ key: "all", label: "All", count: 3, active: true }, { key: "behind", label: "Behind", count: 1, active: false }]);
    assert.match(html, /class="chip on" data-filter="all">All<b>3<\/b>/);
    assert.match(html, /class="chip " data-filter="behind"/);
  });

  it("filters, marks and limits the branch list", () => {
    const branches = [{ name: "main", local: true, rel: "1 day ago" }, { name: "feat/x", local: false, rel: "2 days ago" }];
    const html = view.branchListHTML(branches, "main", "");
    assert.match(html, /● main/);
    assert.match(html, /remote · 2 days ago/);
    assert.doesNotMatch(view.branchListHTML(branches, "main", "FEAT"), /● main/);
    assert.match(view.branchListHTML(branches, "main", "zzz"), /No match/);
    const many = Array.from({ length: 120 }, (_, i) => ({ name: `b${i}`, local: true, rel: "" }));
    assert.equal(view.branchListHTML(many, "", "").match(/data-branch=/g).length, 80);
  });

  it("builds the ⋯ menu for one repo and for a bulk selection", () => {
    const single = view.moreMenuHTML(EDITORS, repo("svc", { branch: "feat/x" }));
    assert.match(single, /Copy path/);
    assert.match(single, /data-url="https:\/\/github.com\/acme\/svc\/compare\/feat\/x\?expand=1"/);
    assert.match(single, /Open in Terminal/);
    const bulk = view.moreMenuHTML(EDITORS, null);
    assert.doesNotMatch(bulk, /Copy path|Refresh status/);
    assert.match(bulk, /data-op="prune_merged"/);
    assert.doesNotMatch(view.moreMenuHTML([], repo("x", { web: null })), /data-url/);
  });

  it("renders the checkout form and folder picker", () => {
    assert.match(view.checkoutFormHTML(4), /Checkout on 4 repo\(s\)/);
    const picker = view.rootPickerHTML("/code", 2, ["/old"]);
    assert.match(picker, /<option value="2" selected>2<\/option>/);
    assert.match(picker, /data-root="\/old"/);
    assert.doesNotMatch(view.rootPickerHTML("/code", 1, []), /Recent/);
  });

  it("renders log entries with optional output", () => {
    const entry = { name: "svc", op: "pull", kind: "ok", message: "pulled main", output: "Fast-forward <x>" };
    const html = view.logEntryHTML(entry, "Pull", "10:00:00");
    assert.match(html, /<b>svc<\/b><span class="op">Pull<\/span><span class="msg">pulled main/);
    assert.match(html, /Fast-forward &lt;x&gt;/);
    assert.doesNotMatch(view.logEntryHTML({ ...entry, output: "" }, "Pull", "t"), /details/);
  });
});
