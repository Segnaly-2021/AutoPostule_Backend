# auto_apply_app/infrastructures/agent/human_behavior.py
import asyncio
import logging
import math
import random
from playwright.async_api import Locator, Page

from auto_apply_app.infrastructures.agent.input.backend import BACKEND_X11, get_backend

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Does the page actually receive our keystrokes?
#
# XTEST delivers keys to whatever widget the BROWSER has focused. That is not
# the same thing as the DOM focus `fill()`/`clear()` sets: Playwright focuses a
# node inside the RENDERER over CDP, which leaves the browser's focused widget
# untouched. On a window whose focused widget is the omnibox, the address bar
# receives the text while `document.activeElement` still reports the field —
# so asserting on activeElement CANNOT detect this. Only a real keystroke can.
#
# The probe is a bare Shift: it fires a keydown the page can count and types no
# character, so it cannot corrupt the field it is testing.
# ---------------------------------------------------------------------------

_FOCUS_PROBE_JS = """() => {
    window.__focusProbe = 0;
    if (!window.__focusProbeBound) {
        window.__focusProbeBound = true;
        document.addEventListener('keydown', () => { window.__focusProbe++; }, true);
    }
}"""


async def _keys_reach_the_page(backend, page) -> bool:
    """Send one harmless keystroke and ask the page whether it arrived."""
    try:
        await page.evaluate(_FOCUS_PROBE_JS)
        await backend.key_press("Shift", 0.03)
        await asyncio.sleep(0.05)
        return bool(await page.evaluate("window.__focusProbe || 0"))
    except Exception:
        # Can't ask — don't block typing on a failed diagnostic.
        logger.debug("focus probe failed", exc_info=True)
        return True


async def _ensure_keys_land_on(locator) -> None:
    """Guarantee the keystrokes about to be sent reach `locator`, not the browser.

    Only meaningful under an OS-level backend; the CDP backend addresses the
    element directly and cannot miss, so this is a no-op there.

    The recovery is a real click, deliberately: `locator.focus()` moves focus
    inside the renderer, which is the half that is already working. Only an
    actual pointer press moves the BROWSER's focus onto the web contents, and
    that is the half that is missing. This is what APEC has always done by hand
    (`apec_worker.py:541`) and what HelloWork and WTTJ never did.
    """
    page = locator.page
    backend = get_backend(page)
    if getattr(backend, "name", None) != BACKEND_X11:
        return

    if await _keys_reach_the_page(backend, page):
        return

    logger.warning(
        "[TYPE] keystrokes are not reaching the page — the browser is focused "
        "somewhere else (the address bar, most likely). Clicking the field to "
        "take focus back. url=%s", page.url,
    )
    try:
        await human_hover_and_click(locator)
    except Exception:
        logger.debug("focus-recovery click failed", exc_info=True)

    if await _keys_reach_the_page(backend, page):
        logger.info("[TYPE] focus recovered; the page is receiving keys again")
        return

    # Refusing to type is the point. Typing anyway is how a job title ended up
    # in the address bar and navigated the run to a broken page — silently,
    # because nothing here ever checked.
    where = "unknown"
    try:
        where = await page.evaluate(
            "() => document.activeElement ? document.activeElement.tagName + "
            "(document.activeElement.id ? '#' + document.activeElement.id : '') "
            ": 'NONE'"
        )
    except Exception:
        pass
    raise RuntimeError(
        "OS-level keystrokes are not reaching the page and a click did not "
        f"recover it (document.activeElement={where}, url={page.url}). Refusing "
        "to type: the text would go to the browser UI."
    )


# ---------------------------------------------------------------------------
# Basic delays / typing (unchanged, kept for backwards compat)
# ---------------------------------------------------------------------------

async def human_delay(min_ms: int = 500, max_ms: int = 2000) -> None:
    """Sleep for a random duration. Use BETWEEN actions, not as a substitute
    for semantic waits (wait_for_selector, etc.)."""
    delay_seconds = random.uniform(min_ms / 1000, max_ms / 1000)
    await asyncio.sleep(delay_seconds)


async def human_type(locator: Locator, text: str, min_delay: int = 90, max_delay: int = 300) -> None:
    """Types text character-by-character, slowly, with per-key jitter.

    Two things this has to get right, both of which it got wrong before.

    The jitter is redrawn per KEY. The original drew one delay for the whole
    string and handed it to locator.type(delay=...), spacing every keystroke
    identically — measured at 0.7ms of deviation across 17 characters, which is
    a metronome no hand produces and cheaper for a board to collect than
    anything on the fingerprint surface.

    And the draw is LOG-NORMAL, not uniform. Flat noise between two bounds is
    random but not human: it makes every interval equally likely, so the
    histogram is a rectangle. Real inter-key intervals pile up around a
    comfortable tempo with a long right tail — mostly quick, occasionally much
    slower. The tail is the part that looks alive.

    On top of that, three things a uniform draw can't express: a per-call tempo
    (nobody types every field at the same speed), pauses at word boundaries
    where the next word gets decided, and the rare long stall of attention going
    elsewhere mid-field. Always clears the field first.
    """
    if not text:
        return
    backend = get_backend(locator.page)
    try:
        await locator.clear()
    except Exception:
        try:
            await locator.click()
            await locator.press("Control+A")
            await locator.press("Delete")
        except Exception:
            pass

    await _type_stream(backend, text, locator, min_delay, max_delay)


