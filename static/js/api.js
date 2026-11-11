export class ApiError extends Error {}

/** Talks to server.py. Every call carries the per-run token from the page. */
export class ApiClient {
  constructor(token, fetchFn = (...args) => globalThis.fetch(...args)) {
    this.token = token;
    this.fetch = fetchFn;
  }

  overview() {
    return this.request("/api/repos");
  }

  status(name) {
    return this.request(`/api/status?repo=${encodeURIComponent(name)}`);
  }

  branches(name) {
    return this.request(`/api/branches?repo=${encodeURIComponent(name)}`);
  }

  runOp(op, repo, extra = {}) {
    return this.request("/api/op", { op, repo, ...extra });
  }

  changeRoot(path, depth) {
    return this.request("/api/root", { path, depth });
  }

  async request(path, body) {
    const response = await this.fetch(path, {
      method: body ? "POST" : "GET",
      headers: { "X-RCC-Token": this.token, "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
    if (!response.ok) throw new ApiError(data.error || `HTTP ${response.status}`);
    return data;
  }
}
