(function (root) {
  "use strict";

  const MODES = ["date", "uploader", "none"];
  const compareText = (a, b) => a.localeCompare(b, "zh-CN", { numeric: true });

  function parseMetadata(markdown) {
    const lines = String(markdown || "").split(/\r?\n/);
    const metadata = lines.filter((line) => /^\s*>\s*\*\*(?:UP主|发布日期)\*\*/.test(line)).join("\n");
    const uploaderMatch = metadata.match(/\*\*UP主\*\*\s*[:：]\s*(.*?)(?=\s*\|\s*\*\*|\n|$)/);
    let uploader = uploaderMatch ? uploaderMatch[1].trim() : "";
    const linkedName = uploader.match(/^\[((?:\\.|[^\]])*)\]\([^\n]*\)$/);
    if (linkedName) uploader = linkedName[1];
    uploader = uploader.replace(/\\([\\[\]|*_])/g, "$1").trim();

    const dateMatch = metadata.match(/\*\*发布日期\*\*\s*[:：]\s*(\d{4}-\d{2}-\d{2})(?=\s|\||$)/);
    let date = dateMatch ? dateMatch[1] : "";
    const parsedDate = new Date(`${date}T00:00:00Z`);
    if (!date || !Number.isFinite(parsedDate.getTime()) || parsedDate.toISOString().slice(0, 10) !== date) date = "";
    return { uploader, date };
  }

  function normalizePrefs(value) {
    const prefs = value && typeof value === "object" ? value : {};
    const collapsed = prefs.collapsed && typeof prefs.collapsed === "object" ? prefs.collapsed : {};
    return {
      mode: MODES.includes(prefs.mode) ? prefs.mode : "date",
      collapsed: {
        date: Array.isArray(collapsed.date) ? collapsed.date.filter((key) => typeof key === "string") : [],
        uploader: Array.isArray(collapsed.uploader) ? collapsed.uploader.filter((key) => typeof key === "string") : [],
      },
    };
  }

  function groupNotes(names, metadata, mode) {
    if (mode === "none") return [{ key: "none", label: "", names: [...names] }];
    const groups = new Map();
    const metaFor = (name) => metadata.get(name) || { date: "", uploader: "" };
    for (const name of names) {
      const meta = metaFor(name);
      const value = mode === "uploader" ? meta.uploader : meta.date.slice(0, 7);
      const key = `${mode}:${value}`;
      if (!groups.has(key)) {
        const label = mode === "uploader"
          ? (value || "未知 UP主")
          : (value ? `${value.slice(0, 4)} 年 ${Number(value.slice(5))} 月` : "未知日期");
        groups.set(key, { key, label, value, names: [] });
      }
      groups.get(key).names.push(name);
    }
    const result = [...groups.values()].sort((a, b) => {
      if (!a.value || !b.value) return a.value ? -1 : b.value ? 1 : 0;
      return mode === "uploader" ? compareText(a.value, b.value) : b.value.localeCompare(a.value);
    });
    for (const group of result) {
      group.names.sort((a, b) => metaFor(b).date.localeCompare(metaFor(a).date) || compareText(a, b));
    }
    return result;
  }

  const api = { parseMetadata, normalizePrefs, groupNotes };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.NoteGrouping = api;
})(globalThis);