async def human_type_into_focus(
    page: Page, text: str, min_delay: int = 90, max_delay: int = 300
) -> None:
    """Type with the same rhythm, but at whatever currently holds focus.

    For the browser's own UI — the address bar above all — which has no DOM node
    and therefore no Locator. Only meaningful under an OS-level backend: CDP
    keystrokes go to the page, so there is no omnibox for them to reach.
    """
    if not text:
        return
    await _type_stream(get_backend(page), text, None, min_delay, max_delay)


async def _type_stream(backend, text: str, locator, min_delay: int, max_delay: int) -> None:
    """The keystroke rhythm itself, shared by both entry points.

    One tempo per field. Drawn fresh each call so two fields in the same form
    never share a rhythm — a run that types every input at one speed is as
    regular as the metronome this replaced, just one level up.
    """
    # Typing goes wherever X focus is, and on a window-managed display that is
    # whatever the operator last clicked — not necessarily this browser. This
    # does not fix that (nothing inside X can), it makes it AUDIBLE: the check
    # inside focus_window logs which window actually holds focus, so a field
    # that silently receives nothing leaves a line saying why.
    focus = getattr(backend, "focus_window", None)
    if focus is not None:
        try:
            await focus()
        except Exception:
            logger.debug("focus check before typing failed", exc_info=True)

    # Window focus is not enough — see _ensure_keys_land_on. Runs AFTER
    # focus_window(), because activating the window is itself something that can
    # move focus inside the browser.
    if locator is not None:
        await _ensure_keys_land_on(locator)

    tempo = random.uniform(0.78, 1.5)
    median_ms = (min_delay + max_delay) / 2 * tempo
    floor_s, ceiling_s = min_delay * 0.9 / 1000.0, max_delay * 3.5 / 1000.0

    for char in text:
        # The whole key-down-to-key-down interval is decided FIRST, because the
        # hold has to come out of it rather than be added on top: otherwise every
        # character costs an extra ~85ms and a cover letter grows by minutes.

        # sigma sets how heavy the tail is; 0.45 keeps most keys near the tempo
        # while still throwing the occasional slow one.
        interval = random.lognormvariate(math.log(median_ms / 1000.0), 0.45)
        interval = max(floor_s, min(interval, ceiling_s))
        base_interval = interval

        # Hesitations are multiples of this field's tempo, not fixed seconds: a
        # caller asking for fast typing should get proportionally short stalls,
        # not the same 2.6s pause dropped into a 20ms rhythm.
        beat = median_ms / 1000.0
        if char == " " and random.random() < 0.45:
            interval += beat * random.uniform(0.8, 3.1)    # deciding the next word
        elif random.random() < 0.08:
            interval += beat * random.uniform(1.5, 4.6)    # a mid-word hesitation
        if random.random() < 0.025:
            interval += beat * random.uniform(5.0, 13.0)   # attention goes elsewhere

        # How long the key is HELD. Measured at 2.4ms before this existed: the
        # intervals between keys were already human, but every key was struck and
        # released instantly, which no finger does. Real dwell sits around
        # 70-100ms and is as cheap for a page to collect as the gaps are.
        #
        # Capped at half the interval so it stays a hold and never becomes the
        # whole beat — which also keeps the shape intact when a caller asks for
        # very fast typing.
        dwell = min(0.16, max(0.04, random.gauss(0.085, 0.025)))
        # Against the BASE interval, not the hesitated one: pausing to think
        # about the next word does not mean leaning on the current key.
        dwell = min(dwell, base_interval * 0.5)

        # Element-targeted where the backend can be (CDP); the X11 backend types
        # into whatever holds focus, which is why callers click the field first.
        await backend.type_char(char, locator=locator, dwell_s=dwell)
        await asyncio.sleep(max(0.004, interval - dwell))


# ---------------------------------------------------------------------------
# Mouse trajectory primitives — the core anti-detection upgrade
# ---------------------------------------------------------------------------

# Track the mouse position across calls. Playwright doesn't expose it, so
# we remember it ourselves. Initial value is a plausible starting point.
_last_mouse_pos: dict[int, tuple[float, float]] = {}


def _get_last_pos(page: Page) -> tuple[float, float]:
    """Get the last known mouse position for this page, or pick a random start."""
    key = id(page)
    if key not in _last_mouse_pos:
        # Random plausible starting position — center-ish but not exact center.
        _last_mouse_pos[key] = (
            random.uniform(400, 900),
            random.uniform(300, 600),
        )
    return _last_mouse_pos[key]


def _set_last_pos(page: Page, x: float, y: float) -> None:
    _last_mouse_pos[id(page)] = (x, y)


