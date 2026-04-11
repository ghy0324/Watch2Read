/* global marked, DOMPurify */

const THEME_KEY = "w2r_theme";
const SIDEBAR_KEY = "w2r_sidebar";
const LAST_NOTE_KEY = "w2r_last_note";
const SEARCH_PREFS_KEY = "w2r_search_prefs";

function $(id) {
  const el = document.getElementById(id);
  if (!el) throw new Error(`Missing #${id}`);
  return el;
}

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function displayTitle(filename) {
  return filename.endsWith(".md") ? filename.slice(0, -3) : filename;
}

function getTheme() {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

function applyTheme(theme) {
  const t = theme === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", t);
  try { localStorage.setItem(THEME_KEY, t); } catch { /* */ }

  const metaScheme = document.getElementById("meta-color-scheme");
  if (metaScheme) metaScheme.setAttribute("content", t);

  const metaColor = document.getElementById("meta-theme-color");
  if (metaColor) metaColor.setAttribute("content", t === "dark" ? "#0c0e14" : "#f5f6f8");

  const btn = document.getElementById("btn-theme");
  if (btn) {
    const dark = t === "dark";
    btn.setAttribute("aria-pressed", String(dark));
    btn.setAttribute("aria-label", dark ? "切换到浅色模式" : "切换到深色模式");
  }
}

function toggleTheme() { applyTheme(getTheme() === "dark" ? "light" : "dark"); }

let selected = null;
let allNames = [];
let filterQuery = "";
let removeTocSpy = null;
let focusedIndex = -1;
let removeProgressSpy = null;
const contentCache = new Map();
let contentReady = false;

function isMobile() { return window.matchMedia("(max-width: 768px)").matches; }

function readPref(key) {
  try {
    const v = localStorage.getItem(key);
    if (v === "1") return true;
    if (v === "0") return false;
  } catch { /* */ }
  return null;
}

/* ── 侧栏 ── */
function initSidebar() {
  const el = $("sidebar");
  const btn = $("btn-sidebar-toggle");
  const pref = readPref(SIDEBAR_KEY);
  const collapsed = pref !== null ? !pref : isMobile();
  el.classList.toggle("is-collapsed", collapsed);

  btn.addEventListener("click", () => {
    const next = !el.classList.contains("is-collapsed");
    el.classList.toggle("is-collapsed", next);
    try { localStorage.setItem(SIDEBAR_KEY, next ? "0" : "1"); } catch { /* */ }
  });
}

function configureMarked() {
  if (typeof marked !== "undefined" && typeof marked.setOptions === "function") {
    marked.setOptions({ gfm: true, breaks: true });
  }
}

function enhancePreviewLinks(container) {
  container.querySelectorAll("a[href]").forEach((a) => {
    const href = a.getAttribute("href") || "";
    if (/^https?:\/\//i.test(href)) {
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noopener noreferrer");
    }
  });
}

function addCopyButtons(container) {
  container.querySelectorAll("pre").forEach((pre) => {
    if (pre.querySelector(".code-copy-btn")) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "code-copy-btn";
    btn.setAttribute("aria-label", "复制代码");
    btn.innerHTML = `<svg aria-hidden="true" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg><span>复制</span>`;
    btn.addEventListener("click", () => {
      const code = pre.querySelector("code");
      const text = (code || pre).textContent;
      navigator.clipboard.writeText(text).then(() => {
        btn.classList.add("is-copied");
        btn.querySelector("span").textContent = "已复制";
        showToast("已复制到剪贴板");
        setTimeout(() => {
          btn.classList.remove("is-copied");
          btn.querySelector("span").textContent = "复制";
        }, 2000);
      }).catch(() => showToast("复制失败"));
    });
    pre.appendChild(btn);
  });
}

let toastEl = null, toastTimer = null;
function showToast(msg) {
  if (!toastEl) {
    toastEl = document.createElement("div");
    toastEl.className = "toast";
    toastEl.setAttribute("role", "status");
    document.body.appendChild(toastEl);
  }
  toastEl.textContent = msg;
  clearTimeout(toastTimer);
  requestAnimationFrame(() => {
    toastEl.classList.add("is-visible");
    toastTimer = setTimeout(() => toastEl.classList.remove("is-visible"), 2200);
  });
}

function scrollHeadingIntoView(container, el) {
  if (!el || !container || !container.contains(el)) return;
  const cRect = container.getBoundingClientRect();
  const eRect = el.getBoundingClientRect();
  const delta = eRect.top - cRect.top + container.scrollTop - 12;
  container.scrollTo({ top: Math.max(0, delta), behavior: "smooth" });
}

function clearToc() {
  if (removeTocSpy) { removeTocSpy(); removeTocSpy = null; }
  $("toc-panel").hidden = true;
  $("toc-list").innerHTML = "";
  $("toc-empty").hidden = true;
  $("toc-count").textContent = "";
}

function bindTocScrollSpy(scrollEl, previewEl) {
  const tocBody = $("toc-list").parentElement;
  const heads = () => [...previewEl.querySelectorAll("h1, h2, h3, h4")];
  let prevActive = null;

  const onScroll = () => {
    const list = heads();
    const allLinks = [...$("toc-list").querySelectorAll(".toc-link, .toc-group-header")];
    if (!list.length || !allLinks.length) return;

    const y = scrollEl.scrollTop + 32;
    let current = list[0].id;
    for (const h of list) {
      if (h.offsetTop <= y) current = h.id;
      else break;
    }

    if (current === prevActive) return;
    prevActive = current;

    let activeBtn = null;
    allLinks.forEach((btn) => {
      const isActive = btn.dataset.target === current;
      btn.classList.toggle("is-active", isActive);
      if (isActive) activeBtn = btn;
    });

    if (activeBtn && tocBody) {
      const tocRect = tocBody.getBoundingClientRect();
      const btnRect = activeBtn.getBoundingClientRect();
      if (btnRect.top < tocRect.top || btnRect.bottom > tocRect.bottom) {
        activeBtn.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    }
  };
  scrollEl.addEventListener("scroll", onScroll, { passive: true });
  onScroll();
  return () => scrollEl.removeEventListener("scroll", onScroll);
}

function buildToc(previewEl, scrollEl) {
  clearToc();

  const panel = $("toc-panel");
  const listEl = $("toc-list");
  const empty = $("toc-empty");
  const countEl = $("toc-count");

  const headings = [...previewEl.querySelectorAll("h1, h2, h3, h4")];
  if (!headings.length) {
    panel.hidden = false;
    empty.hidden = false;
    return;
  }

  headings.forEach((h, i) => { h.id = `w2r-toc-${i}`; });

  let currentChildren = null;

  headings.forEach((h) => {
    const level = Math.min(4, Math.max(1, parseInt(h.tagName.slice(1), 10)));
    const text = h.textContent.replace(/\s+/g, " ").trim() || "(无标题)";
    const id = h.id;

    if (level <= 2) {
      const group = document.createElement("div");
      group.className = "toc-group";

      const header = document.createElement("button");
      header.type = "button";
      header.className = "toc-group-header";
      header.dataset.target = id;
      header.innerHTML = `<svg class="toc-group-chevron" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M9 18l6-6-6-6"/></svg><span class="toc-group-text">${escapeHtml(text)}</span>`;

      header.addEventListener("click", (e) => {
        const chevron = header.querySelector(".toc-group-chevron");
        if (chevron && chevron.contains(e.target)) {
          group.classList.toggle("is-open");
          return;
        }
        scrollHeadingIntoView(scrollEl, h);
        history.replaceState(null, "", `#${id}`);
      });

      const children = document.createElement("div");
      children.className = "toc-group-children";

      group.appendChild(header);
      group.appendChild(children);
      listEl.appendChild(group);

      currentChildren = children;
    } else {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "toc-link";
      btn.dataset.target = id;
      btn.textContent = text;
      btn.addEventListener("click", () => {
        scrollHeadingIntoView(scrollEl, h);
        history.replaceState(null, "", `#${id}`);
      });

      if (currentChildren) {
        currentChildren.appendChild(btn);
      } else {
        listEl.appendChild(btn);
      }
    }
  });

  empty.hidden = true;
  panel.hidden = false;
  countEl.textContent = `${headings.length}`;
  removeTocSpy = bindTocScrollSpy(scrollEl, previewEl);
}

function applyHashIfAny(scrollEl, previewEl) {
  const hash = decodeURIComponent((location.hash || "").replace(/^#/, ""));
  if (!hash || !/^w2r-toc-\d+$/.test(hash)) return;
  const el = document.getElementById(hash);
  if (el && previewEl.contains(el)) {
    requestAnimationFrame(() => scrollHeadingIntoView(scrollEl, el));
  }
}

async function loadIndex() {
  const res = await fetch("./notes-index.json", { cache: "no-store" });
  if (!res.ok) throw new Error(`无法加载 notes-index.json（${res.status}）`);
  const data = await res.json();
  if (!data || !Array.isArray(data.notes)) throw new Error('notes-index.json 格式应为 { "notes": [...] }');
  return data.notes;
}

async function preloadContents() {
  contentReady = false;
  const pending = allNames
    .filter((name) => !contentCache.has(name))
    .map((name) =>
      fetch(`./notes/${encodeURIComponent(name)}`)
        .then((r) => r.ok ? r.text() : "")
        .then((text) => contentCache.set(name, text))
        .catch(() => contentCache.set(name, ""))
    );
  await Promise.all(pending);
  contentReady = true;
  if (filterQuery) renderList();
}

function getSearchPrefs() {
  return {
    scope: document.querySelector('input[name="search-scope"]:checked')?.value === "current" ? "current" : "all",
    title: !!document.getElementById("search-target-title")?.checked,
    h1: !!document.getElementById("search-target-h1")?.checked,
    h2: !!document.getElementById("search-target-h2")?.checked,
    body: !!document.getElementById("search-target-body")?.checked,
  };
}

function saveSearchPrefsFromDom() {
  const p = getSearchPrefs();
  try { localStorage.setItem(SEARCH_PREFS_KEY, JSON.stringify(p)); } catch { /* */ }
}

function initSearchOptions() {
  try {
    const raw = localStorage.getItem(SEARCH_PREFS_KEY);
    if (raw) {
      const p = JSON.parse(raw);
      if (p.scope === "current") {
        const cur = document.getElementById("search-scope-current");
        if (cur) cur.checked = true;
      } else {
        const all = document.getElementById("search-scope-all");
        if (all) all.checked = true;
      }
      if (typeof p.title === "boolean") {
        const el = document.getElementById("search-target-title");
        if (el) el.checked = p.title;
      }
      if (typeof p.h1 === "boolean") {
        const el = document.getElementById("search-target-h1");
        if (el) el.checked = p.h1;
      }
      if (typeof p.h2 === "boolean") {
        const el = document.getElementById("search-target-h2");
        if (el) el.checked = p.h2;
      }
      if (typeof p.body === "boolean") {
        const el = document.getElementById("search-target-body");
        if (el) el.checked = p.body;
      }
    }
  } catch { /* */ }

  const onChange = () => {
    saveSearchPrefsFromDom();
    renderList();
  };
  document.querySelectorAll('input[name="search-scope"], #search-target-title, #search-target-h1, #search-target-h2, #search-target-body').forEach((el) => {
    el.addEventListener("change", onChange);
  });
}

function lineKind(line) {
  const t = line.replace(/^\s*/, "");
  if (/^#\s/.test(t) && !/^##\s/.test(t)) return "h1";
  if (/^##\s/.test(t) && !/^###\s/.test(t)) return "h2";
  return "body";
}

function buildLineMeta(content) {
  const lines = content.split("\n");
  const meta = [];
  let o = 0;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    meta.push({ start: o, end: o + line.length, kind: lineKind(line) });
    o += line.length + 1;
  }
  return meta;
}

function kindAtOffset(meta, offset) {
  for (const m of meta) {
    if (offset >= m.start && offset < m.end) return m.kind;
  }
  return "body";
}

function allMatchOffsets(content, qLower, qLen) {
  const lower = content.toLowerCase();
  const out = [];
  let pos = 0;
  while ((pos = lower.indexOf(qLower, pos)) !== -1) {
    out.push(pos);
    pos += qLen;
  }
  return out;
}

function getSearchableNoteNames(prefs) {
  if (prefs.scope === "current") {
    return selected ? [selected] : [];
  }
  return allNames;
}

function getHitsForNote(name, prefs, rawQ, q) {
  const content = contentCache.get(name) || "";
  const title = displayTitle(name);
  const meta = buildLineMeta(content);
  const qLen = q.length;
  const allOffsets = allMatchOffsets(content, q, qLen);
  const hits = [];
  const fileMatch = name.toLowerCase().includes(q);
  const titleMatch = title.toLowerCase().includes(q);

  if (prefs.title) {
    if (fileMatch && !titleMatch) {
      hits.push({
        name,
        globalIdx: null,
        detailHtml: `<span class="muted">文件名匹配</span>`,
        noHighlight: true,
      });
    } else if (titleMatch) {
      const hasInBody = allOffsets.length > 0;
      hits.push({
        name,
        globalIdx: hasInBody ? 0 : null,
        detailHtml: `<span class="search-hit-kind">题目</span> ${highlightMatch(title, rawQ)}`,
        noHighlight: !hasInBody,
      });
    }
  }

  for (let gi = 0; gi < allOffsets.length; gi++) {
    const idx = allOffsets[gi];
    const kind = kindAtOffset(meta, idx);
    let ok = false;
    let kindLabel = "";
    if (kind === "h1" && prefs.h1) { ok = true; kindLabel = "一级标题"; }
    else if (kind === "h2" && prefs.h2) { ok = true; kindLabel = "二级标题"; }
    else if (kind === "body" && prefs.body) { ok = true; kindLabel = "正文"; }
    if (!ok) continue;
    hits.push({
      name,
      globalIdx: gi,
      detailHtml: `<span class="search-hit-kind">${escapeHtml(kindLabel)}</span> ${highlightMatch(makeSnippetAt(content, idx, qLen), rawQ)}`,
    });
  }
  return hits;
}

function getAllSearchHits() {
  const rawQ = filterQuery.trim();
  const q = rawQ.toLowerCase();
  if (!q) return [];
  const prefs = getSearchPrefs();
  if (!prefs.title && !prefs.h1 && !prefs.h2 && !prefs.body) return [];
  const names = getSearchableNoteNames(prefs);
  const hits = [];
  for (const name of names) {
    hits.push(...getHitsForNote(name, prefs, rawQ, q));
  }
  return hits;
}

function getMatchingNoteNames() {
  const rawQ = filterQuery.trim();
  const q = rawQ.toLowerCase();
  if (!q) return allNames;
  const prefs = getSearchPrefs();
  if (!prefs.title && !prefs.h1 && !prefs.h2 && !prefs.body) return [];
  return getSearchableNoteNames(prefs).filter((n) => getHitsForNote(n, prefs, rawQ, q).length > 0);
}

function makeSnippetAt(content, idx, qLen) {
  const snippetRadius = 32;
  const start = Math.max(0, idx - snippetRadius);
  const end = Math.min(content.length, idx + qLen + snippetRadius);
  let snippet = content.slice(start, end).replace(/\n+/g, " ").replace(/\s+/g, " ");
  if (start > 0) snippet = "…" + snippet;
  if (end < content.length) snippet += "…";
  return snippet;
}

function highlightMatch(text, query) {
  if (!query) return escapeHtml(text);
  const q = query.trim().toLowerCase();
  if (!q) return escapeHtml(text);
  const idx = text.toLowerCase().indexOf(q);
  if (idx === -1) return escapeHtml(text);
  return escapeHtml(text.slice(0, idx)) + `<mark>${escapeHtml(text.slice(idx, idx + q.length))}</mark>` + escapeHtml(text.slice(idx + q.length));
}

function updateSearchOptionsVisibility() {
  const el = document.getElementById("search-options");
  if (!el) return;
  el.hidden = filterQuery.trim().length === 0;
}

function renderList() {
  updateSearchOptionsVisibility();
  const ul = $("notes-list");
  ul.innerHTML = "";
  $("notes-count").textContent = String(allNames.length);
  focusedIndex = -1;

  const qRaw = filterQuery.trim();
  const prefs = getSearchPrefs();
  const searching = qRaw.length > 0;
  const anyTarget = prefs.title || prefs.h1 || prefs.h2 || prefs.body;

  if (allNames.length === 0) {
    ul.innerHTML = '<li class="list-empty muted">暂无笔记</li>';
    $("filter-empty").hidden = true;
    return;
  }

  if (searching) {
    if (!anyTarget) {
      $("filter-empty").textContent = "请至少勾选一项搜索对象";
      $("filter-empty").hidden = false;
      return;
    }
    if (prefs.scope === "current" && !selected) {
      $("filter-empty").textContent = "请先在全部笔记中打开一篇，或切换到「全部笔记」";
      $("filter-empty").hidden = false;
      return;
    }

    const hits = getAllSearchHits();
    if (hits.length === 0) {
      $("filter-empty").textContent = contentReady ? "没有匹配结果" : "正在索引正文…";
      $("filter-empty").hidden = false;
      return;
    }

    $("filter-empty").hidden = true;
    let prevHitName = null;
    hits.forEach((h, i) => {
      const li = document.createElement("li");
      li.setAttribute("role", "option");
      const btn = document.createElement("button");
      btn.type = "button";
      const continueSameNote = prevHitName === h.name;
      prevHitName = h.name;
      btn.className = continueSameNote ? "search-hit search-hit-continue" : "search-hit";
      btn.dataset.name = h.name;
      btn.dataset.index = String(i);
      if (h.noHighlight) {
        btn.dataset.noHighlight = "1";
      } else if (h.globalIdx != null) {
        btn.dataset.matchIndex = String(h.globalIdx);
      }
      if (selected === h.name) { btn.setAttribute("aria-current", "true"); btn.classList.add("active"); }

      const shownTitle = displayTitle(h.name);
      const titleHtml = continueSameNote
        ? `<span class="search-hit-title search-hit-title-skip" aria-hidden="true"></span>`
        : `<span class="search-hit-title">${highlightMatch(shownTitle, filterQuery)}</span>`;
      const plainStrip = document.createElement("div");
      plainStrip.innerHTML = h.detailHtml;
      const snippetPlain = (plainStrip.textContent || "").replace(/\s+/g, " ").trim();
      btn.setAttribute("aria-label", `${shownTitle}：${snippetPlain}`);
      btn.innerHTML = `${titleHtml}<span class="search-hit-snippet">${h.detailHtml}</span>`;

      btn.addEventListener("click", () => {
        if (h.noHighlight) {
          openNote(h.name);
        } else {
          openNote(h.name, { searchQuery: qRaw, matchIndex: h.globalIdx ?? 0 });
        }
      });
      li.appendChild(btn);
      ul.appendChild(li);
    });
    return;
  }

  $("filter-empty").hidden = true;
  allNames.forEach((name, i) => {
    const li = document.createElement("li");
    li.setAttribute("role", "option");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.dataset.name = name;
    btn.dataset.index = String(i);
    if (selected === name) { btn.setAttribute("aria-current", "true"); btn.classList.add("active"); }

    const main = document.createElement("span");
    main.className = "note-title-main";
    main.textContent = displayTitle(name);
    btn.appendChild(main);

    btn.addEventListener("click", () => openNote(name));
    li.appendChild(btn);
    ul.appendChild(li);
  });
}

function setPreviewLoading(loading) {
  $("note-preview-scroll").classList.toggle("is-loading", loading);
  const el = $("preview-loading");
  el.hidden = !loading;
}

function setTitlePlaceholder(on) {
  $("preview-title").classList.toggle("is-placeholder", on);
}

function countWords(text) {
  const zh = (text.match(/[\u4e00-\u9fff\u3400-\u4dbf]/g) || []).length;
  const en = text.replace(/[\u4e00-\u9fff\u3400-\u4dbf]/g, "").trim().split(/\s+/).filter(Boolean).length;
  return zh + en;
}

function updateReadingMeta(text) {
  const metaEl = $("reading-meta");
  if (!text) { metaEl.hidden = true; return; }
  const wc = countWords(text);
  const min = Math.max(1, Math.ceil(wc / 400));
  $("reading-time").textContent = `${min} 分钟`;
  $("word-count").textContent = `${wc.toLocaleString()} 字`;
  metaEl.hidden = false;
}

function setupReadingProgress(scrollEl) {
  const bar = $("reading-progress");
  const onScroll = () => {
    const { scrollTop, scrollHeight, clientHeight } = scrollEl;
    const max = scrollHeight - clientHeight;
    if (max <= 0) { bar.style.width = "0%"; bar.classList.remove("is-visible"); return; }
    const pct = Math.min(100, (scrollTop / max) * 100);
    bar.style.width = `${pct}%`;
    bar.classList.toggle("is-visible", scrollTop > 50);
  };
  scrollEl.addEventListener("scroll", onScroll, { passive: true });
  onScroll();
  return () => scrollEl.removeEventListener("scroll", onScroll);
}

let searchMatches = [];
let searchMatchIndex = -1;

function clearSearchHighlights(container) {
  searchMatches = [];
  searchMatchIndex = -1;
  container.querySelectorAll("mark.search-highlight").forEach((m) => {
    const parent = m.parentNode;
    parent.replaceChild(document.createTextNode(m.textContent), m);
    parent.normalize();
  });
  const nav = document.getElementById("search-nav");
  if (nav) nav.hidden = true;
}

function highlightAllMatches(container, scrollEl, query, initialMatchIndex = 0) {
  clearSearchHighlights(container);
  if (!query) return;
  const q = query.trim().toLowerCase();
  if (!q) return;

  const textNodes = [];
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) textNodes.push(node);

  for (const tn of textNodes) {
    const text = tn.textContent;
    const lower = text.toLowerCase();
    let startIdx = 0;
    const parts = [];
    let lastEnd = 0;

    while ((startIdx = lower.indexOf(q, startIdx)) !== -1) {
      if (startIdx > lastEnd) {
        parts.push({ text: text.slice(lastEnd, startIdx), match: false });
      }
      parts.push({ text: text.slice(startIdx, startIdx + q.length), match: true });
      lastEnd = startIdx + q.length;
      startIdx = lastEnd;
    }

    if (!parts.length) continue;
    if (lastEnd < text.length) {
      parts.push({ text: text.slice(lastEnd), match: false });
    }

    const frag = document.createDocumentFragment();
    for (const p of parts) {
      if (p.match) {
        const mark = document.createElement("mark");
        mark.className = "search-highlight";
        mark.textContent = p.text;
        frag.appendChild(mark);
      } else {
        frag.appendChild(document.createTextNode(p.text));
      }
    }
    tn.parentNode.replaceChild(frag, tn);
  }

  searchMatches = [...container.querySelectorAll("mark.search-highlight")];
  updateSearchNav();

  if (searchMatches.length > 0) {
    goToMatch(Math.min(initialMatchIndex, searchMatches.length - 1), scrollEl);
  }
}

function goToMatch(index, scrollEl) {
  if (!searchMatches.length) return;
  if (searchMatchIndex >= 0 && searchMatchIndex < searchMatches.length) {
    searchMatches[searchMatchIndex].classList.remove("is-current");
  }
  searchMatchIndex = ((index % searchMatches.length) + searchMatches.length) % searchMatches.length;
  const el = searchMatches[searchMatchIndex];
  el.classList.add("is-current");
  updateSearchNav();

  const cRect = scrollEl.getBoundingClientRect();
  const eRect = el.getBoundingClientRect();
  const target = eRect.top - cRect.top + scrollEl.scrollTop - scrollEl.clientHeight / 3;
  scrollEl.scrollTo({ top: Math.max(0, target), behavior: "smooth" });
}

function updateSearchNav() {
  const nav = document.getElementById("search-nav");
  const countEl = document.getElementById("search-nav-count");
  if (!nav || !countEl) return;
  if (!searchMatches.length) {
    nav.hidden = true;
    return;
  }
  nav.hidden = false;
  countEl.textContent = `${searchMatchIndex + 1} / ${searchMatches.length}`;
}

async function openNote(name, { searchQuery, matchIndex } = {}) {
  selected = name;
  try { localStorage.setItem(LAST_NOTE_KEY, name); } catch { /* */ }

  document.querySelectorAll(".notes-list button").forEach((b) => {
    const on = b.dataset.name === name;
    b.classList.toggle("active", on);
    b.classList.remove("is-focused");
    if (on) b.setAttribute("aria-current", "true");
    else b.removeAttribute("aria-current");
  });

  $("preview-title").textContent = displayTitle(name);
  setTitlePlaceholder(false);
  $("btn-scroll-top").hidden = false;

  const scrollEl = $("note-preview-scroll");
  scrollEl.scrollTop = 0;
  const preview = $("note-preview");
  preview.innerHTML = "";
  clearToc();
  setPreviewLoading(true);
  updateReadingMeta(null);
  if (removeProgressSpy) { removeProgressSpy(); removeProgressSpy = null; }

  if (isMobile()) {
    $("sidebar").classList.add("is-collapsed");
  }

  try {
    const cached = contentCache.get(name);
    let raw;
    if (cached) {
      raw = cached;
    } else {
      const res = await fetch(`./notes/${encodeURIComponent(name)}`, { cache: "no-store" });
      if (!res.ok) { preview.textContent = `加载失败（${res.status}）：${name}`; return; }
      raw = await res.text();
      contentCache.set(name, raw);
    }
    configureMarked();
    if (typeof marked !== "undefined" && typeof DOMPurify !== "undefined") {
      preview.innerHTML = DOMPurify.sanitize(marked.parse(raw));
    } else {
      preview.textContent = raw;
    }

    preview.classList.add("is-entering");
    preview.addEventListener("animationend", () => preview.classList.remove("is-entering"), { once: true });

    enhancePreviewLinks(preview);
    addCopyButtons(preview);
    buildToc(preview, scrollEl);

    if (searchQuery) {
      highlightAllMatches(preview, scrollEl, searchQuery, matchIndex ?? 0);
    } else {
      applyHashIfAny(scrollEl, preview);
    }

    preview.focus({ preventScroll: true });
    updateReadingMeta(preview.textContent || "");
    removeProgressSpy = setupReadingProgress(scrollEl);
  } finally {
    setPreviewLoading(false);
  }
}

function clearSelectionUi() {
  selected = null;
  searchMatches = [];
  searchMatchIndex = -1;
  const nav = document.getElementById("search-nav");
  if (nav) nav.hidden = true;
  try { localStorage.removeItem(LAST_NOTE_KEY); } catch { /* */ }
  $("preview-title").textContent = "未选择";
  setTitlePlaceholder(true);
  $("note-preview").innerHTML = `
    <div class="empty-state">
      <svg class="empty-state-icon" aria-hidden="true" viewBox="0 0 24 24" width="56" height="56" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>
        <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>
        <path d="M8 7h8M8 11h6M8 15h4"/>
      </svg>
      <p class="empty-state-title">选择一篇笔记开始阅读</p>
      <p class="empty-state-hint">从侧栏中点击笔记标题，或使用搜索快速定位</p>
    </div>`;
  clearToc();
  $("btn-scroll-top").hidden = true;
  updateReadingMeta(null);
  $("reading-progress").style.width = "0%";
  $("reading-progress").classList.remove("is-visible");
  if (removeProgressSpy) { removeProgressSpy(); removeProgressSpy = null; }
  document.querySelectorAll(".notes-list button").forEach((b) => {
    b.classList.remove("active");
    b.removeAttribute("aria-current");
  });
}

async function refresh() {
  const hint = $("hint");
  hint.textContent = "";
  hint.classList.remove("is-warn");
  try {
    allNames = await loadIndex();
    renderList();
    await preloadContents();
    if (selected && allNames.includes(selected) && getMatchingNoteNames().includes(selected)) {
      await openNote(selected);
    } else if (selected) {
      clearSelectionUi();
    } else {
      setTitlePlaceholder(true);
    }
  } catch (e) {
    $("notes-list").innerHTML = `<li class="list-error" role="alert">${escapeHtml(String(e.message || e))}</li>`;
    $("notes-count").textContent = "0";
    $("filter-empty").hidden = true;
    if (location.protocol === "file:") {
      hint.textContent = "请使用 python -m http.server 或 GitHub Pages 访问，不要直接 file:// 打开。";
      hint.classList.add("is-warn");
    }
  }
}

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

function updateSearchClearBtn() {
  const btn = document.getElementById("btn-search-clear");
  if (btn) btn.hidden = !filterQuery;
}

function getSearchNavTargets() {
  const q = filterQuery.trim();
  if (!q) {
    return [...document.querySelectorAll(".notes-list button[data-name]:not(.search-hit)")];
  }
  const hits = [...document.querySelectorAll(".notes-list button.search-hit")];
  if (hits.length) return hits;
  return [];
}

function navigateList(dir) {
  const buttons = getSearchNavTargets();
  if (!buttons.length) return;
  buttons.forEach((b) => b.classList.remove("is-focused"));
  focusedIndex = dir === "down"
    ? Math.min(focusedIndex + 1, buttons.length - 1)
    : Math.max(focusedIndex - 1, 0);
  const target = buttons[focusedIndex];
  if (target) { target.classList.add("is-focused"); target.scrollIntoView({ block: "nearest" }); }
}

function openFocusedNote() {
  const buttons = getSearchNavTargets();
  if (focusedIndex < 0 || focusedIndex >= buttons.length) return;
  const b = buttons[focusedIndex];
  const name = b.dataset.name;
  if (!name) return;
  const q = filterQuery.trim();
  if (b.classList.contains("search-hit")) {
    if (b.dataset.noHighlight === "1") {
      openNote(name);
    } else {
      openNote(name, { searchQuery: q, matchIndex: parseInt(b.dataset.matchIndex || "0", 10) });
    }
  } else {
    openNote(name);
  }
}

function setupFab() {
  const fab = document.getElementById("btn-fab-top");
  if (!fab) return;
  const scrollEl = $("note-preview-scroll");
  const check = () => {
    const show = scrollEl.scrollTop > 300 && selected;
    fab.classList.toggle("is-visible", !!show);
    fab.hidden = !show;
  };
  scrollEl.addEventListener("scroll", check, { passive: true });
  fab.addEventListener("click", () => scrollEl.scrollTo({ top: 0, behavior: "smooth" }));
}

function restoreLastNote() {
  try {
    const last = localStorage.getItem(LAST_NOTE_KEY);
    if (last && allNames.includes(last)) openNote(last);
  } catch { /* */ }
}

document.addEventListener("DOMContentLoaded", () => {
  applyTheme(getTheme());
  document.getElementById("btn-theme")?.addEventListener("click", toggleTheme);
  initSidebar();
  initSearchOptions();
  configureMarked();

  const search = $("input-search");
  const clearBtn = document.getElementById("btn-search-clear");

  const debouncedRender = debounce(() => {
    renderList();
    const q = filterQuery.trim();
    if (selected && q && !getMatchingNoteNames().includes(selected)) clearSelectionUi();
  }, 120);

  search.addEventListener("input", () => {
    filterQuery = search.value;
    updateSearchClearBtn();
    debouncedRender();
    if (isMobile() && filterQuery.trim()) {
      $("sidebar").classList.remove("is-collapsed");
    }
  });

  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      search.value = ""; filterQuery = "";
      updateSearchClearBtn();
      renderList();
      search.focus();
    });
  }

  $("btn-refresh").addEventListener("click", refresh);

  $("btn-scroll-top").addEventListener("click", () => {
    $("note-preview-scroll").scrollTo({ top: 0, behavior: "smooth" });
    $("note-preview").focus({ preventScroll: true });
  });

  const scrollEl = $("note-preview-scroll");
  $("btn-match-prev").addEventListener("click", () => goToMatch(searchMatchIndex - 1, scrollEl));
  $("btn-match-next").addEventListener("click", () => goToMatch(searchMatchIndex + 1, scrollEl));

  document.addEventListener("keydown", (ev) => {
    if (ev.target instanceof HTMLInputElement || ev.target instanceof HTMLTextAreaElement) {
      if (ev.key === "Escape" && ev.target === search) {
        ev.preventDefault();
        search.value = ""; filterQuery = "";
        updateSearchClearBtn();
        renderList();
        search.blur();
      }
      if (ev.target === search) {
        if (ev.key === "ArrowDown") { ev.preventDefault(); navigateList("down"); }
        else if (ev.key === "ArrowUp") { ev.preventDefault(); navigateList("up"); }
        else if (ev.key === "Enter") { ev.preventDefault(); openFocusedNote(); search.blur(); }
      }
      return;
    }
    if (ev.key === "/" && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
      ev.preventDefault(); search.focus();
    }
  });

  if (location.protocol === "file:") {
    const hint = $("hint");
    hint.textContent = "请使用 python -m http.server 或 GitHub Pages 访问。";
    hint.classList.add("is-warn");
  }

  setTitlePlaceholder(true);
  setupFab();
  refresh().then(restoreLastNote);
});
