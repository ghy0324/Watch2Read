const { test, before, after, beforeEach, afterEach } = require("node:test");
const assert = require("node:assert/strict");
const { createHash } = require("node:crypto");
const { createServer } = require("node:http");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
let server, browser, page, base;
let errors = [];

before(async () => {
  server = createServer(async (req, res) => {
    try {
      const pathname = decodeURIComponent(new URL(req.url, "http://localhost").pathname);
      const file = path.resolve(root, "." + (pathname === "/" ? "/index.html" : pathname));
      if (!file.startsWith(root + path.sep)) throw new Error("Invalid path");
      const types = { ".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".json": "application/json" };
      res.setHeader("Content-Type", types[path.extname(file)] || "text/plain; charset=utf-8");
      res.end(await fs.readFile(file));
    } catch { res.writeHead(404); res.end(); }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_CHANNEL ? { channel: process.env.BROWSER_CHANNEL } : {}) });
});

after(async () => {
  await browser?.close();
  if (server) await new Promise((resolve) => server.close(resolve));
});

beforeEach(async () => {
  errors = [];
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  page.on("pageerror", (error) => errors.push(error.message));
  // Test the pinned production libraries without depending on CDN availability.
  await page.route("https://cdn.jsdelivr.net/**", async (route) => {
    const lib = route.request().url().includes("/marked@")
      ? path.join(path.dirname(require.resolve("marked")), "marked.umd.js")
      : path.join(path.dirname(require.resolve("dompurify")), "purify.min.js");
    await route.fulfill({ path: lib, contentType: "text/javascript" });
  });
  await page.goto(base + "/?note=" + encodeURIComponent("对话孙宇晨"));
  await page.waitForSelector("#note-preview h3");
  await page.waitForFunction(() => contentReady && !document.querySelector("#note-preview").classList.contains("is-entering"));
  await page.waitForTimeout(220);
});

afterEach(async () => {
  await page?.close();
  assert.deepEqual(errors, []);
});

async function markHeading(index = 45) {
  return page.evaluate((index) => {
    const scroll = document.getElementById("note-preview-scroll");
    const heading = document.querySelectorAll("#note-preview h3")[index];
    scroll.scrollTop += heading.getBoundingClientRect().top - scroll.getBoundingClientRect().top - 12;
    window.testAnchor = heading;
    return heading.textContent;
  }, index).then(async (text) => { await page.waitForTimeout(220); return text; });
}

async function anchorY() {
  return page.evaluate(() => testAnchor.getBoundingClientRect().top - document.getElementById("note-preview-scroll").getBoundingClientRect().top);
}

test("zoom reflow preserves the reading line and does not intercept native shortcuts", async () => {
  await markHeading();
  const initial = await anchorY();
  const nativeAllowed = await page.evaluate(() => document.dispatchEvent(new KeyboardEvent("keydown", { key: "+", metaKey: true, bubbles: true, cancelable: true })));
  assert.equal(nativeAllowed, true);
  const cdp = await page.context().newCDPSession(page);
  // Native desktop zoom changes both CSS viewport size and devicePixelRatio.
  for (const factor of [1.25, 1.5, 2, 1, 0.8, 1, 1.25]) {
    await cdp.send("Emulation.setDeviceMetricsOverride", {
      width: Math.round(1440 / factor), height: Math.round(1000 / factor),
      deviceScaleFactor: factor, mobile: false,
    });
    await page.waitForTimeout(260);
    assert.ok(Math.abs(await anchorY() - initial) < 3, `reading line drift at ${factor}x: ${await anchorY() - initial}px`);
  }
});