def _bezier_curve(p0, p1, p2, p3, t: float) -> tuple[float, float]:
    """Cubic Bezier evaluation at t ∈ [0, 1]."""
    u = 1 - t
    x = (u**3) * p0[0] + 3 * (u**2) * t * p1[0] + 3 * u * (t**2) * p2[0] + (t**3) * p3[0]
    y = (u**3) * p0[1] + 3 * (u**2) * t * p1[1] + 3 * u * (t**2) * p2[1] + (t**3) * p3[1]
    return x, y


async def human_mouse_move(page: Page, target_x: float, target_y: float) -> None:
    """
    Move the mouse to (target_x, target_y) along a curved, multi-step path.
    Simulates real human mouse motion: acceleration, deceleration, slight wobble.
    """
    backend = get_backend(page)
    start_x, start_y = _get_last_pos(page)
    dx = target_x - start_x
    dy = target_y - start_y
    distance = math.hypot(dx, dy)

    if distance < 5:
        # Already close enough — just move and bail
        await get_backend(page).move(target_x, target_y)
        _set_last_pos(page, target_x, target_y)
        return

    # Two control points perpendicular-ish to the straight line, randomly offset.
    # This produces a natural arc rather than a straight line.
    perpendicular_offset = random.uniform(-1, 1) * distance * 0.15
    mid_x = (start_x + target_x) / 2
    mid_y = (start_y + target_y) / 2
    # Rotate the offset 90° from the travel direction
    if distance > 0:
        nx = -dy / distance
        ny = dx / distance
    else:
        nx, ny = 0, 1

    ctrl1 = (
        start_x + dx * 0.3 + nx * perpendicular_offset * 0.6,
        start_y + dy * 0.3 + ny * perpendicular_offset * 0.6,
    )
    ctrl2 = (
        start_x + dx * 0.7 + nx * perpendicular_offset * 0.4,
        start_y + dy * 0.7 + ny * perpendicular_offset * 0.4,
    )

    # More steps for longer distances; min 15 even for short hops.
    steps = max(15, min(40, int(distance / 20)))

    # Longer reaches overshoot and get corrected — Fitts's law in practice.
    # A cursor that lands exactly on target every single time is its own tell.
    overshooting = distance > 220 and random.random() < 0.35
    if overshooting:
        aim_x = target_x + (dx / distance) * random.uniform(8, 26)
        aim_y = target_y + (dy / distance) * random.uniform(8, 26)
    else:
        aim_x, aim_y = target_x, target_y

    # One mid-flight hesitation on longer moves: the hand pauses as the eye
    # re-acquires the target.
    hesitate_at = random.randint(int(steps * 0.35), int(steps * 0.7)) if (
        distance > 300 and random.random() < 0.25
    ) else None

    for i in range(1, steps + 1):
        t = i / steps
        # Ease-in-out curve — humans accelerate then decelerate
        eased = 0.5 - 0.5 * math.cos(math.pi * t)
        x, y = _bezier_curve((start_x, start_y), ctrl1, ctrl2, (aim_x, aim_y), eased)
        # Tiny random wobble (sub-pixel) so the line isn't mathematically perfect
        x += random.uniform(-0.6, 0.6)
        y += random.uniform(-0.6, 0.6)
        await backend.move(x, y)

        if i == hesitate_at:
            await asyncio.sleep(random.uniform(0.06, 0.22))

        # Per-step delay — total path takes 150–500ms for typical distances
        await asyncio.sleep(random.uniform(0.005, 0.018))

    if overshooting:
        # Pull back onto the target in a few quick corrective steps.
        for i in range(1, 4):
            cx = aim_x + (target_x - aim_x) * (i / 3)
            cy = aim_y + (target_y - aim_y) * (i / 3)
            await backend.move(cx + random.uniform(-0.4, 0.4),
                               cy + random.uniform(-0.4, 0.4))
            await asyncio.sleep(random.uniform(0.012, 0.035))

    _set_last_pos(page, target_x, target_y)


# The page-level half of the stray-window problem, and the half that actually
# works. Closing a window after it opens cannot undo the interval it existed
# for: it takes focus, and on a window-managed display focus cannot be taken
# back (measured — `xdotool windowactivate`, `windowfocus`, `XSetInputFocus`, a
# pointer move and a full XTEST click all failed). Meanwhile XTEST clicks go to
# whatever window is topmost, so the stray receives them. Never creating it is
# the only version of this that holds.
#
# Both interceptions send the navigation to the CURRENT page, which is where the
# workers expect it: WTTJ's card loop, for one, waits on `self.page` and reads a
# missing navigation as a bot bounce.
# An IIFE, matching to_init_script(). `add_init_script` evaluates a string as
# SOURCE, not as a function to call — written as a bare `() => {...}` it
# creates a function value and discards it, silently doing nothing.
SUPPRESS_POPUPS_JS = """(() => {
    const nativeOpen = window.open;

    const open = function (url) {
        if (url) { window.location.assign(url); }
        // A stub rather than null: callers that poke at the return value
        // (`w.focus()`, `w.closed`) would otherwise throw where they used to work.
        return { closed: false, focus() {}, blur() {}, close() {}, document: null };
    };
    // Keep the override from announcing itself. `window.open.toString()` reading
    // as our source is a cheaper tell than the window would have been.
    try {
        Object.defineProperty(open, 'toString', {
            value: nativeOpen.toString.bind(nativeOpen), writable: true, configurable: true,
        });
        Object.defineProperty(open, 'name', { value: 'open', configurable: true });
    } catch (e) {}
    window.open = open;

    // Capture phase, so the target is gone before the default action runs.
    document.addEventListener('click', (event) => {
        const anchor = event.target && event.target.closest
            ? event.target.closest('a[target]') : null;
        if (!anchor) return;
        const target = anchor.getAttribute('target');
        if (target && target !== '_self' && target !== '_top' && target !== '_parent') {
            anchor.setAttribute('target', '_self');
        }
    }, true);
})();"""


