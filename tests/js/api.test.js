import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { ApiClient, ApiError } from "../../static/js/api.js";

function fakeFetch(responses) {
  const calls = [];
  const fetchFn = async (path, init) => {
    calls.push({ path, init });
    const { status = 200, body } = responses.shift();
    return {
      ok: status < 400, status,
      json: async () => (typeof body === "string" ? JSON.parse(body) : body),
    };
  };
  return { fetchFn, calls };
}

describe("ApiClient", () => {
  it("sends the token and encodes repo names", async () => {
    const { fetchFn, calls } = fakeFetch([{ body: { branch: "main" } }, { body: { branches: [] } }]);
    const api = new ApiClient("tok", fetchFn);
    assert.deepEqual(await api.status("org/my repo"), { branch: "main" });
    await api.branches("a&b");
    assert.equal(calls[0].path, "/api/status?repo=org%2Fmy%20repo");
    assert.equal(calls[1].path, "/api/branches?repo=a%26b");
    assert.equal(calls[0].init.method, "GET");
    assert.equal(calls[0].init.headers["X-RCC-Token"], "tok");
    assert.equal(calls[0].init.body, undefined);
  });

  it("posts operations and root changes as JSON", async () => {
    const { fetchFn, calls } = fakeFetch([{ body: { result: "ok" } }, { body: { count: 3 } }, { body: { repos: [] } }]);
    const api = new ApiClient("tok", fetchFn);
    await api.runOp("checkout", "svc", { branch: "dev" });
    await api.changeRoot("~/code", 2);
    await api.overview();
    assert.equal(calls[0].init.method, "POST");
    assert.deepEqual(JSON.parse(calls[0].init.body), { op: "checkout", repo: "svc", branch: "dev" });
    assert.deepEqual(JSON.parse(calls[1].init.body), { path: "~/code", depth: 2 });
    assert.equal(calls[2].path, "/api/repos");
  });

  it("raises the server's error message", async () => {
    const { fetchFn } = fakeFetch([{ status: 404, body: { error: "unknown repo: x" } }]);
    await assert.rejects(new ApiClient("t", fetchFn).status("x"), new ApiError("unknown repo: x"));
  });

  it("falls back to the HTTP status when the body isn't JSON", async () => {
    const { fetchFn } = fakeFetch([{ status: 502, body: "<html>" }, { status: 500, body: {} }]);
    const api = new ApiClient("t", fetchFn);
    await assert.rejects(api.overview(), { message: "HTTP 502" });
    await assert.rejects(api.overview(), { message: "HTTP 500" });
  });

  it("uses the global fetch by default", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ hello: 1 }) });
    try {
      assert.deepEqual(await new ApiClient("t").overview(), { hello: 1 });
    } finally {
      globalThis.fetch = original;
    }
  });
});