test("expanded paragraphs preserve the same text character across reflow", async () => {
  await page.click("#btn-toggle-all-details");
  await page.evaluate(() => {
    const scroll = document.getElementById("note-preview-scroll");
    const paragraph = document.querySelectorAll("#note-preview details li")[220];
    scroll.scrollTop += paragraph.getBoundingClientRect().top - scroll.getBoundingClientRect().top - 12;
    const text = [...paragraph.childNodes].find((node) => node.nodeType === Node.TEXT_NODE && node.length);
    const range = document.createRange();
    range.setStart(text, 0); range.setEnd(text, 1);
    window.testTextRange = range;
  });
  await page.waitForTimeout(220);
  const y = () => page.evaluate(() => testTextRange.getBoundingClientRect().top - document.getElementById("note-preview-scroll").getBoundingClientRect().top);
  const initial = await y();
  await page.setViewportSize({ width: 1080, height: 800 });
  await page.waitForTimeout(300);
  assert.ok(Math.abs(await y() - initial) < 3, `expanded text drift: ${await y() - initial}px`);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.waitForTimeout(300);
  assert.ok(Math.abs(await y() - initial) < 3);
});

test("bulk details expansion keeps the currently visible section anchored", async () => {
  await markHeading();
  const initial = await anchorY();
  await page.click("#btn-toggle-all-details");
  await page.waitForTimeout(300);
  assert.ok(Math.abs(await anchorY() - initial) < 3, `bulk expansion drift: ${await anchorY() - initial}px`);
  await page.click("#btn-toggle-all-details");
  await page.waitForTimeout(300);
  assert.ok(Math.abs(await anchorY() - initial) < 3);
});

test("explicit navigation is not undone by an ongoing sidebar reflow", async () => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await markHeading();
  await page.click("#btn-sidebar-toggle");
  await page.click("#btn-scroll-top");
  await page.waitForTimeout(1200);
  assert.equal(await page.locator("#note-preview-scroll").evaluate((el) => el.scrollTop), 0);
});

test("reader script URL changes with its content and bypasses the unversioned cache", async () => {
  const source = await fs.readFile(path.join(root, "app.js"));
  const version = createHash("sha256").update(source).digest("hex").slice(0, 12);
  const script = await page.locator('script[src^="./app.js"]').getAttribute("src");
  assert.equal(script, `./app.js?v=${version}`, "Update the app.js version in index.html when its content changes");

  let legacyRequests = 0;
  await page.route("**/app.js", async (route) => {
    legacyRequests++;
    await route.fulfill({ body: "/* stale unversioned reader */", contentType: "text/javascript" });
  });
  await page.goto(base + "/?note=" + encodeURIComponent("对Dyna创始人Lindon Gao的3小时访谈.md"));
  await page.waitForSelector("#toc-list .toc-group-text");
  assert.equal(legacyRequests, 0);
  const labels = await page.locator("#toc-list .toc-group-text").allTextContents();
  assert.ok(labels.every((text) => !/展开本节|折叠本节/.test(text)));
});

test("TOC labels exclude section controls without stripping actual title text", async () => {
  const labels = await page.locator("#toc-list .toc-group-text, #toc-list .toc-link").allTextContents();
  assert.ok(labels.length > 0);
  assert.ok(labels.every((text) => !/展开本节|折叠本节/.test(text)));

  const control = page.locator("#note-preview .section-group-toggle-btn").first();
  const id = await control.evaluate((el) => el.closest("h2").id);
  const label = page.locator(`#toc-list [data-target="${id}"] .toc-group-text`);
  const original = await label.textContent();
  assert.equal(await control.textContent(), "展开本节");
  await control.click();
  assert.equal(await control.textContent(), "折叠本节");
  assert.equal(await label.textContent(), original);

  await page.evaluate((id) => {
    const prefix = document.createElement("strong");
    prefix.textContent = "展开本节与折叠本节：";
    document.getElementById(id).prepend(prefix);
    buildToc(document.getElementById("note-preview"), document.getElementById("note-preview-scroll"));
  }, id);
  assert.equal(await label.textContent(), "展开本节与折叠本节：" + original);
  await control.click();
  assert.equal(await control.textContent(), "展开本节");
  assert.equal(await label.textContent(), "展开本节与折叠本节：" + original);
});