# ---------------------------------------------------------------------------
# Stray windows
#
# A click can open a window the flow never asked for — a mis-aimed click landing
# on a `target="_blank"` link, or a page calling window.open(). Under CDP that
# was harmless: events go to a Page object and a spare window just sits there.
# Under XTEST it is not. Clicks are delivered by POINTER POSITION, so a popup
# covering the browser receives every click that follows while the code goes on
# addressing the original page. One orphaned window silently breaks the rest of
# the run.
#
# What this does NOT cover, deliberately: in-page overlays. WTTJ's "Postuler"
# opens a modal (`[data-testid="modals"]`, dismissed by the worker's own
# _handle_wttj_application_modal), which is the SAME page — `context.on("page")`
# never fires for a DOM overlay or an iframe, only for a real Page. Do not
# "improve" this into overlay detection; it would close that modal mid-apply.
# ---------------------------------------------------------------------------

# Keyed by id(context), the convention _backends and _last_mouse_pos already use.
_strays: dict[int, list] = {}
_watched: set = set()


def watch_for_stray_pages(page: Page) -> None:
    """Register once per context. Cheap, and never awaited per click.

    A run makes on the order of seventy clicks; wrapping each in
    `expect_popup()` would add its timeout to every one of them. A listener costs
    nothing until a page actually appears.
    """
    context = page.context
    key = id(context)
    if key in _watched:
        return
    _watched.add(key)
    _strays.setdefault(key, [])

    def _on_page(new_page):
        # Logged unconditionally. "Did it even see the window?" has to be
        # answerable from the log alone — a run where nothing was detected and a
        # run where detection was skipped looked identical before this line.
        logger.info("[CLICK] a new page appeared: %s", getattr(new_page, "url", "?"))
        _strays.setdefault(key, []).append(new_page)

    context.on("page", _on_page)


def _stray_count(page: Page) -> int:
    return len(_strays.get(id(page.context), ()))


async def _close_strays_since(page: Page, mark: int) -> list:
    """Close every page opened since `mark`, and put ours back on top.

    Raising the original window is the half that restores correctness: the
    pointer clicks whatever window is topmost, so a stray left in front would go
    on swallowing clicks even after the flow moved past it.
    """
    strays = _strays.get(id(page.context), [])
    closed = []
    for stray in strays[mark:]:
        try:
            # Ours or nobody's business. A page opened by something other than
            # the page we clicked from is not this click's stray.
            if stray.opener is not None:
                opener = await stray.opener()
                if opener is not None and opener is not page:
                    # Someone else's window. Say so: a bare `continue` here left
                    # no trace, which is indistinguishable from never having seen
                    # the page at all.
                    logger.info(
                        "[CLICK] leaving a window opened by another page: %s", stray.url
                    )
                    continue
            closed.append(stray.url)
            await stray.close()
        except Exception:
            logger.debug("could not close a stray page", exc_info=True)
    del strays[mark:]
    if closed:
        try:
            # Worth having on an Xvfb we own, where nothing competes for focus.
            # Powerless against a window manager: this activates a TAB over CDP
            # and has no say in which OS window the compositor focuses. On such a
            # display the real fix is SUPPRESS_POPUPS_JS, which stops the window
            # existing in the first place.
            await page.bring_to_front()
        except Exception:
            logger.debug("could not raise the main window", exc_info=True)
    return closed


# What the page reports is at a point, and whether that is the element we aimed
# at. Returns None when the question cannot be asked (a detached element, a
# locator matching more than one node), which callers treat as "no answer" rather
# than as a mismatch.
_HIT_TEST_JS = """(el, point) => {
    const hit = document.elementFromPoint(point[0], point[1]);
    if (!hit) return { ok: false, tag: null, cls: null, href: null };
    return {
        ok: el === hit || el.contains(hit),
        tag: hit.tagName,
        cls: String(hit.className || '').slice(0, 90),
        href: hit.getAttribute ? hit.getAttribute('href') : null,
    };
}"""


async def _hit_test(locator: Locator, x: float, y: float):
    try:
        return await locator.evaluate(_HIT_TEST_JS, [x, y])
    except Exception:
        return None


def _point_in(box) -> tuple[float, float]:
    """A point inside the box, pulled 25% in from the edges so a click cannot
    land on a border or a margin."""
    margin_x = box["width"] * 0.25
    margin_y = box["height"] * 0.25
    return (
        box["x"] + margin_x + random.uniform(0, box["width"] - 2 * margin_x),
        box["y"] + margin_y + random.uniform(0, box["height"] - 2 * margin_y),
    )


