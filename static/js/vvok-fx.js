/* ==========================================================================
   VvokAI - interface effects
   Everything here exists to make the interface feel like a material rather
   than a picture of one. Four things:

     1. a specular highlight on panels that follows the cursor
     2. a parallax offset on the background layers
     3. a tilt on the brand mark
     4. numbers that count up when they first appear

   Rules this file follows, because effects that stutter look worse than no
   effects at all:

     * Pointer handlers NEVER read layout. Element rectangles are measured on
       demand and cached until the next scroll or resize, so moving the mouse
       cannot trigger a reflow.
     * All work is batched into one requestAnimationFrame per frame, no matter
       how many pointermove events arrive.
     * Everything stops when the tab is hidden or the window loses focus. The
       emulator is rendering the game on the same machine and should not be
       competing with a background tab for GPU time.
     * prefers-reduced-motion disables the lot.
   ========================================================================== */

(() => {
    "use strict";

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    const root = document.documentElement;

    let pointerX = 0;
    let pointerY = 0;
    let havePointer = false;
    let frame = 0;
    let active = true;

    /* ----------------------------------------------------------------------
       Rect cache.

       getBoundingClientRect() forces layout. Calling it inside pointermove -
       once per hovered panel, up to a hundred times a second - is the single
       most common way an effect like this turns into jank. Measure lazily,
       throw the measurements away whenever anything could have moved.
       ---------------------------------------------------------------------- */
    const rects = new WeakMap();
    let generation = 0;

    function rectOf(element) {
        const cached = rects.get(element);
        if (cached && cached.generation === generation) return cached.rect;
        const rect = element.getBoundingClientRect();
        rects.set(element, { rect, generation });
        return rect;
    }

    function invalidateRects() {
        generation += 1;
    }

    /* ----------------------------------------------------------------------
       Specular highlight

       Each lit surface gets --mx/--my in its own coordinate space. The CSS
       paints a radial gradient there. Only surfaces under the cursor are
       updated; the rest keep their last value and are invisible anyway,
       because the gradient's opacity is 0 unless hovered.
       ---------------------------------------------------------------------- */
    const LIT = ".panel, .hist-card, .ps-card";

    let litElements = [];

    function collectLit() {
        litElements = Array.from(document.querySelectorAll(LIT));
        invalidateRects();
    }

    function paintSpecular() {
        for (const element of litElements) {
            const rect = rectOf(element);
            if (!rect.width) continue;
            // Cheap bounds test first: skip anything the cursor is nowhere near,
            // which on a busy view is nearly all of them.
            if (pointerX < rect.left - 40 || pointerX > rect.right + 40 ||
                pointerY < rect.top - 40 || pointerY > rect.bottom + 40) {
                continue;
            }
            element.style.setProperty("--mx", `${pointerX - rect.left}px`);
            element.style.setProperty("--my", `${pointerY - rect.top}px`);
        }
    }

    /* ----------------------------------------------------------------------
       Parallax + brand tilt
       ---------------------------------------------------------------------- */
    const PARALLAX_RANGE = 14;   // px the furthest background layer travels
    const TILT_RANGE = 9;        // degrees the brand mark rotates

    function paintParallax() {
        const w = window.innerWidth || 1;
        const h = window.innerHeight || 1;
        // -1 .. 1 from the centre of the window.
        const nx = (pointerX / w) * 2 - 1;
        const ny = (pointerY / h) * 2 - 1;

        // Negative: the world shifts opposite to the cursor, the way it does
        // when you lean to look past something.
        root.style.setProperty("--px", `${(-nx * PARALLAX_RANGE).toFixed(2)}px`);
        root.style.setProperty("--py", `${(-ny * PARALLAX_RANGE).toFixed(2)}px`);

        const mark = document.querySelector(".brand-mark");
        if (mark) {
            mark.style.setProperty("--tilt-y", `${(nx * TILT_RANGE).toFixed(2)}deg`);
            mark.style.setProperty("--tilt-x", `${(-ny * TILT_RANGE).toFixed(2)}deg`);
        }
    }

    function tick() {
        frame = 0;
        if (!active || !havePointer) return;
        paintSpecular();
        paintParallax();
    }

    function schedule() {
        if (frame || !active) return;
        frame = requestAnimationFrame(tick);
    }

    window.addEventListener("pointermove", (event) => {
        pointerX = event.clientX;
        pointerY = event.clientY;
        havePointer = true;
        schedule();
    }, { passive: true });

    window.addEventListener("resize", invalidateRects, { passive: true });
    // Scrolling moves every panel relative to the viewport, so the cache dies
    // with it. The views wrapper is the scroll container, not the window.
    document.addEventListener("scroll", invalidateRects, { passive: true, capture: true });

    /* ----------------------------------------------------------------------
       Stop when nobody is looking.

       The bot is driving an emulator on this machine. A dashboard sitting
       behind it, animating a background it is not showing anyone, is pure
       stolen frame time.
       ---------------------------------------------------------------------- */
    function setActive(value) {
        active = value;
        if (!active && frame) {
            cancelAnimationFrame(frame);
            frame = 0;
        }
    }

    document.addEventListener("visibilitychange", () => setActive(!document.hidden));
    window.addEventListener("blur", () => setActive(false));
    window.addEventListener("focus", () => setActive(true));

    /* ----------------------------------------------------------------------
       Entry stagger

       Panels rise in sequence instead of the whole view appearing at once.
       The delay is a CSS variable so the timing stays in the stylesheet.
       ---------------------------------------------------------------------- */
    function stagger(view) {
        const panels = view.querySelectorAll(".panel");
        panels.forEach((panel, index) => {
            panel.style.setProperty("--stagger", String(Math.min(index, 8)));
        });
    }

    /* ----------------------------------------------------------------------
       Counting numbers

       Statistics animate from zero the first time they are rendered. Only on
       first sight: re-running it on every refresh would mean a number that is
       polled once a second never stands still long enough to read.
       ---------------------------------------------------------------------- */
    const counted = new WeakSet();
    const COUNT_MS = 900;

    function animateNumber(element) {
        if (counted.has(element)) return;
        const text = element.textContent.trim();
        // Pull the number out but keep whatever surrounds it (%, +, /1200).
        const match = text.match(/^([^\d-]*)(-?[\d\s]+(?:[.,]\d+)?)(.*)$/);
        if (!match) return;

        const target = parseFloat(match[2].replace(/\s/g, "").replace(",", "."));
        if (!isFinite(target) || Math.abs(target) < 2) return;

        counted.add(element);
        const decimals = (match[2].split(/[.,]/)[1] || "").length;
        const prefix = match[1];
        const suffix = match[3];
        const start = performance.now();

        const step = (now) => {
            const t = Math.min((now - start) / COUNT_MS, 1);
            // Ease out quint: fast at first, settles gently on the real value.
            const eased = 1 - Math.pow(1 - t, 5);
            const value = target * eased;
            element.textContent = prefix + value.toFixed(decimals) + suffix;
            if (t < 1) {
                requestAnimationFrame(step);
            } else {
                element.textContent = text;
            }
        };
        requestAnimationFrame(step);
    }

    function animateNumbers(scope) {
        scope.querySelectorAll(".history-kpi, .stat-value, .hist-stat, .rate-stat")
            .forEach(animateNumber);
    }

    /* ----------------------------------------------------------------------
       Re-arm after the app re-renders.

       app.js replaces whole views with innerHTML, which throws away every
       element these effects were attached to. Watching for that is far more
       robust than trying to hook each of its render functions.
       ---------------------------------------------------------------------- */
    function refresh(scope) {
        collectLit();
        if (scope) {
            stagger(scope);
            if (!reduced.matches) animateNumbers(scope);
        }
    }

    function start() {
        refresh(document);
        document.querySelectorAll(".view").forEach(stagger);

        const observer = new MutationObserver((records) => {
            let touched = null;
            for (const record of records) {
                if (record.addedNodes.length) {
                    touched = record.target instanceof Element ? record.target : null;
                    break;
                }
            }
            if (touched) refresh(touched);
        });

        const wrapper = document.querySelector(".views-wrapper");
        if (wrapper) observer.observe(wrapper, { childList: true, subtree: true });
    }

    if (reduced.matches) {
        // Still collect panels so nothing depends on this having run, but do
        // not attach any of the motion.
        document.addEventListener("DOMContentLoaded", collectLit);
        return;
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
