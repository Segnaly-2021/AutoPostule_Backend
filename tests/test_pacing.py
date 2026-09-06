"""Pacing tiers, the budget guard, and the heartbeat invariant.

The heartbeat test is the important one. WTTJ and HelloWork had no budget guard
and no heartbeat while idle, so slowing them down without the mixin would have
had them declared dead at AGENT_HEARTBEAT_STALE_SECONDS rather than merely slow.
"""
import ast
import re
import time
from pathlib import Path
from unittest import mock

import pytest

from auto_apply_app.application.use_cases.agent_state_use_cases import (
    AGENT_HEARTBEAT_STALE_SECONDS,
)
from auto_apply_app.infrastructures.agent import pacing
from auto_apply_app.infrastructures.agent.pacing import (
    DEFAULT_HUMAN_BUDGET_S,
    HumanPacing,
    PAUSE_CEILING_S,
    Tier,
)

WORKER_DIR = Path("auto_apply_app/infrastructures/agent/workers")
LIVE_WORKERS = [
    WORKER_DIR / "apec" / "apec_worker.py",
    WORKER_DIR / "wttj" / "wttj_worker.py",
    WORKER_DIR / "hellowork" / "hw_worker.py",
]


class FakeWorker(HumanPacing):
    page = None

    def __init__(self, prefix="APEC"):
        self.beats = 0
        self.logs = []
        self._init_pacing(prefix)

    def _plog(self, message):
        self.logs.append(message)

    async def _beat(self, state):
        self.beats += 1


@pytest.fixture
def worker():
    return FakeWorker()


# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------

def test_tiers_are_ordered_from_reflex_to_deliberate():
    order = [Tier.REFLEX, Tier.LOGIN, Tier.NAV, Tier.SCROLL,
             Tier.CARD, Tier.READ, Tier.SUBMIT]
    values = [t.value for t in order]
    assert values == sorted(values), f"tiers out of order: {values}"


def test_reflex_actions_are_not_slowed(worker):
    """Keystrokes and click presses are limited by hands, not attention.
    Stretching them is what makes automation look broken rather than human."""
    assert worker._scaled(0.5, Tier.REFLEX) == 0.5


def test_login_stays_brisk(worker):
    """People type a saved password quickly, so there is little time to be won
    by dawdling here — but not instantly, which is its own tell. Login is the
    briskest deliberate tier; the weight belongs in the card loop."""
    assert worker._scaled(4.0, Tier.LOGIN) < 10.0
    assert Tier.LOGIN.value < Tier.NAV.value < Tier.CARD.value


def test_the_deliberate_tiers_carry_the_weight(worker):
    """Cards, reading, scrolling and submitting are where the time goes.

    Stated as an ordering rather than as magnitudes. The magnitudes are a tuning
    decision that has changed twice already and will again; what must not drift
    is that deliberate acts cost more than page transitions — that ranking is
    what makes a run's rhythm read as attention rather than as a schedule.

    Deliberately NOT asserting a ratio between tiers any more. How long a
    particular wait runs now lives in the seconds passed at its call site, not in
    the gap between two multipliers: the between-cards gap and the hesitation
    before clicking a card share `CARD` and differ by 5x, which no relationship
    between `CARD` and `SUBMIT` could express."""
    for tier in (Tier.SCROLL, Tier.CARD, Tier.READ, Tier.SUBMIT):
        assert tier.value > Tier.NAV.value, f"{tier.name} is no slower than a nav"


def test_no_two_tiers_share_a_value():
    """Equal values in an Enum are not two tiers, they are ALIASES: `Tier.CARD`
    would BE `Tier.NAV`, name and all, and every card pause would quietly take
    the wrong multiplier. `@unique` on the enum makes that an ImportError, and
    this test says why anyone should keep it there."""
    values = [t.value for t in Tier]
    assert len(values) == len(set(values)) == 7


async def test_no_single_pause_can_exceed_two_minutes(worker):
    """The ceiling is enforced in `_pause`, not asked of its callers.

    A tier multiplier moves every call site at once: `_pause(8, 20, SUBMIT)` sat
    unedited on the page while a retune turned it into a four-and-a-half minute
    wait. A limit that only holds while nobody touches an unrelated number is not
    a limit."""
    worker._start_pacing()
    captured = {}

    async def fake_long_pause(low, high, **kw):
        captured["low"], captured["high"] = low, high

    with mock.patch(f"{pacing.__name__}.human_long_pause", fake_long_pause):
        await worker._pause(None, 600.0, 900.0, tier=Tier.SUBMIT)

    assert captured["high"] <= PAUSE_CEILING_S
    assert captured["low"] <= PAUSE_CEILING_S