async def human_hover_and_click(
    locator: Locator,
    hover_min_ms: int = 80,
    hover_max_ms: int = 280,
    pre_hesitation_min: int = 150,
    pre_hesitation_max: int = 600,
    retry_on_popup: bool = True,
) -> None:
    """
    The replacement for human_click on links, cards, and buttons that pages
    might fingerprint.

    Pipeline:
      1. Pre-hesitation (user noticing the element)
      2. Curved mouse trajectory to a random point inside the element's bbox
      3. Hover (eye fixation)
      4. Click via the mouse, not the element

    This emits real mousemove + mousedown + mouseup events at the actual
    cursor coordinates, which is what detection scripts look for.
    """
    page = locator.page

    # Make sure the element is on screen first — by wheeling to it, not by
    # teleporting the scroll position (see human_scroll_to).
    await human_scroll_to(locator)
    await human_delay(pre_hesitation_min, pre_hesitation_max)

    # Get the bounding box — may be None if element isn't laid out yet
    box = await locator.bounding_box()
    if not box:
        # Fall back to a regular click; nothing we can do without geometry
        await locator.click(timeout=120000)
        return

    target_x, target_y = _point_in(box)

    # Curved trajectory to the target
    await human_mouse_move(page, target_x, target_y)

    # Eye-fixation hover
    await asyncio.sleep(random.uniform(hover_min_ms / 1000, hover_max_ms / 1000))

    # Ask the page what is actually under the cursor before pressing.
    #
    # An OS-level click goes to a COORDINATE. Playwright's own .click() refuses
    # when another element intercepts that point; going through XTEST gave that
    # protection up, and a box read a moment ago can be stale — a hydrating SPA
    # moves things, and a responsive layout ships the same control twice at two
    # breakpoints with only one of them laid out. The click then lands on
    # whatever really occupies the pixel, silently. On WTTJ that opened a second
    # window; on a page where the neighbour is inert it would just be a step that
    # quietly did not happen, which is worse for being invisible.
    hit = await _hit_test(locator, target_x, target_y)
    if hit is not None and not hit["ok"]:
        logger.info(
            "click at (%.0f, %.0f) would land on <%s class=%r href=%r>, not the target"
            " -> re-aiming", target_x, target_y, hit["tag"], hit["cls"], hit["href"],
        )
        # One re-aim from a fresh box. Most misses are a stale layout, and a box
        # re-read after the pointer has already travelled there is usually right.
        box = await locator.bounding_box()
        if box:
            target_x, target_y = _point_in(box)
            await human_mouse_move(page, target_x, target_y)
            await asyncio.sleep(random.uniform(hover_min_ms / 1000, hover_max_ms / 1000))
            hit = await _hit_test(locator, target_x, target_y)
        if hit is not None and not hit["ok"]:
            # Pressed anyway, deliberately: refusing would turn a wrong click
            # into a missing one, and a missing click strands the run mid-flow
            # with no error. The log line is what makes it findable.
            logger.warning(
                "[CLICK] still on <%s class=%r href=%r> after re-aiming — clicking anyway",
                hit["tag"], hit["cls"], hit["href"],
            )

    # Real mouse click at the cursor's current position
    backend = get_backend(page)
    mark = _stray_count(page)
    await backend.button_down()
    await asyncio.sleep(random.uniform(0.04, 0.12))  # press duration
    await backend.button_up()

    # A window may follow the click rather than accompany it, so give it a beat.
    # Bounded and short: this runs after every click in the run.
    await asyncio.sleep(0.25)
    try:
        closed = await _close_strays_since(page, mark)
    except Exception:
        # Never let this reach human_click's except, which answers any failure
        # with a plain locator.click() — an unguarded click that opens another
        # window and closes nothing, i.e. the exact problem this code exists for,
        # with the evidence swallowed.
        logger.warning("[CLICK] stray-window handling failed", exc_info=True)
        return
    if not closed:
        return

    logger.warning(
        "[CLICK] the click opened %d window(s) — closed %s", len(closed), closed
    )
    if not retry_on_popup:
        # The caller has told us the action is not safe to repeat; sending an
        # application twice is not recoverable. The window is still gone.
        logger.info("[CLICK] not retrying: caller asked for a single attempt")
        return

    # Re-aim before trying again. Repeating the same coordinates would find the
    # same wrong element and open the same window — a loop, not a retry. Exactly
    # one further attempt, whatever happens to it.
    box = await locator.bounding_box()
    if not box:
        return
    target_x, target_y = _point_in(box)
    await human_mouse_move(page, target_x, target_y)
    await asyncio.sleep(random.uniform(hover_min_ms / 1000, hover_max_ms / 1000))
    mark = _stray_count(page)
    await backend.button_down()
    await asyncio.sleep(random.uniform(0.04, 0.12))
    await backend.button_up()
    await asyncio.sleep(0.25)
    closed_again = await _close_strays_since(page, mark)
    if closed_again:
        logger.warning(
            "[CLICK] the retry opened a window too (%s) — leaving it at that",
            closed_again,
        )


