import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { changeKey, groupChanges, parsePatch, totals } from "../../static/js/diff.js";

const PATCH = `diff --git a/app.py b/app.py
index 1..2 100644
--- a/app.py
+++ b/app.py
@@ -1,3 +1,3 @@ def main():
 keep
-old
+new
\\ No newline at end of file
@@ -10 +10,2 @@
 ten
+eleven
diff --git a/new.txt b/new.txt
new file mode 100644
--- /dev/null
+++ b/new.txt
@@ -0,0 +1 @@
+hello
diff --git a/gone.txt b/gone.txt
deleted file mode 100644
--- a/gone.txt
+++ /dev/null
@@ -1 +0,0 @@
-bye
diff --git a/old name.md b/new name.md
similarity index 90%
rename from old name.md
rename to new name.md
diff --git a/logo.png b/logo.png
Binary files a/logo.png and b/logo.png differ
`;

describe("parsePatch", () => {
  const files = parsePatch(PATCH);

  it("finds every file with its status", () => {
    assert.deepEqual(files.map(f => [f.path, f.status, f.binary]), [
      ["app.py", "M", false], ["new.txt", "A", false], ["gone.txt", "D", false],
      ["new name.md", "R", false], ["logo.png", "M", true]]);
    assert.equal(files[3].oldPath, "old name.md");
  });

  it("numbers old and new lines per hunk and counts additions/deletions", () => {
    const [first, second] = files[0].hunks;
    assert.equal(first.context, "def main():");
    assert.deepEqual(first.lines.map(l => [l.kind, l.a, l.b]), [
      ["ctx", 1, 1], ["del", 2, null], ["add", null, 2], ["note", null, null]]);
    assert.deepEqual(second.lines.map(l => [l.kind, l.a, l.b, l.text]), [["ctx", 10, 10, "ten"], ["add", null, 11, "eleven"]]);
    assert.deepEqual([files[0].add, files[0].del], [2, 1]);
    assert.deepEqual(totals(files), { add: 3, del: 2 });
  });

  it("copes with empty input, preamble noise, combined diffs and odd hunk headers", () => {
    assert.deepEqual(parsePatch(""), []);
    assert.deepEqual(parsePatch(undefined), []);
    const cc = parsePatch("junk before\ndiff --cc conflict.txt\n@@@ -1,1 -1,1 +1,1 @@@\n weird");
    assert.equal(cc[0].path, "conflict.txt");
    assert.equal(cc[0].hunks[0].lines[0].a, 0);
    assert.equal(cc[0].hunks[0].lines[0].kind, "ctx");
  });
});

describe("groupChanges", () => {
  it("splits porcelain entries into conflicts, staged, unstaged and untracked", () => {
    const groups = groupChanges([
      { path: "both.txt", x: "M", y: "M" }, { path: "new.txt", x: "A", y: " " }, { path: "edit.txt", x: " ", y: "M" },
      { path: "u.txt", x: "?", y: "?" }, { path: "c1", x: "U", y: "U" }, { path: "c2", x: "A", y: "A" }, { path: "c3", x: "D", y: "D" },
    ]);
    assert.deepEqual(groups.staged.map(f => [f.path, f.status]), [["both.txt", "M"], ["new.txt", "A"]]);
    assert.deepEqual(groups.unstaged.map(f => f.path), ["both.txt", "edit.txt"]);
    assert.deepEqual(groups.untracked.map(f => f.status), ["?"]);
    assert.deepEqual(groups.conflicts.map(f => f.path), ["c1", "c2", "c3"]);
    assert.equal(changeKey(groups.staged[0]), "s:both.txt");
    assert.equal(changeKey(groups.unstaged[0]), "w:both.txt");
  });
});