def test_reading_spans_its_band_instead_of_sitting_at_one_end(worker):
    """A short offer must read briefly and a long one near the ceiling.

    Both halves matter. A dwell that ignores length is a fixed pause on every
    page, which is the tell the mechanism exists to remove; a dwell that only
    ever sits at the floor means raising a board's ceiling changes nothing, which
    is how WTTJ ended up with a 150s limit and a 45s read."""
    worker._READ_BAND_S = (60.0, 180.0)
    worker._READ_LONG_CHARS = 5400

    short_lo, short_hi = worker._read_bounds(200)
    long_lo, long_hi = worker._read_bounds(9000)

    assert short_lo >= 60.0, "a short offer dropped below the band"
    assert long_hi <= 180.0, "a long offer ran past the ceiling"
    assert long_hi > short_hi * 2, "length barely moves the dwell"


def test_a_read_is_never_a_single_fixed_duration(worker):
    """min and max must stay apart, at both ends of the band. Collapsing them
    would make every long offer read for exactly the same time."""
    worker._READ_BAND_S = (60.0, 180.0)
    worker._READ_LONG_CHARS = 5400

    for desc_len in (0, 200, 2700, 5400, 50000):
        low, high = worker._read_bounds(desc_len)
        assert low < high, f"dwell collapsed to a constant at {desc_len} chars"


def test_each_board_reads_for_its_own_length():
    """WTTJ was measured to deserve more attention than APEC, and HelloWork
    less. That is a per-board judgement, so it lives on the worker rather than in
    a shared constant."""
    from auto_apply_app.infrastructures.agent.workers.apec.apec_worker import ApecWorker
    from auto_apply_app.infrastructures.agent.workers.wttj.wttj_worker import (
        WelcomeToTheJungleWorker,
    )
    from auto_apply_app.infrastructures.agent.workers.hellowork.hw_worker import (
        HelloWorkWorker,
    )

    wttj = WelcomeToTheJungleWorker._READ_BAND_S
    apec = ApecWorker._READ_BAND_S
    hw = HelloWorkWorker._READ_BAND_S

    assert wttj[1] > apec[1] > hw[1], f"reading order wrong: {wttj} {apec} {hw}"
    assert (wttj, apec, hw) == ((60.0, 180.0), (50.0, 150.0), (30.0, 90.0))


def test_the_pause_ceiling_is_the_one_that_was_asked_for():
    """Named, so a later 'let's speed things up' has to change a number somebody
    chose rather than quietly widen a range."""
    assert PAUSE_CEILING_S == 150.0


def test_the_gap_between_cards_is_allowed_to_run_long(worker):
    """The one wait deliberately near its ceiling. Back-to-back card opens are
    the most obvious pattern in a run's request timing, so when everything else
    was cut this was widened instead — trimming uniformly would have taken the
    most valuable wait down with the rest."""
    low = worker._scaled(6.0, Tier.CARD)
    high = min(worker._scaled(30.0, Tier.CARD), PAUSE_CEILING_S)

    assert high <= PAUSE_CEILING_S
    assert high >= 60.0, "the between-cards gap has been trimmed back to nothing"
    assert low >= 20.0


def test_submitting_is_the_most_deliberate_act():
    assert Tier.SUBMIT.value == max(t.value for t in Tier)


def test_per_board_env_overrides_the_global(monkeypatch):
    monkeypatch.setenv("AGENT_HUMAN_PACE", "10")
    monkeypatch.setenv("WTTJ_HUMAN_PACE", "3")
    assert FakeWorker("APEC")._pace == 10.0
    assert FakeWorker("WTTJ")._pace == 3.0


def test_garbage_env_falls_back_instead_of_crashing(monkeypatch):
    monkeypatch.setenv("AGENT_HUMAN_PACE", "not-a-number")
    monkeypatch.setenv("AGENT_HUMAN_BUDGET_S", "")
    w = FakeWorker("APEC")
    assert w._pace == 1.0
    assert w._human_budget_s == DEFAULT_HUMAN_BUDGET_S