async def human_click_point(
    page: Page, x: float, y: float, hover_min_ms: int = 90, hover_max_ms: int = 320
) -> None:
    """Travel to a raw viewport coordinate and click it.

    The locator-free sibling of human_hover_and_click, for things that are not in
    the page at all: the browser's back button, the address bar. Under an
    OS-level backend a NEGATIVE y is meaningful — it is above the viewport, in
    the browser's own chrome — which is exactly how those targets are reached.
    """
    backend = get_backend(page)
    await human_mouse_move(page, x, y)
    await asyncio.sleep(random.uniform(hover_min_ms / 1000, hover_max_ms / 1000))
    await backend.button_down()
    await asyncio.sleep(random.uniform(0.04, 0.12))
    await backend.button_up()


async def human_hover(locator: Locator, duration_ms: int = None) -> None:
    """
    Just hover the element. Useful for behavioral noise — sometimes users
    hover a card without clicking it, especially in list views.
    """
    page = locator.page
    await human_scroll_to(locator)
    box = await locator.bounding_box()
    if not box:
        return

    target_x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
    target_y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
    await human_mouse_move(page, target_x, target_y)

    if duration_ms is None:
        duration_ms = random.randint(400, 1500)
    await asyncio.sleep(duration_ms / 1000)


# ---------------------------------------------------------------------------
# Backwards-compatible human_click — now uses trajectory by default
# ---------------------------------------------------------------------------

async def human_click(locator: Locator, hesitation: bool = True,
                      retry_on_popup: bool = True) -> None:
    """
    Clicks an element with mouse trajectory + hover (anti-fingerprint).
    Drop-in replacement for the previous human_click.
    """
    try:
        await human_hover_and_click(
            locator,
            pre_hesitation_min=200 if hesitation else 0,
            pre_hesitation_max=800 if hesitation else 1,
            retry_on_popup=retry_on_popup,
        )
    except Exception:
        # If trajectory fails for any reason, fall back to a vanilla click.
        # Better to keep the scraper running than to crash on a detection-feature.
        await locator.click(timeout=120000)


# ---------------------------------------------------------------------------
# Scrolling
# ---------------------------------------------------------------------------

async def human_scroll(page: Page, distance: int = None, pace: float = 1.0) -> None:
    """Scroll by roughly `distance` px with a human velocity profile.

    A real scroll is not uniform. The wheel spins up, coasts, and slows as the
    target comes into view; it sometimes overshoots and gets corrected; and it
    sometimes stops dead partway because something caught the eye. The previous
    implementation emitted N identical deltas separated by identical gaps, which
    is one of the easiest automation signatures to match on.

    `pace` stretches the inter-step gaps only — the wheel deltas themselves stay
    physical, because a human scrolling slowly makes MORE, smaller movements
    rather than the same movements further apart.
    """
    if distance is None:
        distance = random.randint(200, 600)

    backend = get_backend(page)
    direction = 1 if distance >= 0 else -1
    remaining = abs(distance)

    # Slower pacing means more, finer wheel notches, not slower notches.
    steps = max(4, min(22, int(remaining / random.uniform(45, 90) * min(pace, 3.0))))

    for i in range(steps):
        if remaining <= 0:
            break

        # Ease-in-out over the scroll: small deltas at both ends, largest in
        # the middle. sin() across 0..pi gives that shape directly.
        progress = (i + 0.5) / steps
        weight = math.sin(math.pi * progress)
        step = int((remaining / max(1, steps - i)) * (0.55 + weight))
        step = max(12, min(step, remaining))

        await backend.wheel(0, direction * step)
        remaining -= step

        # Occasional mid-scroll stop — something caught the eye.
        if random.random() < 0.10:
            await asyncio.sleep(random.uniform(0.25, 0.9) * pace)
        else:
            await asyncio.sleep(random.uniform(0.035, 0.14) * pace)

    # Overshoot and correct. Scroll wheels have momentum and people scroll past
    # what they were aiming for, then come back.
    if abs(distance) > 250 and random.random() < 0.28:
        overshoot = random.randint(40, 130)
        await backend.wheel(0, direction * overshoot)
        await asyncio.sleep(random.uniform(0.18, 0.55) * pace)
        await backend.wheel(0, -direction * int(overshoot * random.uniform(0.7, 1.0)))
        await asyncio.sleep(random.uniform(0.12, 0.35) * pace)


