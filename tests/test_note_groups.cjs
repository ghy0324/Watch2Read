const test = require("node:test");
const assert = require("node:assert/strict");
const { parseMetadata, normalizePrefs, groupNotes } = require("../notes-grouping.js");

test("reads canonical metadata from Markdown, not chapter text", () => {
  const source = "# Interview\n\n> **UP主**: [Example](https://example.test) | **发布日期**: 2026-10-15 00:00:00 | **时长**: 2:00:00\n\n## 2020-01-01\nUP主: Wrong";
  assert.deepEqual(parseMetadata(source), { uploader: "Example", date: "2026-10-15" });
});

test("accepts unlinked uploaders and separate metadata lines", () => {
  const source = "> **UP主**： Example \\\| Studio\r\n> **发布日期**：2024-02-29\n";
  assert.deepEqual(parseMetadata(source), { uploader: "Example | Studio", date: "2024-02-29" });
});

test("missing or invalid metadata falls back without inventing dates", () => {
  assert.deepEqual(parseMetadata(undefined), { uploader: "", date: "" });
  assert.deepEqual(parseMetadata("> **发布日期**: 2025-02-29"), { uploader: "", date: "" });
  assert.deepEqual(parseMetadata("> **发布日期**: 2026-13-01"), { uploader: "", date: "" });
});

test("linked uploader names preserve escaped punctuation", () => {
  assert.deepEqual(parseMetadata("> **UP主**: [A\\[B\\]](https://example.test)"), { uploader: "A[B]", date: "" });
});

const names = ["old.md", "new.md", "unknown.md", "same.md"];
const metadata = new Map([
  ["old.md", { date: "2026-08-26", uploader: "Beta" }],
  ["new.md", { date: "2026-10-15", uploader: "Alpha" }],
  ["same.md", { date: "2026-10-13", uploader: "Beta" }],
]);

test("date groups are newest first, with unknown dates last", () => {
  const groups = groupNotes(names, metadata, "date");
  assert.deepEqual(groups.map((group) => group.key), ["date:2026-10", "date:2026-08", "date:"]);
  assert.deepEqual(groups[0].names, ["new.md", "same.md"]);
  assert.equal(groups[0].label, "2026 年 10 月");
  assert.equal(groups[2].label, "未知日期");
  assert.deepEqual(names, ["old.md", "new.md", "unknown.md", "same.md"]);
});

test("uploader groups are alphabetical and each group sorts newest first", () => {
  const groups = groupNotes(names, metadata, "uploader");
  assert.deepEqual(groups.map((group) => group.label), ["Alpha", "Beta", "未知 UP主"]);
  assert.deepEqual(groups[1].names, ["same.md", "old.md"]);
});

test("ungrouped mode preserves the existing index order", () => {
  const groups = groupNotes(names, metadata, "none");
  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].names, names);
  assert.notEqual(groups[0].names, names);
});

test("stored preferences have safe defaults and independent collapse lists", () => {
  assert.deepEqual(normalizePrefs(null), { mode: "date", collapsed: { date: [], uploader: [] } });
  assert.deepEqual(normalizePrefs({ mode: "wrong", collapsed: { date: [null, "date:2026-10"], uploader: "bad" } }), {
    mode: "date", collapsed: { date: ["date:2026-10"], uploader: [] },
  });
  assert.deepEqual(normalizePrefs({ mode: "uploader", collapsed: { uploader: ["uploader:Alpha"] } }), {
    mode: "uploader", collapsed: { date: [], uploader: ["uploader:Alpha"] },
  });
});