# ---------------------------------------------------------------------------
# Budget guard
# ---------------------------------------------------------------------------

def test_budget_guard_disarms_pacing_before_the_task_timeout(worker):
    assert not worker._over_budget()
    worker._start_pacing()
    assert not worker._over_budget()
    worker._run_started_at = time.monotonic() - (worker._human_budget_s + 1)
    assert worker._over_budget()


def test_default_budget_leaves_room_under_the_task_timeout():
    """--task-timeout=9000 in deploy-browser.yml, with --max-retries=0, so a
    timeout is a permanent failure. The budget must finish well before it."""
    assert DEFAULT_HUMAN_BUDGET_S < 9000


def test_default_budget_fits_inside_the_sticky_proxy_window():
    """TWOCAPTCHA_USERNAME_TEMPLATE carries sessTime-120, so the exit IP holds
    for 2 hours. A run outliving that changes IP mid-session, which is a worse
    signal than anything the pacing buys."""
    assert DEFAULT_HUMAN_BUDGET_S < 120 * 60


def test_pacing_clock_start_is_idempotent_within_a_run(worker):
    """A run boots two browser tracks; the second must not restart the clock.
    Idempotency is keyed on the run token — see
    test_pacing_clock_resets_between_runs for why it must NOT be per-process."""
    worker._start_pacing("run-A")
    first = worker._run_started_at
    worker._start_pacing("run-A")
    assert worker._run_started_at == first


async def test_pauses_taper_rather_than_falling_off_a_cliff(worker):
    """As the budget runs out, pauses shrink toward zero instead of switching
    off abruptly — a sudden change in action cadence is itself a pattern."""
    worker._start_pacing()
    worker._run_started_at = time.monotonic() - (worker._human_budget_s - 2)
    assert 0 < worker._budget_remaining_s() <= 3

    started = time.monotonic()
    await worker._pause(None, 8.0, 20.0, tier=Tier.SUBMIT)  # would be ~112-280s
    assert time.monotonic() - started < 6, "pause ignored the remaining budget"


async def test_over_budget_pause_is_a_noop(worker):
    worker._start_pacing()
    worker._run_started_at = time.monotonic() - (worker._human_budget_s + 10)
    started = time.monotonic()
    await worker._pause(None, 30.0, 60.0, tier=Tier.SUBMIT)
    await worker._maybe_break(None, "test")
    await worker._distracted_stop(None, probability=1.0)
    assert time.monotonic() - started < 0.5


async def test_breaks_cluster_with_sustained_activity(worker):
    """A flat coin flip is uniform in a way attention is not."""
    worker._start_pacing()
    worker._human_budget_s = 0.0  # pauses no-op; we only measure the decision
    worker._run_started_at = time.monotonic() - 1

    worker._actions_since_break = 1
    early = min(0.85, 0.04 + (1 ** 1.5) / 90)
    worker._actions_since_break = 20
    late = min(0.85, 0.04 + (20 ** 1.5) / 90)
    assert late > early * 5, "break probability does not climb with activity"


async def test_pause_beats_the_heartbeat_while_idling(worker):
    """A pause longer than the staleness cutoff must beat while it waits.

    human_long_pause clamps tick_every to a 1s floor, so the real invariant is
    the GAP between beats, not their count: it must stay well under
    AGENT_HEARTBEAT_STALE_SECONDS.
    """
    from auto_apply_app.infrastructures.agent.human_behavior import human_long_pause

    stamps = []

    async def record():
        stamps.append(time.monotonic())

    await human_long_pause(3.0, 3.0, on_tick=record, tick_every=1.0)

    assert len(stamps) >= 3, f"only beat {len(stamps)} times during a 3s pause"
    gaps = [stamps[i + 1] - stamps[i] for i in range(len(stamps) - 1)]
    assert max(gaps) < AGENT_HEARTBEAT_STALE_SECONDS, (
        f"largest gap between beats was {max(gaps):.1f}s"
    )


async def test_worker_pause_always_beats_at_least_once(worker):
    """The mixin's own _pause is what the workers actually call. It slices on a
    30s tick, so a short pause beats once (at the end) and a long one beats
    repeatedly — either way it never idles silently."""
    worker._pace = 1.0
    worker._human_budget_s = 600.0
    worker._start_pacing()
    await worker._pause(None, 1.0, 1.0, tier=Tier.REFLEX)
    assert worker.beats >= 1, "_pause idled without beating at all"


