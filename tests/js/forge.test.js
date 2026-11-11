import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { forgeFor, forgeLinks } from "../../static/js/forge.js";

describe("forgeFor", () => {
  it("recognises GitHub, GitLab and Bitbucket hosts", () => {
    assert.equal(forgeFor("https://github.com/acme/api").short, "GH");
    assert.equal(forgeFor("https://github-personal/acme/api").short, "GH");
    assert.equal(forgeFor("https://gitlab.example.com/acme/api").short, "GL");
    assert.equal(forgeFor("https://bitbucket.org/acme/api").short, "BB");
  });

  it("falls back to a generic link for unknown hosts", () => {
    assert.deepEqual(forgeFor("https://git.internal/acme/api"), { short: "↗", links: [] });
    assert.equal(forgeFor(undefined).short, "↗");
  });
});

describe("forgeLinks", () => {
  it("builds full GitHub URLs including the branch PR link", () => {
    assert.deepEqual(forgeLinks("https://github.com/acme/api", "feat/x 1"), [
      { label: "Pull requests", url: "https://github.com/acme/api/pulls" },
      { label: "Actions", url: "https://github.com/acme/api/actions" },
      { label: "Open PR for this branch", url: "https://github.com/acme/api/compare/feat/x%201?expand=1" },
    ]);
  });

  it("builds GitLab merge-request and Bitbucket PR links", () => {
    const gitlab = forgeLinks("https://gitlab.com/g/api", "fix/y").at(-1).url;
    assert.equal(gitlab, "https://gitlab.com/g/api/-/merge_requests/new?merge_request%5Bsource_branch%5D=fix/y");
    assert.equal(forgeLinks("https://bitbucket.org/a/api", "b").at(-1).url, "https://bitbucket.org/a/api/pull-requests/new?source=b");
  });

  it("omits branch links on a detached HEAD", () => {
    const labels = forgeLinks("https://github.com/acme/api", null).map(link => link.label);
    assert.deepEqual(labels, ["Pull requests", "Actions"]);
  });
});
