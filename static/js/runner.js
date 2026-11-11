import { runPool } from "./util.js";

export const CONFIRM_OPS = new Set(["stash", "stash_pop", "prune_merged"]);
const STATUS_CONCURRENCY = 10;
const OP_CONCURRENCY = 6;
const OPEN_CONCURRENCY = 2;
const CONFIRM_PREVIEW = 12;

export const concurrencyFor = op => (op.startsWith("open_") ? OPEN_CONCURRENCY : OP_CONCURRENCY);

export function confirmMessage(label, names, preview = CONFIRM_PREVIEW) {
  const more = names.length > preview ? "\n…" : "";
  return `${label} on ${names.length} repo(s)?\n\n${names.slice(0, preview).join("\n")}${more}`;
}

export const summarize = (label, tally) =>
  `${label}: ${tally.ok} ok · ${tally.skipped} skipped · ${tally.error} failed`;

/**
 * Loads statuses and runs operations through the API, a few repos at a time, keeping the store current.
 * hooks.changed(name?) asks the UI to redraw one tile (or everything), hooks.logged(entry) adds to the
 * activity log, hooks.confirm(message) gates destructive ops, hooks.notify(message) shows a summary.
 */
export class OperationRunner {
  constructor(api, store, hooks = {}) {
    this.api = api;
    this.store = store;
    this.hooks = { changed() {}, logged() {}, confirm: () => true, notify() {}, ...hooks };
  }

  async loadAll() {
    const overview = await this.api.overview();
    this.store.applyOverview(overview);
    this.hooks.changed();
    await runPool(overview.repos, STATUS_CONCURRENCY, name => this.refresh(name));
    this.store.loadedAt = this.store.now();
    this.hooks.changed();
  }

  async refresh(name) {
    try {
      this.store.setStatus(name, await this.api.status(name));
    } catch (error) {
      this.store.setStatus(name, { name, error: error.message });
    }
    this.hooks.changed(name);
  }

  async run(op, names, extra = {}) {
    const targets = names.filter(name => !this.store.busy.has(name));
    if (!targets.length || !this.confirmed(op, targets)) return null;
    const tally = { ok: 0, skipped: 0, error: 0 };
    await runPool(targets, concurrencyFor(op), name => this.runOne(op, name, extra, tally));
    if (targets.length > 1 || tally.error) this.hooks.notify(summarize(this.store.opLabel(op), tally));
    return tally;
  }

  confirmed(op, names) {
    return !CONFIRM_OPS.has(op) || this.hooks.confirm(confirmMessage(this.store.opLabel(op), names));
  }

  async runOne(op, name, extra, tally) {
    this.setBusy(name, true);
    const outcome = await this.call(op, name, extra);
    tally[outcome.result] += 1;
    this.store.recordResult(name, op, outcome.result, outcome.message);
    this.hooks.logged({ name, op, kind: outcome.result, message: outcome.message, output: outcome.output });
    this.setBusy(name, false);
  }

  async call(op, name, extra) {
    try {
      const outcome = await this.api.runOp(op, name, extra);
      if (outcome.status) this.store.setStatus(name, outcome.status);
      return outcome;
    } catch (error) {
      return { result: "error", message: error.message };
    }
  }

  setBusy(name, busy) {
    if (busy) this.store.busy.add(name);
    else this.store.busy.delete(name);
    this.hooks.changed(name);
  }
}
