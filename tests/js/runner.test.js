import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";

import { concurrencyFor, confirmMessage, OperationRunner, summarize } from "../../static/js/runner.js";
import { makeStore, NOW_MS, repo } from "./fakes.js";

class FakeApi {
  constructor() {
    this.statuses = { a: repo("a"), b: repo("b", { behind: 2 }) };
    this.opResults = {};
    this.calls = [];
  }

  async overview() {
    return { root: "/code", home: "/h", depth: 1, repos: Object.keys(this.statuses), openers: [] };
  }

  async status(name) {
    if (name === "broken") throw new Error("status failed");
    return this.statuses[name];
  }

  async runOp(op, name, extra) {
    this.calls.push({ op, name, extra });
    const outcome = this.opResults[name] ?? { result: "ok", message: `${op} done`, output: "out", status: repo(name, { behind: 0 }) };
    if (outcome instanceof Error) throw outcome;
    return outcome;
  }
}

function hookRecorder(overrides = {}) {
  const events = { changed: [], logged: [], confirm: [], notify: [] };
  const hooks = {
    changed: name => events.changed.push(name),
    logged: entry => events.logged.push(entry),
    confirm: message => { events.confirm.push(message); return true; },
    notify: message => events.notify.push(message),
    ...overrides,
  };
  return { events, hooks };
}

describe("helpers", () => {
  it("limit open_* concurrency and summarize tallies", () => {
    assert.equal(concurrencyFor("open_vscode"), 2);
    assert.equal(concurrencyFor("pull"), 6);
    assert.equal(summarize("Pull", { ok: 3, skipped: 1, error: 0 }), "Pull: 3 ok · 1 skipped · 0 failed");
  });

  it("previews at most 12 repos in confirmations", () => {
    const names = Array.from({ length: 14 }, (_, i) => `r${i}`);
    const message = confirmMessage("Stash", names);
    assert.match(message, /^Stash on 14 repo\(s\)\?/);
    assert.ok(message.includes("r11") && !message.includes("r12") && message.endsWith("…"));
    assert.ok(!confirmMessage("Stash", ["a"]).includes("…"));
  });
});

describe("OperationRunner", () => {
  let api;
  let store;

  beforeEach(() => {
    api = new FakeApi();
    store = makeStore();
  });

  it("loads the overview then every status, redrawing as it goes", async () => {
    const { events, hooks } = hookRecorder();
    await new OperationRunner(api, store, hooks).loadAll();
    assert.equal(store.get("b").behind, 2);
    assert.equal(store.loadedAt, NOW_MS);
    assert.equal(events.changed[0], undefined);
    assert.equal(events.changed.at(-1), undefined);
    assert.deepEqual(events.changed.filter(Boolean).sort(), ["a", "b"]);
  });

  it("marks a repo as errored when its status fails", async () => {
    const runner = new OperationRunner(api, store);
    await runner.refresh("broken");
    assert.deepEqual(store.get("broken"), { name: "broken", error: "status failed" });
  });

  it("runs an op, updates status, records and logs results", async () => {
    store.setStatus("b", repo("b", { behind: 2 }));
    const { events, hooks } = hookRecorder();
    const tally = await new OperationRunner(api, store, hooks).run("pull", ["b"]);
    assert.deepEqual(tally, { ok: 1, skipped: 0, error: 0 });
    assert.equal(store.get("b").behind, 0);
    assert.deepEqual(store.results.get("b"), { op: "pull", kind: "ok", message: "pull done" });
    assert.deepEqual(events.logged[0], { name: "b", op: "pull", kind: "ok", message: "pull done", output: "out" });
    assert.equal(store.busy.size, 0);
    assert.deepEqual(events.notify, []);
  });

  it("passes extra fields and summarizes multi-repo runs", async () => {
    api.opResults.b = { result: "skipped", message: "no branch" };
    const { events, hooks } = hookRecorder();
    const tally = await new OperationRunner(api, store, hooks).run("checkout", ["a", "b"], { branch: "dev" });
    assert.deepEqual(tally, { ok: 1, skipped: 1, error: 0 });
    assert.deepEqual(api.calls.map(c => c.extra), [{ branch: "dev" }, { branch: "dev" }]);
    assert.deepEqual(events.notify, ["Checkout: 1 ok · 1 skipped · 0 failed"]);
  });

  it("turns API failures into error results and always notifies on errors", async () => {
    api.opResults.a = new Error("network down");
    const { events, hooks } = hookRecorder();
    const tally = await new OperationRunner(api, store, hooks).run("fetch", ["a"]);
    assert.deepEqual(tally, { ok: 0, skipped: 0, error: 1 });
    assert.equal(store.results.get("a").message, "network down");
    assert.equal(events.notify.length, 1);
  });

  it("skips repos that are already busy", async () => {
    store.busy.add("a");
    const runner = new OperationRunner(api, store);
    assert.equal(await runner.run("pull", ["a"]), null);
    assert.deepEqual(api.calls, []);
  });

  it("asks before destructive ops and respects a no", async () => {
    const { events, hooks } = hookRecorder({ confirm: () => false });
    const result = await new OperationRunner(api, store, hooks).run("prune_merged", ["a"]);
    assert.equal(result, null);
    assert.deepEqual(api.calls, []);
    assert.deepEqual(events.logged, []);
  });

  it("does not ask before safe ops", async () => {
    const { events, hooks } = hookRecorder();
    await new OperationRunner(api, store, hooks).run("fetch", ["a"]);
    assert.deepEqual(events.confirm, []);
  });

  it("works without hooks", async () => {
    await new OperationRunner(api, store).run("stash", ["a", "b"]);
    assert.equal(api.calls.length, 2);
  });
});
