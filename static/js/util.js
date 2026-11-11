const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const AGE_UNITS = [[86400 * 365, "y"], [86400 * 30, "mo"], [86400, "d"], [3600, "h"], [60, "m"]];

export const esc = value => String(value ?? "").replace(/[&<>"']/g, c => HTML_ESCAPES[c]);

export function timeAgo(epochSeconds, nowMs = Date.now()) {
  if (!epochSeconds) return "never";
  const elapsed = nowMs / 1000 - epochSeconds;
  const unit = AGE_UNITS.find(([seconds]) => elapsed >= seconds);
  return unit ? `${Math.floor(elapsed / unit[0])}${unit[1]} ago` : "just now";
}

/** Runs `worker` over `items` with at most `limit` in flight at once. */
export async function runPool(items, limit, worker) {
  const queue = [...items];
  const lanes = Array.from({ length: Math.min(limit, queue.length) }, async () => {
    while (queue.length) await worker(queue.shift());
  });
  await Promise.all(lanes);
}

export function hostOf(url) {
  try {
    return new URL(url).host;
  } catch {
    return "";
  }
}

export function homeRelative(path, home) {
  return home && `${path}/`.startsWith(`${home}/`) ? `~${path.slice(home.length)}` : path;
}

/** localStorage-backed preferences that degrade to defaults when storage is unavailable. */
export class Prefs {
  constructor(storage = globalThis.localStorage) {
    this.storage = storage;
  }

  get(key, fallback) {
    try {
      return JSON.parse(this.storage.getItem(key)) ?? fallback;
    } catch {
      return fallback;
    }
  }

  set(key, value) {
    try {
      this.storage.setItem(key, JSON.stringify(value));
    } catch {
      // Private mode or blocked storage: the preference simply isn't remembered.
    }
  }
}