async def human_scroll_to(locator: Locator, pace: float = 1.0) -> bool:
    """Bring an element into view by WHEELING toward it.

    The gap this closes: every path into an element went through
    `scroll_into_view_if_needed()`, which jumps the scroll position with no wheel
    events at all. A page that watches scrolling sees the viewport teleport and
    an element get clicked in a place it was never scrolled to — worse than not
    scrolling, because the click coordinates are then the only evidence the
    element was ever reached.

    Aims the element into a comfortable reading band rather than the top edge,
    because that is where a person stops scrolling. Returns False only if the
    element has no geometry at all.

    Falls back to `scroll_into_view_if_needed` when the wheel makes no progress
    — an element inside its own scrollable container does not move when the page
    scrolls, and spinning the wheel at it forever is worse than a jump.
    """
    page = locator.page

    box = await locator.bounding_box()
    if box is None:
        # Not laid out yet, or parked in a container the page cannot scroll to.
        await locator.scroll_into_view_if_needed()
        box = await locator.bounding_box()
        if box is None:
            return False

    viewport = page.viewport_size or {"width": 1280, "height": 720}
    height = viewport["height"]
    previous_y = None

    for _ in range(5):
        centre = box["y"] + box["height"] / 2
        # Where a person leaves the thing they are about to click: upper-middle
        # of the window, not jammed against an edge.
        target_y = height * random.uniform(0.30, 0.55)
        delta = centre - target_y

        # Already sitting somewhere comfortable — a human would not keep nudging.
        if abs(delta) < max(50.0, box["height"] * 0.75):
            return True

        await human_scroll(page, distance=int(delta), pace=pace)
        await asyncio.sleep(random.uniform(0.08, 0.25) * pace)

        box = await locator.bounding_box()
        if box is None:
            return False

        # No movement means the page is not what scrolls this element.
        if previous_y is not None and abs(box["y"] - previous_y) < 4:
            await locator.scroll_into_view_if_needed()
            return True
        previous_y = box["y"]

    return True


async def estimate_read_seconds(page: Page, wpm: int = 260) -> float:
    """Roughly how long the visible text on this page would take to read.

    APEC already scaled its reading pause to the offer's description length;
    this generalises it so every worker reads in proportion to what is actually
    on the page instead of a flat window. Skim speed, not careful-reading speed,
    since nobody reads a job ad word by word.
    """
    try:
        text = await page.inner_text("body")
    except Exception:
        return 0.0
    words = len(text.split())
    return (words / max(1, wpm)) * 60.0


async def human_read_page(page: Page, min_seconds: float = 2.0, max_seconds: float = 6.0, pace: float = 1.0) -> None:
    """
    Simulates a user reading a page: scroll down a bit, pause, sometimes
    scroll back up, total time within the given window.
    Use this on job description pages BEFORE clicking back to the list.
    """
    total = random.uniform(min_seconds, max_seconds)
    end_time = asyncio.get_event_loop().time() + total

    while asyncio.get_event_loop().time() < end_time:
        # 70% scroll down, 20% pause, 10% scroll up a little
        roll = random.random()
        if roll < 0.7:
            await human_scroll(page, distance=random.randint(150, 400), pace=pace)
        elif roll < 0.9:
            await asyncio.sleep(random.uniform(0.4, 1.2) * pace)
        else:
            # Small scroll back up — like re-reading something
            await get_backend(page).wheel(0, -random.randint(80, 200))
            await asyncio.sleep(random.uniform(0.3, 0.8) * pace)


# ---------------------------------------------------------------------------
# Warmup
# ---------------------------------------------------------------------------

async def human_warmup(page: Page, base_url: str) -> None:
    """Brief warmup on landing — short scroll + mouse jiggle."""
    await human_delay(800, 2000)
    try:
        # Wander the mouse a little — landing-page users move it around
        for _ in range(random.randint(1, 3)):
            await human_mouse_move(
                page,
                random.uniform(300, 1200),
                random.uniform(200, 700),
            )
            await human_delay(150, 500)
        await human_scroll(page, distance=random.randint(150, 400))
        await human_delay(500, 1500)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Behavioral noise — skip decisions
# ---------------------------------------------------------------------------

def plan_card_visit_order(count: int, skip_probability: float = 0.12) -> tuple[list[int], set[int]]:
    """The order to work a page of results in, and which cards get circled back to.

    Every card on a filtered results page already matches the search, so the
    order between them carries no information and can be shuffled freely.
    Walking 1,2,3,...,n on every page of every run is one of the plainest
    automation signatures a board can log.

    But the order has to be *plausible*, not merely random. A uniform shuffle
    lands consecutive visits a mean of 7 positions apart on a 20-card page, so
    every scroll_into_view teleports up and down the list — which just trades
    the perfect 1..n sweep for a scroll trace no human produces. Instead the
    next card is drawn from the next few remaining, with an occasional step
    back to something already scrolled past: broadly downward, locally ragged,
    a mean step of about 2.7.

    Cards chosen to be passed over are moved to the END rather than dropped:
    the sweep still visits them, just later, the way someone scrolls past a
    listing and comes back to it. That is what makes skipping free — no offer
    is ever lost, so the caller's quota is unaffected.

    Returns (order, passed_over). `order` is always a permutation of
    range(count) — every card exactly once.
    """
    # `remaining` stays sorted, so its front is the next un-opened card down the
    # page. Drawing from a narrow front window is ordinary reading; drawing from
    # a wide one is a glance further down before coming back for what was
    # stepped over. The skipped-past cards are still at the front afterwards, so
    # the walk returns to them on its own — that is the backtrack.
    NEAR_WINDOW, FAR_WINDOW, GLANCE_AHEAD = 3, 9, 0.15

    remaining = list(range(count))
    visit_order = []
    while remaining:
        window = FAR_WINDOW if random.random() < GLANCE_AHEAD else NEAR_WINDOW
        index = random.randrange(0, min(len(remaining), window))
        visit_order.append(remaining.pop(index))

    passed_over = {i for i in visit_order if should_skip_card(skip_probability)}
    order = (
        [i for i in visit_order if i not in passed_over]
        + [i for i in visit_order if i in passed_over]
    )
    return order, passed_over


