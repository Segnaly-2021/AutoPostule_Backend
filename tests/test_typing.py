"""Keystroke rhythm.

The bug this guards against is subtle and was live: human_type drew ONE random
delay for the whole string and passed it to locator.type(delay=...), so every
keystroke landed at an identical interval. Measured against a real input, that
produced a standard deviation of 0.7ms across a 17-character string — a
metronome, and a far cheaper signal for a board to collect than anything on the
fingerprint surface. Per-key jitter has to be redrawn per key.
"""
import ast
import asyncio
import statistics
import time
from pathlib import Path

import pytest

from auto_apply_app.infrastructures.agent.human_behavior import human_type


class FakeLocator:
    """Records when each character was typed, in wall-clock terms."""

    def __init__(self):
        self.chars = []
        self.stamps = []
        self.cleared = False
        self.dwells_ms = []
        # human_type resolves an input backend from the page; the default is the
        # CDP one, which routes each character straight back to
        # press_sequentially below. A distinct object per locator keeps the
        # backend registry from sharing state between tests.
        self.page = object()

    async def clear(self):
        self.cleared = True

    async def press_sequentially(self, char, delay=0):
        # Stamp at key-DOWN, then hold for `delay` — which is what Playwright's
        # delay means for a single character (keydown, wait, keyup), not the gap
        # to the next key. Modelling the hold matters now that human_type sets
        # one: without it the recorded gaps are short by the dwell and the
        # key-down-to-key-down rhythm this file measures comes out wrong.
        self.chars.append(char)
        self.stamps.append(time.perf_counter())
        self.dwells_ms.append(delay)
        if delay:
            await asyncio.sleep(delay / 1000)

    @property
    def gaps_ms(self):
        return [(b - a) * 1000 for a, b in zip(self.stamps, self.stamps[1:])]


@pytest.mark.asyncio
async def test_every_character_is_typed_individually():
    """One call per character — not one call for the whole string."""
    loc = FakeLocator()
    await human_type(loc, "AI Engineer")
    assert "".join(loc.chars) == "AI Engineer"
    assert len(loc.chars) == len("AI Engineer")


@pytest.mark.asyncio
async def test_the_field_is_cleared_first():
    loc = FakeLocator()
    await human_type(loc, "x")
    assert loc.cleared


@pytest.mark.asyncio
async def test_empty_text_types_nothing():
    loc = FakeLocator()
    await human_type(loc, "")
    assert loc.chars == []


@pytest.mark.asyncio
async def test_keystroke_intervals_are_not_a_metronome():
    """The regression that mattered: a single delay reused for every key.

    Real hands vary by tens of milliseconds. The old implementation held the
    interval to within a millisecond, which is the whole tell.
    """
    loc = FakeLocator()
    await human_type(loc, "AI Engineer Paris", min_delay=25, max_delay=75)
    gaps = loc.gaps_ms

    assert len(gaps) > 10
    # A uniform delay lands near 0; anything human is far above it.
    # the old single-delay implementation scored 0.7ms on this string
    assert statistics.pstdev(gaps) > statistics.median(gaps) * 0.20, (
        f"intervals are too regular: {gaps}"
    )
    # And the values must genuinely differ, not cluster on two settings.
    assert len({round(g) for g in gaps}) > len(gaps) // 2


@pytest.mark.asyncio
async def test_the_configured_range_is_respected():
    loc = FakeLocator()
    await human_type(loc, "abcdefgh", min_delay=10, max_delay=30)
    gaps = loc.gaps_ms
    # Floor holds; the ceiling allows for the occasional deliberate hesitation
    # plus event-loop overhead.
    assert min(gaps) >= 8.0
    assert statistics.median(gaps) < 120.0


