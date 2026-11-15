const HUNK_HEADER = /^@@+ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@+ ?(.*)$/;
const LINE_KINDS = { "+": "add", "-": "del", " ": "ctx", "\\": "note" };

/** Splits unified-diff text (git diff / show / stash show -p) into files, hunks and numbered lines. */
export function parsePatch(text) {
  const files = [];
  let file = null;
  let hunk = null;
  for (const line of (text || "").split("\n")) {
    if (line.startsWith("diff --git ") || line.startsWith("diff --cc ")) {
      file = newFile(line);
      files.push(file);
      hunk = null;
    } else if (!file) {
      continue;
    } else if (line.startsWith("@@")) {
      hunk = newHunk(line);
      file.hunks.push(hunk);
    } else if (hunk && line) {
      addLine(file, hunk, line);
    } else if (!hunk) {
      readFileHeader(file, line);
    }
  }
  return files;
}

function newFile(line) {
  const bAt = line.lastIndexOf(" b/");
  const path = bAt >= 0 ? line.slice(bAt + 3) : line.replace(/^diff --cc /, "");
  return { path, oldPath: null, status: "M", binary: false, hunks: [], add: 0, del: 0 };
}

function readFileHeader(file, line) {
  if (line.startsWith("new file")) file.status = "A";
  else if (line.startsWith("deleted file")) file.status = "D";
  else if (line.startsWith("rename from ")) Object.assign(file, { status: "R", oldPath: line.slice(12) });
  else if (line.startsWith("rename to ")) file.path = line.slice(10);
  else if (line.startsWith("Binary files") || line.startsWith("GIT binary patch")) file.binary = true;
  else if (line.startsWith("+++ b/")) file.path = line.slice(6);
}

function newHunk(line) {
  const match = HUNK_HEADER.exec(line);
  const [oldNo, newNo, context] = match ? [Number(match[1]), Number(match[2]), match[3]] : [0, 0, ""];
  return { header: line, context, oldNo, newNo, lines: [] };
}

function addLine(file, hunk, line) {
  const kind = LINE_KINDS[line[0]] || "ctx";
  const entry = { kind, text: line.slice(1), a: null, b: null };
  if (kind === "del" || kind === "ctx") entry.a = hunk.oldNo++;
  if (kind === "add" || kind === "ctx") entry.b = hunk.newNo++;
  if (kind === "add") file.add += 1;
  if (kind === "del") file.del += 1;
  hunk.lines.push(entry);
}

export const totals = files => files.reduce((sum, f) => ({ add: sum.add + f.add, del: sum.del + f.del }), { add: 0, del: 0 });

/** Porcelain v1 entries grouped the way a "changes" panel shows them. A file can be staged and unstaged. */
export function groupChanges(files) {
  const groups = { conflicts: [], staged: [], unstaged: [], untracked: [] };
  for (const file of files) {
    const xy = file.x + file.y;
    if (xy === "??") groups.untracked.push({ ...file, staged: false, status: "?" });
    else if (xy.includes("U") || xy === "AA" || xy === "DD") groups.conflicts.push({ ...file, staged: false, status: "U" });
    else {
      if (file.x !== " ") groups.staged.push({ ...file, staged: true, status: file.x });
      if (file.y !== " ") groups.unstaged.push({ ...file, staged: false, status: file.y });
    }
  }
  return groups;
}

export const changeKey = file => `${file.staged ? "s" : "w"}:${file.path}`;
