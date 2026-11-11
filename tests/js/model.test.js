import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { attentionScore, FILTERS, isDirty, isLoaded, isOffDefault, RepoStore } from "../../static/js/model.js";
import { makePrefs, makeStore, NOW_MS, NOW_S, repo } from "./fakes.js";

const names = list => list.map(s => s.name);

describe("status predicates", () => {
  it("treat loading and errored repos as not loaded", () => {
    assert.equal(isLoaded(repo("a")), true);
    assert.equal(isLoaded({ name: "a", loading: true }), false);
    assert.equal(isLoaded(repo("a", { error: "boom" })), false);
    assert.equal(isLoaded(undefined), false);
  });

  it("count untracked files as dirty and compare branch with default", () => {
    assert.equal(isDirty(repo("a", { untracked: 1 })), true);
    assert.equal(isDirty(repo("a")), false);
    assert.equal(isOffDefault(repo("a", { branch: "feat" })), true);
    assert.equal(isOffDefault(repo("a", { error: "x", branch: "feat" })), false);
  });
});

describe("attentionScore", () => {
  it("ranks errors > conflicts > behind > changes > ahead > off default > untracked", () => {
    const ordered = [
      repo("error", { error: "x" }), repo("conflicts", { conflicts: 1 }), repo("behind", { behind: 1 }),
      repo("changes", { unstaged: 1 }), repo("ahead", { ahead: 1 }), repo("off", { branch: "feat" }),
      repo("untracked", { untracked: 1 }), repo("clean"),
    ];
    const scores = ordered.map(attentionScore);
    assert.deepEqual([...scores].sort((a, b) => b - a), scores);
    assert.equal(attentionScore({ name: "x", loading: true }), 0);
    assert.equal(attentionScore(undefined), 0);
  });
});

describe("FILTERS", () => {
  const store = makeStore();
  const cases = {
    dirty: [repo("a", { staged: 1 }), repo("b")],
    behind: [repo("a", { behind: 2 }), repo("b")],
    ahead: [repo("a", { ahead: 1 }), repo("b")],
    offdefault: [repo("a", { branch: "feat" }), repo("b")],
    stash: [repo("a", { stashes: 1 }), repo("b")],
    stale: [repo("a", { fetchedAt: NOW_S - 2 * 86400 }), repo("b")],
    error: [repo("a", { error: "x" }), repo("b")],
  };
  for (const [key, [match, miss]] of Object.entries(cases)) {
    it(`${key} matches only the right repos`, () => {
      assert.equal(FILTERS[key].test(match, store), true);
      assert.equal(FILTERS[key].test(miss, store), false);
    });
  }

  it("stale includes repos never fetched", () => {
    assert.equal(FILTERS.stale.test(repo("a", { fetchedAt: null }), store), true);
  });
});

