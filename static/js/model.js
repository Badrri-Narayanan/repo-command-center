const DAY_SECONDS = 86400;
const RECENT_ROOTS_LIMIT = 8;
const OPEN_PREFIX = "open_";

export const isLoaded = status => Boolean(status) && !status.loading && !status.error;
export const trackedChanges = status => status.staged + status.unstaged + status.conflicts;
export const isDirty = status => isLoaded(status) && trackedChanges(status) + status.untracked > 0;
export const isOffDefault = status => isLoaded(status) && status.branch !== status.default;

export const FILTERS = {
  all: { label: "All", test: () => true },
  selected: { label: "Selected", test: (s, store) => store.selected.has(s.name) },
  pinned: { label: "★ Pinned", test: (s, store) => store.pins.has(s.name) },
  dirty: { label: "Uncommitted", test: isDirty },
  behind: { label: "Behind", test: s => isLoaded(s) && s.behind > 0 },
  ahead: { label: "Unpushed", test: s => isLoaded(s) && s.ahead > 0 },
  offdefault: { label: "Off default", test: isOffDefault },
  stash: { label: "Has stash", test: s => isLoaded(s) && s.stashes > 0 },
  stale: { label: "Not fetched 24h+", test: (s, store) => isLoaded(s) && isStale(s, store.now()) },
  error: { label: "Errors", test: s => Boolean(s?.error) },
};

function isStale(status, nowMs) {
  return !status.fetchedAt || nowMs / 1000 - status.fetchedAt > DAY_SECONDS;
}

/** Higher = more urgent. Errors first, then conflicts, behind, uncommitted, unpushed, off default. */
export function attentionScore(status) {
  if (!status || status.loading) return 0;
  if (status.error) return 1000;
  return status.conflicts * 100
    + (status.behind ? 40 : 0)
    + (trackedChanges(status) ? 20 : 0)
    + (status.ahead ? 15 : 0)
    + (isOffDefault(status) ? 10 : 0)
    + (status.untracked ? 2 : 0);
}

const SORTS = {
  name: () => 0,
  recent: (a, b) => (b.commit?.ts || 0) - (a.commit?.ts || 0),
  attention: (a, b) => attentionScore(b) - attentionScore(a),
};

export const OP_LABELS = {
  fetch: "Fetch", pull: "Pull", switch_default: "Switch to default", checkout: "Checkout",
  sync: "Morning sync", stash: "Stash", stash_pop: "Stash pop", prune_merged: "Prune merged branches",
};

/** All UI state: repos and their status, selection, pins, filter, search and sort. */
export class RepoStore {
  constructor(prefs, now = () => Date.now()) {
    this.prefs = prefs;
    this.now = now;
    this.root = "";
    this.home = "";
    this.depth = 1;
    this.names = [];
    this.openers = [];
    this.query = "";
    this.loadedAt = 0;
    this.sort = SORTS[prefs.get("rcc.sort")] ? prefs.get("rcc.sort") : "attention";
    this.pins = new Set(prefs.get("rcc.pins", []));
    this.reset();
  }

  reset() {
    this.repos = new Map();
    this.selected = new Set();
    this.busy = new Set();
    this.results = new Map();
    this.filter = "all";
  }

  applyOverview({ root, home, depth, repos, openers }) {
    Object.assign(this, { root, home, depth, openers, names: repos });
    for (const name of repos) {
      if (!this.repos.has(name)) this.repos.set(name, { name, loading: true });
    }
    this.selected = new Set([...this.selected].filter(name => repos.includes(name)));
    this.rememberRoot(root);
  }

  get(name) {
    return this.repos.get(name);
  }

  setStatus(name, status) {
    this.repos.set(name, status);
  }

  all() {
    return this.names.map(name => this.repos.get(name));
  }

  toggleSelected(name, selected = !this.selected.has(name)) {
    if (selected) this.selected.add(name);
    else this.selected.delete(name);
  }

  setSelected(names, selected) {
    names.forEach(name => this.toggleSelected(name, selected));
  }

  togglePin(name) {
    if (this.pins.has(name)) this.pins.delete(name);
    else this.pins.add(name);
    this.prefs.set("rcc.pins", [...this.pins]);
  }

  setSort(sort) {
    if (!SORTS[sort]) return;
    this.sort = sort;
    this.prefs.set("rcc.sort", sort);
  }

  setFilter(filter) {
    if (FILTERS[filter]) this.filter = filter;
  }

  visible() {
    const query = this.query.toLowerCase();
    const filter = FILTERS[this.filter].test;
    const compare = SORTS[this.sort];
    return this.all()
      .filter(status => filter(status, this) && matchesQuery(status, query))
      .sort((a, b) => this.pinOrder(a, b) || compare(a, b) || a.name.localeCompare(b.name));
  }

  pinOrder(a, b) {
    return this.pins.has(b.name) - this.pins.has(a.name);
  }

  /** Filter chips with counts. Empty chips are hidden, except "All" and the active one. */
  filterChips() {
    const all = this.all();
    return Object.entries(FILTERS)
      .map(([key, { label, test }]) => ({ key, label, count: all.filter(s => test(s, this)).length, active: key === this.filter }))
      .filter(chip => chip.count > 0 || chip.key === "all" || chip.active);
  }

  selectionState() {
    const visible = this.visible();
    const selectedVisible = visible.filter(s => this.selected.has(s.name)).length;
    return {
      checked: visible.length > 0 && selectedVisible === visible.length,
      indeterminate: selectedVisible > 0 && selectedVisible < visible.length,
      count: this.selected.size,
    };
  }

  opLabel(op) {
    if (OP_LABELS[op]) return OP_LABELS[op];
    if (!op.startsWith(OPEN_PREFIX)) return op;
    const opener = this.openers.find(o => OPEN_PREFIX + o.id === op);
    return `Open in ${opener?.label || op.slice(OPEN_PREFIX.length)}`;
  }

  recordResult(name, op, kind, message) {
    if (!op.startsWith(OPEN_PREFIX)) this.results.set(name, { op, kind, message });
  }

  recentRoots(exclude = this.root) {
    return this.prefs.get("rcc.roots", []).filter(root => root !== exclude);
  }

  rememberRoot(root) {
    this.prefs.set("rcc.roots", [root, ...this.recentRoots(root)].slice(0, RECENT_ROOTS_LIMIT));
  }

  needsRefresh(maxAgeMs) {
    return this.loadedAt > 0 && this.now() - this.loadedAt > maxAgeMs && this.busy.size === 0;
  }
}

function matchesQuery(status, query) {
  if (!query) return true;
  return status.name.toLowerCase().includes(query) || (status.branch || "").toLowerCase().includes(query);
}