def should_skip_card(skip_probability: float = 0.12) -> bool:
    """
    Returns True ~12% of the time. Use to skip the occasional job card
    without opening it — real users don't click every single result.
    """
    return random.random() < skip_probability


def should_hover_without_clicking(probability: float = 0.08) -> bool:
    """
    Returns True ~8% of the time. Use to hover a card briefly then move on
    without clicking — natural browsing behavior.
    """
    return random.random() < probability

# ---------------------------------------------------------------------------
# Idle behaviour — mouse drift, list scanning, heartbeat-safe long pauses
#
# Everything below is additive. Nothing above this line changed, so the other
# workers (hellowork, wttj, teaser) keep their exact current timing.
# ---------------------------------------------------------------------------

def _viewport(page: Page) -> tuple[int, int]:
    """Viewport size, with a sane fallback when Playwright reports None."""
    try:
        vp = page.viewport_size
        if vp and vp.get("width") and vp.get("height"):
            return vp["width"], vp["height"]
    except Exception:
        pass
    return 1280, 720


async def human_idle_drift(page: Page, moves: int = None) -> None:
    """
    Wander the mouse around the viewport without clicking anything.

    This is what a person's hand does while they're reading: the cursor keeps
    moving in small, aimless arcs. A page that only ever sees mousemove events
    immediately before a click looks scripted.
    """
    if moves is None:
        moves = random.randint(2, 4)

    width, height = _viewport(page)
    try:
        for _ in range(moves):
            await human_mouse_move(
                page,
                random.uniform(width * 0.08, width * 0.92),
                random.uniform(height * 0.10, height * 0.88),
            )
            await asyncio.sleep(random.uniform(0.3, 1.5))
    except Exception:
        # Purely cosmetic behaviour — never let it break a run.
        pass


async def human_scan_list(page: Page, min_seconds: float = 2.0, max_seconds: float = 5.0, pace: float = 1.0) -> None:
    """
    Scan a list of results the way someone choosing between them does:
    down a bit, back up, drift the mouse, down again.

    Distinct from human_read_page, which models top-to-bottom reading of a
    single document. Use this on a results/search page before touching a card.
    """
    total = random.uniform(min_seconds, max_seconds)
    end_time = asyncio.get_event_loop().time() + total

    try:
        while asyncio.get_event_loop().time() < end_time:
            roll = random.random()
            if roll < 0.55:
                await human_scroll(page, distance=random.randint(180, 450), pace=pace)
            elif roll < 0.75:
                # Back up the list — comparing two entries
                await get_backend(page).wheel(0, -random.randint(100, 280))
                await asyncio.sleep(random.uniform(0.4, 1.1) * pace)
            elif roll < 0.92:
                await human_idle_drift(page, moves=1)
            else:
                await asyncio.sleep(random.uniform(0.5, 1.4) * pace)
    except Exception:
        pass


async def human_long_pause(
    min_seconds: float,
    max_seconds: float,
    on_tick=None,
    tick_every: float = 30.0,
    page: Page = None,
) -> None:
    """
    Idle for a random stretch, sliced so a caller can keep something alive.

    The agent is considered dead if its heartbeat goes stale (see
    AGENT_HEARTBEAT_STALE_SECONDS), so any pause long enough to matter MUST be
    able to beat while it waits. `on_tick` is awaited after every `tick_every`
    seconds and again at the end; failures in it are swallowed, mirroring the
    fail-soft contract of the workers' own _beat().

    Pass `page` and the cursor drifts occasionally during the pause, so the tab
    isn't perfectly frozen for the whole break.
    """
    total = random.uniform(min_seconds, max_seconds)
    if total <= 0:
        return

    tick_every = max(1.0, tick_every)
    remaining = total

    while remaining > 0:
        slice_seconds = min(tick_every, remaining)
        await asyncio.sleep(slice_seconds)
        remaining -= slice_seconds

        if on_tick is not None:
            try:
                await on_tick()
            except Exception:
                pass

        # Small chance of a cursor twitch mid-break.
        if page is not None and remaining > 0 and random.random() < 0.35:
            await human_idle_drift(page, moves=1)


def should_take_break(probability: float = 0.18) -> bool:
    """
    Returns True ~18% of the time. Use between cards / pages / submissions to
    decide whether to step away for a moment. Real sessions aren't uniform:
    people stall, read something else, come back.
    """
    return random.random() < probability


def reset_mouse_state(page: Page) -> None:
    """
    Drop the remembered cursor position for a page.

    _last_mouse_pos is keyed by id(page) and nothing evicts it, so entries pile
    up across runs and a recycled id() would hand a brand-new page some other
    page's stale cursor origin. Call this when tearing a page down.
    """
    try:
        _last_mouse_pos.pop(id(page), None)
    except Exception:
        pass