describe("RepoStore", () => {
  it("creates loading placeholders and remembers the root", () => {
    const prefs = makePrefs({ "rcc.roots": ["/old", "/code"] });
    const store = new RepoStore(prefs, () => NOW_MS);
    store.applyOverview({ root: "/code", home: "/h", depth: 2, repos: ["x", "y"], openers: [] });
    assert.deepEqual(store.get("x"), { name: "x", loading: true });
    assert.equal(store.depth, 2);
    assert.deepEqual(prefs.get("rcc.roots"), ["/code", "/old"]);
    assert.deepEqual(store.recentRoots(), ["/old"]);
  });

  it("keeps only the 8 most recent roots", () => {
    const prefs = makePrefs({ "rcc.roots": Array.from({ length: 10 }, (_, i) => `/r${i}`) });
    new RepoStore(prefs).rememberRoot("/new");
    assert.equal(prefs.get("rcc.roots").length, 8);
  });

  it("drops selections for repos that disappeared", () => {
    const store = makeStore([repo("a"), repo("b")]);
    store.setSelected(["a", "b"], true);
    store.applyOverview({ root: "/code", home: "", depth: 1, repos: ["b"], openers: [] });
    assert.deepEqual([...store.selected], ["b"]);
  });

  it("sorts pinned first, then by the chosen order, then by name", () => {
    const store = makeStore([
      repo("c-clean"), repo("b-behind", { behind: 1 }), repo("a-clean"), repo("d-error", { error: "x" }),
    ]);
    assert.deepEqual(names(store.visible()), ["d-error", "b-behind", "a-clean", "c-clean"]);
    store.togglePin("c-clean");
    assert.deepEqual(names(store.visible())[0], "c-clean");
    store.setSort("name");
    assert.deepEqual(names(store.visible()), ["c-clean", "a-clean", "b-behind", "d-error"]);
  });

  it("sorts by most recent commit", () => {
    const store = makeStore([repo("old", { commit: { ts: 1 } }), repo("new", { commit: { ts: 9 } }), repo("none", { commit: null })]);
    store.setSort("recent");
    assert.deepEqual(names(store.visible()), ["new", "old", "none"]);
  });

  it("persists sort and pins, ignoring unknown sorts", () => {
    const prefs = makePrefs();
    const store = makeStore([repo("a")], { prefs });
    store.setSort("bogus");
    assert.equal(store.sort, "attention");
    store.setSort("recent");
    store.togglePin("a");
    const reopened = new RepoStore(prefs);
    assert.equal(reopened.sort, "recent");
    assert.deepEqual([...reopened.pins], ["a"]);
    store.togglePin("a");
    assert.deepEqual(prefs.get("rcc.pins"), []);
  });

  it("ignores a corrupt saved sort", () => {
    assert.equal(new RepoStore(makePrefs({ "rcc.sort": "nonsense" })).sort, "attention");
  });

  it("filters by search over name and branch", () => {
    const store = makeStore([repo("billing-api"), repo("web", { branch: "feat/BILL-1" }), repo("auth")]);
    store.query = "bill";
    assert.deepEqual(names(store.visible()).sort(), ["billing-api", "web"]);
  });

  it("applies filters, ignoring unknown ones", () => {
    const store = makeStore([repo("a", { behind: 1 }), repo("b")]);
    store.setFilter("behind");
    assert.deepEqual(names(store.visible()), ["a"]);
    store.setFilter("nope");
    assert.equal(store.filter, "behind");
  });

  it("lists chips with counts, hiding empty ones except All and the active filter", () => {
    const store = makeStore([repo("a", { behind: 1 }), repo("b")]);
    store.setFilter("stash");
    const chips = Object.fromEntries(store.filterChips().map(c => [c.key, c]));
    assert.deepEqual(Object.keys(chips).sort(), ["all", "behind", "stale", "stash"].filter(k => chips[k]).sort());
    assert.equal(chips.all.count, 2);
    assert.equal(chips.behind.count, 1);
    assert.equal(chips.stash.active, true);
    assert.equal(chips.dirty, undefined);
  });

  it("reports the select-all checkbox state", () => {
    const store = makeStore([repo("a"), repo("b")]);
    assert.deepEqual(store.selectionState(), { checked: false, indeterminate: false, count: 0 });
    store.toggleSelected("a");
    assert.deepEqual(store.selectionState(), { checked: false, indeterminate: true, count: 1 });
    store.toggleSelected("b", true);
    assert.deepEqual(store.selectionState(), { checked: true, indeterminate: false, count: 2 });
    store.toggleSelected("b");
    assert.equal(store.selected.has("b"), false);
  });

  it("labels operations, including opener names", () => {
    const store = makeStore([], { openers: [{ id: "vscode", label: "VS Code" }] });
    assert.equal(store.opLabel("sync"), "Morning sync");
    assert.equal(store.opLabel("open_vscode"), "Open in VS Code");
    assert.equal(store.opLabel("open_zed"), "Open in zed");
    assert.equal(store.opLabel("custom"), "custom");
  });

  it("records results except for open actions", () => {
    const store = makeStore();
    store.recordResult("a", "pull", "ok", "pulled");
    store.recordResult("a", "open_vscode", "ok", "opened");
    assert.deepEqual(store.results.get("a"), { op: "pull", kind: "ok", message: "pulled" });
  });

  it("asks for a refresh only when loaded, old enough and idle", () => {
    const store = makeStore();
    assert.equal(store.needsRefresh(1000), false);
    store.loadedAt = NOW_MS - 5000;
    assert.equal(store.needsRefresh(1000), true);
    store.busy.add("a");
    assert.equal(store.needsRefresh(1000), false);
  });

  it("reset clears per-folder state but keeps pins", () => {
    const store = makeStore([repo("a")]);
    store.togglePin("a");
    store.toggleSelected("a");
    store.setFilter("behind");
    store.reset();
    assert.equal(store.repos.size + store.selected.size, 0);
    assert.equal(store.filter, "all");
    assert.equal(store.pins.has("a"), true);
  });
});
