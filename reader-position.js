/* Preserve a visible text position across native browser zoom and pane reflow. */
(function () {
  "use strict";

  function trackReadingPosition(scroller, content) {
    let anchor = null;
    let frame = 0;
    let settleTimer = 0;
    let reflowing = false;
    let navigating = false;
    const nativeAnchor = scroller.style.overflowAnchor;
    const dimensions = () => [scroller.clientWidth, scroller.clientHeight, window.devicePixelRatio];
    let previousSize = dimensions();
    const sizeChanged = () => dimensions().some((value, i) => value !== previousSize[i]);

    function textRange(node, offset) {
      if (node?.nodeType !== Node.TEXT_NODE || !node.length || !content.contains(node)) return null;
      const range = document.createRange();
      const start = Math.max(0, Math.min(offset, node.length - 1));
      range.setStart(node, start);
      range.setEnd(node, start + 1);
      return range;
    }

    function capture() {
      if (scroller.clientHeight <= 1 || scroller.clientWidth <= 1) return;
      if (scroller.scrollTop <= 1) {
        anchor = { top: true };
        return;
      }
      const box = scroller.getBoundingClientRect();
      const body = content.getBoundingClientRect();
      const padding = parseFloat(getComputedStyle(content).paddingLeft) || 0;
      const xs = [body.left + padding + 4, box.left + box.width / 2, box.right - padding - 8];
      // A character, not a paragraph's height ratio, survives line wrapping.
      for (const dy of [12, 28, 44]) {
        const y = box.top + Math.min(dy, box.height - 1);
        for (const x of xs) {
          if (x <= box.left || x >= box.right) continue;
          let node, offset;
          if (document.caretPositionFromPoint) {
            const caret = document.caretPositionFromPoint(x, y);
            node = caret?.offsetNode;
            offset = caret?.offset;
          } else if (document.caretRangeFromPoint) {
            const caret = document.caretRangeFromPoint(x, y);
            node = caret?.startContainer;
            offset = caret?.startOffset;
          }
          const range = textRange(node, offset);
          const rect = range?.getBoundingClientRect();
          if (rect?.height && rect.bottom > box.top && rect.top < box.bottom) {
            anchor = { node, offset: range.startOffset, y: rect.top - box.top };
            return;
          }
        }
      }
      // Blank gaps, images and browsers without a caret API use a block anchor.
      const blocks = content.querySelectorAll("h1, h2, h3, h4, p, li, summary, pre, img");
      for (const element of blocks) {
        const rect = element.getBoundingClientRect();
        if (rect.height && rect.bottom > box.top && rect.top < box.bottom) {
          anchor = { element, y: rect.top - box.top };
          return;
        }
      }
      if (!content.contains(anchor?.element || anchor?.node)) anchor = null;
    }

    function restore() {
      frame = 0;
      previousSize = dimensions();
      if (scroller.clientHeight <= 1 || scroller.clientWidth <= 1) return;
      if (anchor?.top) {
        scroller.scrollTop = 0;
      } else if (anchor) {
        const target = anchor.element || textRange(anchor.node, anchor.offset);
        if (target && content.contains(anchor.element || anchor.node)) {
          const rect = target.getBoundingClientRect();
          if (rect.height) {
            const delta = rect.top - scroller.getBoundingClientRect().top - anchor.y;
            if (Math.abs(delta) > 0.5) scroller.scrollTop += delta;
          }
        }
      }
      clearTimeout(settleTimer);
      settleTimer = setTimeout(() => {
        reflowing = false;
        scroller.style.overflowAnchor = nativeAnchor;
        previousSize = dimensions();
        capture();
      }, 160);
    }

    function reflow() {
      if (navigating) { finishNavigationLater(); return; }
      if (!anchor) capture();
      reflowing = true;
      scroller.style.overflowAnchor = "none";
      clearTimeout(settleTimer);
      if (!frame) frame = requestAnimationFrame(restore);
    }

    function reset() {
      cancelAnimationFrame(frame);
      clearTimeout(settleTimer);
      frame = 0;
      reflowing = false;
      navigating = false;
      scroller.style.overflowAnchor = nativeAnchor;
      previousSize = dimensions();
      capture();
    }

    function onScroll() {
      if (navigating) finishNavigationLater();
      else if (sizeChanged()) reflow();
      else if (!reflowing) capture();
    }

    function finishNavigationLater() {
      clearTimeout(settleTimer);
      settleTimer = setTimeout(() => {
        navigating = false;
        previousSize = dimensions();
        capture();
      }, 180);
    }

    function navigate() {
      cancelAnimationFrame(frame);
      frame = 0;
      reflowing = false;
      navigating = true;
      scroller.style.overflowAnchor = nativeAnchor;
      finishNavigationLater();
    }

    function onKey(event) {
      if ((event.metaKey || event.ctrlKey) && ["+", "=", "-", "_", "0"].includes(event.key)) {
        if (!reflowing) capture();
        // Keep native browser zoom, including accessibility and reset-to-100%.
      } else if (event.target.id !== "toc-resizer" && ["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) {
        navigate();
      }
    }

    function onWheel(event) {
      if (event.ctrlKey || event.metaKey) {
        if (!reflowing) capture();
      } else navigate();
    }

    function onAnimationEnd() {
      if (!navigating && !reflowing) reset();
    }

    const observer = new ResizeObserver(() => {
      if (sizeChanged()) reflow();
    });
    observer.observe(scroller);
    scroller.addEventListener("scroll", onScroll, { passive: true });
    scroller.addEventListener("wheel", onWheel, { passive: true });
    scroller.addEventListener("pointerdown", navigate, { passive: true });
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("resize", reflow);
    content.addEventListener("animationend", onAnimationEnd);
    capture();

    return { reset, navigate, destroy() {
      cancelAnimationFrame(frame);
      clearTimeout(settleTimer);
      observer.disconnect();
      scroller.style.overflowAnchor = nativeAnchor;
      scroller.removeEventListener("scroll", onScroll);
      scroller.removeEventListener("wheel", onWheel);
      scroller.removeEventListener("pointerdown", navigate);
      document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("resize", reflow);
      content.removeEventListener("animationend", onAnimationEnd);
    } };
  }

  window.Watch2ReadPosition = { trackReadingPosition };
})();
