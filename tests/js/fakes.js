import { RepoStore } from "../../static/js/model.js";
import { Prefs } from "../../static/js/util.js";

export const NOW_MS = Date.UTC(2026, 0, 15, 12, 0, 0);
export const NOW_S = NOW_MS / 1000;

export class MemoryStorage {
  constructor(initial = {}) {
    this.data = new Map(Object.entries(initial));
  }

  getItem(key) {
    return this.data.has(key) ? this.data.get(key) : null;
  }

  setItem(key, value) {
    this.data.set(key, value);
  }
}

export function makePrefs(initial = {}) {
  const encoded = Object.fromEntries(Object.entries(initial).map(([k, v]) => [k, JSON.stringify(v)]));
  return new Prefs(new MemoryStorage(encoded));
}

/** A loaded, clean repo on its default branch; override any field. */
export function repo(name, overrides = {}) {
  return {
    name, path: `/code/${name}`, branch: "main", default: "main", upstream: "origin/main", detached: false,
    ahead: 0, behind: 0, staged: 0, unstaged: 0, untracked: 0, conflicts: 0, stashes: 0,
    commit: { hash: "abc1234", subject: `work on ${name}`, author: "Ada", rel: "2 days ago", ts: NOW_S - 172800 },
    web: `https://github.com/acme/${name}`, fetchedAt: NOW_S - 3600, error: null, ...overrides,
  };
}

export function makeStore(repos = [], { prefs = makePrefs(), openers = [] } = {}) {
  const store = new RepoStore(prefs, () => NOW_MS);
  store.applyOverview({ root: "/code", home: "/home/me", depth: 1, repos: repos.map(r => r.name), openers });
  repos.forEach(r => store.setStatus(r.name, r));
  return store;
}
