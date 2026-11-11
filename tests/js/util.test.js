import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { esc, homeRelative, hostOf, Prefs, runPool, timeAgo } from "../../static/js/util.js";
import { MemoryStorage, NOW_MS, NOW_S } from "./fakes.js";

describe("esc", () => {
  it("escapes HTML-significant characters", () => {
    assert.equal(esc(`<a href="x" onclick='y'>&</a>`), "&lt;a href=&quot;x&quot; onclick=&#39;y&#39;&gt;&amp;&lt;/a&gt;");
  });

  it("renders null and undefined as empty, numbers as text", () => {
    assert.equal(esc(null), "");
    assert.equal(esc(undefined), "");
    assert.equal(esc(42), "42");
  });
});

describe("timeAgo", () => {
  const cases = [
    [0, "never"], [NOW_S - 10, "just now"], [NOW_S - 120, "2m ago"], [NOW_S - 7200, "2h ago"],
    [NOW_S - 3 * 86400, "3d ago"], [NOW_S - 65 * 86400, "2mo ago"], [NOW_S - 800 * 86400, "2y ago"],
  ];
  for (const [ts, expected] of cases) {
    it(`${ts ? `${NOW_S - ts}s` : "missing"} → ${expected}`, () => assert.equal(timeAgo(ts, NOW_MS), expected));
  }

  it("defaults to the current time", () => assert.equal(timeAgo(Date.now() / 1000 - 5), "just now"));
});

describe("runPool", () => {
  it("processes every item without exceeding the limit", async () => {
    let inFlight = 0;
    let peak = 0;
    const done = [];
    await runPool([1, 2, 3, 4, 5, 6, 7], 3, async item => {
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      await new Promise(resolve => setTimeout(resolve, 2));
      done.push(item);
      inFlight -= 1;
    });
    assert.deepEqual(done.sort(), [1, 2, 3, 4, 5, 6, 7]);
    assert.equal(peak, 3);
  });

  it("handles an empty list", async () => {
    await runPool([], 4, () => assert.fail("should not run"));
  });
});

describe("hostOf / homeRelative", () => {
  it("extracts hosts and tolerates junk", () => {
    assert.equal(hostOf("https://gitlab.com/a/b"), "gitlab.com");
    assert.equal(hostOf("not a url"), "");
  });

  it("abbreviates paths under home only on a folder boundary", () => {
    assert.equal(homeRelative("/home/me/code", "/home/me"), "~/code");
    assert.equal(homeRelative("/home/me", "/home/me"), "~");
    assert.equal(homeRelative("/home/meow", "/home/me"), "/home/meow");
    assert.equal(homeRelative("/srv/code", ""), "/srv/code");
  });
});

describe("Prefs", () => {
  it("round-trips JSON values with fallbacks", () => {
    const prefs = new Prefs(new MemoryStorage());
    assert.equal(prefs.get("missing", "fallback"), "fallback");
    prefs.set("pins", ["a", "b"]);
    assert.deepEqual(prefs.get("pins", []), ["a", "b"]);
  });

  it("survives broken or unavailable storage", () => {
    const broken = { getItem: () => "{not json", setItem: () => { throw new Error("quota"); } };
    const prefs = new Prefs(broken);
    assert.equal(prefs.get("x", 7), 7);
    assert.doesNotThrow(() => prefs.set("x", 1));
  });
});
