# auto_apply_app/infrastructures/agent/human_behavior.py
import asyncio
import math
import random
from playwright.async_api import Locator, Page


# ---------------------------------------------------------------------------
# Basic delays / typing (unchanged, kept for backwards compat)
# ---------------------------------------------------------------------------

async def human_delay(min_ms: int = 500, max_ms: int = 2000) -> None:
    """Sleep for a random duration. Use BETWEEN actions, not as a substitute
    for semantic waits (wait_for_selector, etc.)."""
    delay_seconds = random.uniform(min_ms / 1000, max_ms / 1000)
    await asyncio.sleep(delay_seconds)


async def human_type(locator: Locator, text: str, min_delay: int = 50, max_delay: int = 150) -> None:
    """Types text character-by-character with realistic per-key jitter.
    Always clears the field first."""
    if not text:
        return
    try:
        await locator.clear()
    except Exception:
        try:
            await locator.click()
            await locator.press("Control+A")
            await locator.press("Delete")
        except Exception:
            pass
    delay = random.randint(min_delay, max_delay)
    await locator.type(text, delay=delay)


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
    start_x, start_y = _get_last_pos(page)
    dx = target_x - start_x
    dy = target_y - start_y
    distance = math.hypot(dx, dy)

    if distance < 5:
        # Already close enough — just move and bail
        await page.mouse.move(target_x, target_y)
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

    for i in range(1, steps + 1):
        t = i / steps
        # Ease-in-out curve — humans accelerate then decelerate
        eased = 0.5 - 0.5 * math.cos(math.pi * t)
        x, y = _bezier_curve((start_x, start_y), ctrl1, ctrl2, (target_x, target_y), eased)
        # Tiny random wobble (sub-pixel) so the line isn't mathematically perfect
        x += random.uniform(-0.6, 0.6)
        y += random.uniform(-0.6, 0.6)
        await page.mouse.move(x, y)
        # Per-step delay — total path takes 150–500ms for typical distances
        await asyncio.sleep(random.uniform(0.005, 0.018))

    _set_last_pos(page, target_x, target_y)


async def human_hover_and_click(
    locator: Locator,
    hover_min_ms: int = 80,
    hover_max_ms: int = 280,
    pre_hesitation_min: int = 150,
    pre_hesitation_max: int = 600,
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

    # Make sure the element is on screen first
    await locator.scroll_into_view_if_needed()
    await human_delay(pre_hesitation_min, pre_hesitation_max)

    # Get the bounding box — may be None if element isn't laid out yet
    box = await locator.bounding_box()
    if not box:
        # Fall back to a regular click; nothing we can do without geometry
        await locator.click(timeout=120000)
        return

    # Click at a randomized point *inside* the element, not dead center.
    # Pull in from the edges by 25% to avoid hitting borders / margins.
    margin_x = box["width"] * 0.25
    margin_y = box["height"] * 0.25
    target_x = box["x"] + margin_x + random.uniform(0, box["width"] - 2 * margin_x)
    target_y = box["y"] + margin_y + random.uniform(0, box["height"] - 2 * margin_y)

    # Curved trajectory to the target
    await human_mouse_move(page, target_x, target_y)

    # Eye-fixation hover
    await asyncio.sleep(random.uniform(hover_min_ms / 1000, hover_max_ms / 1000))

    # Real mouse click at the cursor's current position
    await page.mouse.down()
    await asyncio.sleep(random.uniform(0.04, 0.12))  # press duration
    await page.mouse.up()


async def human_hover(locator: Locator, duration_ms: int = None) -> None:
    """
    Just hover the element. Useful for behavioral noise — sometimes users
    hover a card without clicking it, especially in list views.
    """
    page = locator.page
    await locator.scroll_into_view_if_needed()
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

async def human_click(locator: Locator, hesitation: bool = True) -> None:
    """
    Clicks an element with mouse trajectory + hover (anti-fingerprint).
    Drop-in replacement for the previous human_click.
    """
    try:
        await human_hover_and_click(
            locator,
            pre_hesitation_min=200 if hesitation else 0,
            pre_hesitation_max=800 if hesitation else 1,
        )
    except Exception:
        # If trajectory fails for any reason, fall back to a vanilla click.
        # Better to keep the scraper running than to crash on a detection-feature.
        await locator.click(timeout=120000)


# ---------------------------------------------------------------------------
# Scrolling
# ---------------------------------------------------------------------------

async def human_scroll(page: Page, distance: int = None) -> None:
    """Scrolls the page by a random amount with multiple wheel events."""
    if distance is None:
        distance = random.randint(200, 600)

    steps = random.randint(3, 6)
    step_size = distance // steps

    for _ in range(steps):
        await page.mouse.wheel(0, step_size)
        await human_delay(80, 200)


async def human_read_page(page: Page, min_seconds: float = 2.0, max_seconds: float = 6.0) -> None:
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
            await human_scroll(page, distance=random.randint(150, 400))
        elif roll < 0.9:
            await asyncio.sleep(random.uniform(0.4, 1.2))
        else:
            # Small scroll back up — like re-reading something
            await page.mouse.wheel(0, -random.randint(80, 200))
            await asyncio.sleep(random.uniform(0.3, 0.8))


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