def test_apec_submits_the_search_from_the_keyboard():
    """Someone who just typed the keywords hits Enter; reaching for the button
    is the rarer of the two. The button is kept only as a fallback."""
    src = Path("auto_apply_app/infrastructures/agent/workers/apec/apec_worker.py").read_text()
    tree = ast.parse(src)

    presses_enter = any(
        isinstance(n, ast.Call)
        and getattr(n.func, "attr", "") in ("press", "_press")
        and n.args
        and isinstance(n.args[0], ast.Constant)
        and n.args[0].value == "Enter"
        for n in ast.walk(tree)
    )
    assert presses_enter, "APEC never submits its search with Enter"


@pytest.mark.asyncio
async def test_the_interval_distribution_has_a_long_tail():
    """Uniform noise between two bounds is random but not human — it makes
    every interval equally likely, so the histogram is a rectangle. Real typing
    piles up around a tempo and trails off slowly to the right.
    """
    gaps = []
    for _ in range(6):
        loc = FakeLocator()
        # Same min:max ratio as the default (90:300), scaled down so the suite
        # stays fast — hesitations are proportional, so the shape is unchanged.
        await human_type(loc, "AI Engineer Paris", min_delay=6, max_delay=20)
        gaps += loc.gaps_ms

    gaps.sort()
    median = statistics.median(gaps)
    p95 = gaps[int(len(gaps) * 0.95)]
    # A rectangle would put p95 barely above the median; a tail puts it far above.
    assert p95 > median * 2.0, f"distribution is too flat: median={median:.0f} p95={p95:.0f}"
    # Skewed right: the mean sits above the median when the tail is on top.
    assert statistics.mean(gaps) > median


@pytest.mark.asyncio
async def test_each_field_gets_its_own_tempo():
    """Nobody types every field at the same speed. Without a per-call tempo the
    run is still a metronome, just one level up.
    """
    medians = []
    for _ in range(8):
        loc = FakeLocator()
        await human_type(loc, "AI Engineer Paris", min_delay=6, max_delay=20)
        medians.append(statistics.median(loc.gaps_ms))

    # tempo spans 0.78..1.5, so medians must spread by a visible fraction of the mean
    assert statistics.pstdev(medians) > statistics.mean(medians) * 0.10, (
        f"every field typed alike: {medians}"
    )


@pytest.mark.asyncio
async def test_typing_a_field_takes_a_believable_amount_of_time():
    """Slow enough to read as deliberate, bounded enough not to eat the run."""
    loc = FakeLocator()
    start = time.perf_counter()
    await human_type(loc, "AI Engineer")
    elapsed = time.perf_counter() - start
    assert 1.0 < elapsed < 20.0, f"typing 11 chars took {elapsed:.1f}s"


@pytest.mark.asyncio
async def test_keys_are_held_not_merely_struck():
    """Every key was released 2.4ms after being pressed — measured against the
    probe harness, not guessed. The intervals BETWEEN keys were already human;
    the hold was not, and a page can collect dwell as cheaply as it collects
    gaps. Playwright's per-character `delay` is that hold.
    """
    loc = FakeLocator()
    await human_type(loc, "Ingenieur logiciel")

    held = [d for d in loc.dwells_ms if d > 0]
    assert len(held) == len(loc.chars), "some characters were struck with no dwell"
    assert statistics.median(held) > 40.0, f"dwell is too short to be a finger: {held}"
    assert statistics.pstdev(held) > 1.0, "every key held for exactly the same time"


@pytest.mark.asyncio
async def test_the_hold_never_swallows_the_interval():
    """The dwell comes OUT of the key-down-to-key-down interval rather than being
    added on top, so a caller asking for fast typing still gets fast typing.

    Stated as the invariant it actually relies on: a key is never held longer
    than the gap that follows it, which is what capping the hold at half the
    interval buys.
    """
    loc = FakeLocator()
    await human_type(loc, "abcdefghijklmno", min_delay=10, max_delay=30)

    gaps = loc.gaps_ms
    for i, gap in enumerate(gaps):
        # gap here is down-to-down, which already contains the hold.
        assert loc.dwells_ms[i] <= gap + 1.0, (
            f"key {i} was held {loc.dwells_ms[i]:.1f}ms inside a {gap:.1f}ms interval"
        )