test("TOC resize supports dragging, keyboard, persistence and keeps reading position", async () => {
  await markHeading();
  const y = await anchorY();
  const handle = page.locator("#toc-resizer");
  const box = await handle.boundingBox();
  const width = async () => (await page.locator("#toc-panel").boundingBox()).width;
  const beforeWidth = await width();
  await page.mouse.move(box.x + box.width / 2, box.y + 60);
  await page.mouse.down();
  await page.mouse.move(box.x - 120, box.y + 60, { steps: 12 });
  await page.mouse.up();
  await page.waitForTimeout(300);
  assert.ok(await width() > beforeWidth + 100);
  assert.ok(Math.abs(await anchorY() - y) < 3);
  const storedWidth = await width();
  await page.reload();
  await page.waitForSelector("#note-preview h3");
  await page.waitForFunction(() => contentReady && !document.querySelector("#note-preview").classList.contains("is-entering") && document.querySelector("#preview-loading").hidden);
  assert.ok(Math.abs(await width() - storedWidth) < 2);
  await handle.focus();
  await page.keyboard.press("Home");
  await page.waitForFunction(() => document.querySelector("#toc-panel").getBoundingClientRect().width === 180);
  assert.equal(await width(), 180);
  await page.keyboard.press("End");
  await page.waitForFunction(() => document.querySelector("#toc-panel").getBoundingClientRect().width === 480);
  assert.equal(await width(), 480);
  await page.keyboard.press("ArrowRight");
  await page.waitForFunction(() => document.querySelector("#toc-panel").getBoundingClientRect().width === 464);
  assert.equal(await width(), 464);
  await page.click("#btn-toc-toggle");
  assert.equal(await handle.isVisible(), false);
});

test("group modes, folded groups and flat search survive switching and reload", async () => {
  assert.equal(await page.locator("#notes-group-mode").inputValue(), "date");
  assert.equal(await page.locator(".note-group").count(), 9);
  assert.match(await page.locator(".note-group-label").first().textContent(), /2026 年 10 月/);
  await page.locator(".note-group-toggle").first().click();
  assert.equal(await page.locator(".note-group").first().getAttribute("open"), null);
  await page.reload();
  await page.waitForFunction(() => contentReady);
  assert.equal(await page.locator(".note-group").first().getAttribute("open"), null);
  await page.selectOption("#notes-group-mode", "uploader");
  assert.equal(await page.locator(".note-group").count(), 10);
  await page.fill("#input-search", "Kimi");
  await page.waitForSelector(".search-hit");
  assert.equal(await page.locator(".note-group").count(), 0);
  await page.click("#btn-search-clear");
  assert.equal(await page.locator(".note-group").count(), 10);
  await page.selectOption("#notes-group-mode", "none");
  assert.equal(await page.locator(".note-group").count(), 0);
  assert.equal(await page.locator("#notes-list button[data-name]").count(), 18);
});

test("mobile layout and theme stay usable without horizontal overflow", async () => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  assert.equal(await page.locator("#toc-resizer").isVisible(), false);
  assert.equal(Math.round((await page.locator("#toc-panel").boundingBox()).width), 390);
  if (await page.locator("#sidebar").evaluate((el) => el.classList.contains("is-collapsed"))) await page.click("#btn-sidebar-toggle");
  await page.selectOption("#notes-group-mode", "uploader");
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.click("#btn-theme");
  await page.waitForTimeout(300);
  const dir = process.env.SCREENSHOT_DIR || path.join(os.tmpdir(), "watch2read-reader-tests");
  await fs.mkdir(dir, { recursive: true });
  await page.screenshot({ path: path.join(dir, "mobile.png") });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.click("#btn-theme");
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(dir, "desktop.png") });
});

test("new note and home reset the saved reading anchor", async () => {
  await markHeading();
  await page.locator('#notes-list button[data-name="对话田柯宇.md"]').click();
  await page.waitForFunction(() => document.querySelector("#preview-title").textContent === "对话田柯宇" && document.querySelectorAll("#note-preview h3").length === 56);
  await page.setViewportSize({ width: 1200, height: 800 });
  await page.waitForTimeout(300);
  assert.equal(await page.locator("#note-preview-scroll").evaluate((element) => element.scrollTop), 0);
  await page.click("#btn-home");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.waitForTimeout(300);
  assert.equal(await page.locator("#note-preview-scroll").evaluate((element) => element.scrollTop), 0);
});