def test_pause_tick_interval_is_well_under_the_staleness_cutoff():
    """The real guarantee for multi-minute pauses: _pause hands human_long_pause
    a 30s tick, so the longest possible gap between two heartbeat writes during
    an idle stretch is 30s against a 180s cutoff. Asserting the source keeps a
    future edit from quietly raising it."""
    import inspect
    from auto_apply_app.infrastructures.agent import pacing

    source = inspect.getsource(pacing.HumanPacing._pause)
    match = re.search(r"tick_every=([\d.]+)", source)
    assert match, "_pause no longer passes tick_every"
    assert float(match.group(1)) < AGENT_HEARTBEAT_STALE_SECONDS / 2


# ---------------------------------------------------------------------------
# Static invariant across the live workers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", LIVE_WORKERS, ids=lambda p: p.stem)
def test_no_bare_sleep_can_outlast_the_heartbeat(path):
    """Any literal asyncio.sleep longer than the staleness cutoff would mark the
    agent dead. Long waits must go through human_long_pause(on_tick=...).
    """
    tree = ast.parse(path.read_text())
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "sleep"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        value = node.args[0].value
        if isinstance(value, (int, float)) and value >= AGENT_HEARTBEAT_STALE_SECONDS:
            offenders.append((node.lineno, value))
    assert not offenders, f"{path}: bare sleeps at/over the staleness cutoff: {offenders}"


@pytest.mark.parametrize("path", LIVE_WORKERS, ids=lambda p: p.stem)
def test_every_live_worker_has_the_pacing_mixin(path):
    src = path.read_text()
    assert "HumanPacing" in src, f"{path} is not paced"
    assert "_init_pacing(" in src, f"{path} never initialises its pace"
    assert "_start_pacing(" in src, f"{path} never starts its budget clock"


@pytest.mark.parametrize("path", LIVE_WORKERS, ids=lambda p: p.stem)
def test_every_live_worker_resets_mouse_state_on_cleanup(path):
    """_last_mouse_pos is keyed by id(page) and nothing evicts it, so a recycled
    id hands a new page some other page's stale cursor origin."""
    src = path.read_text()
    assert "reset_mouse_state" in src, f"{path} leaks _last_mouse_pos entries"


@pytest.mark.parametrize("path", LIVE_WORKERS, ids=lambda p: p.stem)
def test_no_worker_pins_a_hardcoded_user_agent(path):
    """Every fingerprintless run used to share one identical Chrome 131 UA."""
    src = path.read_text()
    assert not re.search(r'"Mozilla/5\.0 \(Windows NT', src), (
        f"{path} still hardcodes a user agent"
    )


async def test_pacing_clock_resets_between_runs(worker):
    """Workers are process-singletons (create_agent behind a cached_property) and
    LocalDispatcher reuses the process across runs. A clock that only ever set
    itself once meant the first run burned the budget and every run after it
    found _over_budget() already true — silently disabling ALL pacing for the
    life of the process. Prod hid this (one process per Job); dev showed it as
    "the pacing does nothing".
    """
    worker._start_pacing("run-A")
    worker._run_started_at = time.monotonic() - (worker._human_budget_s + 1)
    assert worker._over_budget()

    worker._start_pacing("run-B")
    assert not worker._over_budget(), "a new run inherited the previous run's exhausted budget"


def test_both_tracks_of_one_run_share_a_clock(worker):
    """A run boots a scrape browser and a submit browser. The second must not
    restart the budget and hand the run double the time."""
    worker._start_pacing("run-A")
    started = worker._run_started_at
    worker._start_pacing("run-A")
    assert worker._run_started_at == started


def test_missing_run_token_resets_rather_than_disables(worker):
    """Over-pacing a run is bounded and visible; silently not pacing is not."""
    worker._start_pacing(None)
    worker._run_started_at = time.monotonic() - (worker._human_budget_s + 1)
    worker._start_pacing(None)
    assert not worker._over_budget()


@pytest.mark.parametrize("path", LIVE_WORKERS, ids=lambda p: p.stem)
def test_workers_pass_the_run_token_to_the_clock(path):
    src = path.read_text()
    assert '_start_pacing(state.get("run_token"))' in src, (
        f"{path} starts its clock without a run token, so the clock can never reset"
    )
