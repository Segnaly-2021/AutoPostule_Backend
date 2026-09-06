# auto_apply_app/infrastructures/agent/pacing.py
"""Human pacing, shared by every browser worker.

Generalises the machinery APEC grew on its own (_pace / _human_budget_s /
_over_budget / _pause / _maybe_break) so WTTJ and HelloWork stop running at
machine speed — neither had a budget guard, and neither beat the heartbeat while
idle, so simply making them slower would have got them declared dead.

The multiplier is TIERED, not flat. Where the time goes matters more than how
much of it there is: a person lingers over a job card, reads an offer, hesitates
before submitting, and scrolls at an uneven speed — and then logs in briskly,
because typing a saved password is not a decision. A flat multiplier applied to
everything would put 25 seconds between two form fields, which reads as a hung
browser, not a human.
"""
import asyncio
import logging
import os
import random
import time
from enum import Enum, unique
from typing import Optional

from auto_apply_app.infrastructures.agent.input.backend import (
    BACKEND_X11,
    attach_backend,
    detach_backend,
    get_backend,
    resolve_backend_name,
)
from auto_apply_app.infrastructures.agent.fingerprint_alignment import (
    fit_fingerprint_to_display,
)
from auto_apply_app.infrastructures.agent.input.display import (
    open_display,
    screen_size_for,
    window_geometry_for,
)
from auto_apply_app.infrastructures.agent.human_behavior import (
    human_click,
    human_idle_drift,
    human_long_pause,
    human_scroll,
    human_scroll_to,
    human_type,
    watch_for_stray_pages,
    SUPPRESS_POPUPS_JS,
)


# Wall-clock ceiling on deliberate pacing. Past it, every deliberate pause
# collapses to a no-op so the pacing itself can never be what trips the Cloud Run
# Job task timeout (--task-timeout in .github/workflows/deploy-browser.yml).
# Keep this comfortably under both that timeout AND the proxy's sticky-session
# lifetime (sessTime in TWOCAPTCHA_USERNAME_TEMPLATE) — an exit IP that rotates
# mid-run is a worse signal than anything this module buys.
DEFAULT_HUMAN_BUDGET_S = 6300.0  # 1h45m
DEFAULT_HUMAN_PACE = 1.0


# Testing-only escape hatch for the typing layer.
#
# A 1500-character cover letter typed key by key is minutes per application, and
# under the x11 backend every character is its own `xdotool` subprocess — which
# makes iterating on a worker impractical. `fill()` sets the value through
# `Input.insertText` and produces NO key events whatsoever, which is precisely
# what `_type` exists to avoid. So this is opt-in, never the default, and it says
# so in the log every single time it fires.
#
#   AGENT_FAST_TYPING=long   long-form text only (cover letters) — recommended
#   AGENT_FAST_TYPING=all    every field, including logins
#   unset                    normal human typing
#
# Never set this on a real run. It is the one switch in this module that makes
# the browser measurably less human rather than more.
FAST_TYPING_LONG_FORM_CHARS = 400


def fast_typing_mode() -> str:
    """`AGENT_FAST_TYPING`: off (default), long, or all."""
    raw = (os.getenv("AGENT_FAST_TYPING") or "").strip().lower()
    if raw in ("all", "always", "everything"):
        return "all"
    if raw in ("1", "true", "yes", "on", "long", "longform", "long-form"):
        return "long"
    if raw not in ("", "0", "off", "false", "no"):
        logger.warning("Unknown AGENT_FAST_TYPING=%r -> typing normally", raw)
    return "off"


# ----------------------------------------------------------------------
# Residential bandwidth
#
# Residential proxy traffic is billed per GB, and on a job board the bytes are
# not in the text we came for -- they are in hero images, logo sprites and the
# occasional autoplaying video. The agent reads the DOM; it never looks at a
# picture. So those bytes are pure cost.
#
#   AGENT_BLOCK_HEAVY_ASSETS=on          images + video/audio  -- the money saver
#   AGENT_BLOCK_HEAVY_ASSETS=aggressive  the above + web fonts
#   unset / off                          fetch everything (default)
#
# Default off because this is the kind of change that trades money for
# detectability, and that trade should be made deliberately -- see
# _conserve_bandwidth for how the images case is made survivable.
BLOCKED_TYPES = {
    "on": {"image", "media"},
    "aggressive": {"image", "media", "font"},
}

# Hosts that are never blocked, whatever the policy. A captcha or an SSO
# challenge that cannot draw itself is a login that cannot complete, and the
# failure looks like a selector bug rather than a bandwidth setting.
ASSET_ALLOW_HOSTS = (
    "challenges.cloudflare.com",
    "hcaptcha.com",
    "recaptcha.net",
    "gstatic.com/recaptcha",
    "google.com/recaptcha",
    "captcha",
    "turnstile",
)

# 43 bytes of transparent GIF. Served locally, so it costs no proxy traffic --
# but the page still sees an image that LOADED, which abort() does not give it.
# onload fires, <img> keeps its box, and the lazy-loaders that job boards use to
# drive infinite scroll do not stall waiting for a request that failed.
_TRANSPARENT_GIF = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c000000000100010000020144003b"
)


def blocked_asset_types() -> frozenset:
    """`AGENT_BLOCK_HEAVY_ASSETS`: off (default), on, or aggressive."""
    raw = (os.getenv("AGENT_BLOCK_HEAVY_ASSETS") or "").strip().lower()
    if raw in ("aggressive", "max", "all"):
        return frozenset(BLOCKED_TYPES["aggressive"])
    if raw in ("1", "true", "yes", "on", "media", "images"):
        return frozenset(BLOCKED_TYPES["on"])
    if raw not in ("", "0", "off", "false", "no"):
        logger.warning(
            "Unknown AGENT_BLOCK_HEAVY_ASSETS=%r -> fetching everything", raw
        )
    return frozenset()


@unique
class Tier(Enum):
    """How deliberate an action is.

    The value is the multiplier applied to that tier's base delay, before the
    global pace knob. Ordered from reflex to deliberate.

    The deliberate tiers were cut roughly 3x on 2026-08-31. The ORDERING is the
    invariant here, not the magnitudes: a card must cost more than a page
    transition and less than sending an application, because that ranking is what
    makes a run's rhythm read as attention rather than as a schedule. The
    absolute values were tuned to a target instead — at most ~7 minutes per
    application, counting the two reads and the ten-odd delays in
    `submit_applications`, not the `_pause` alone. Before the cut a single
    application spent 7.4-18 minutes idle, which is the whole afternoon across
    sixty jobs.

    LOGIN and NAV came down with them rather than staying put. They had to: with
    CARD cut to 4.0 it would have collided with NAV's old 4.0, and equal values
    in an Enum are not two tiers — they are ALIASES. `Tier.CARD` would have BEEN
    `Tier.NAV`, silently, name and all. `@unique` now makes that a failure at
    import instead of a mystery in the logs.
    """

    # Motor reflexes. Untouched — these are limited by hands, not attention,
    # and stretching them is what makes automation look broken rather than human.
    REFLEX = 1.0

    # Logins and cookie banners. Still the briskest deliberate tier — a saved
    # password is typed without much thought — but not instant: landing on a
    # form and starting to type inside a second is itself a machine tell.
    LOGIN = 1.2

    # Moving between form fields, waiting out a page transition.
    NAV = 2.0

    # Scrolling and skimming a results list.
    SCROLL = 3.0

    # Opening a job card, closing it, moving to the next one.
    CARD = 4.0

    # Reading an offer description properly.
    READ = 5.0

    # Reviewing and sending an application — the most deliberate thing in a run.
    SUBMIT = 6.0


logger = logging.getLogger(__name__)

# Ceilings on how long a single wait may last, in seconds. Set per KIND of wait
# rather than per tier, because a tier is shared: the gap between cards and the
# hesitation before clicking into one are both CARD, and they want very different
# limits. Expressing the limits here keeps the tier multipliers free to stay
# ordered — a tier forced up to satisfy one call site would drag its other call
# sites with it, and two tiers landing on the same value are not two tiers at all
# but Enum aliases.
PAUSE_CEILING_S = 150.0   # any single deliberate pause: at most 2.5 minutes


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class HumanPacing:
    """Mixin. Expects the host worker to provide `self.page`, `self._plog(str)`
    and an async `self._beat(state)`.

    Call `_start_pacing()` when a run's browser comes up, so the budget clock
    starts at the same moment the session does.
    """

    # Set by the host worker's __init__ (see _init_pacing).
    _pace: float = DEFAULT_HUMAN_PACE
    _human_budget_s: float = DEFAULT_HUMAN_BUDGET_S
    _run_started_at: Optional[float] = None
    _actions_since_break: int = 0
    _pacing_run_token: Optional[str] = None

    # How long this worker spends reading an offer, in seconds: (floor, ceiling).
    # Per WORKER rather than shared, because the boards were measured to deserve
    # different attention — a WTTJ description is worth three minutes where a
    # HelloWork one is worth ninety seconds. Overridden in each worker class.
    _READ_BAND_S: tuple = (50.0, 150.0)
    # The description length, in characters, at which an offer counts as "long"
    # and its reading reaches the top of the band. Boards write to different
    # lengths, so this is theirs too.
    _READ_LONG_CHARS: int = 6000

    def _read_bounds(self, desc_len: int) -> tuple:
        """The min and max seconds to spend reading a description this long.

        Two properties have to hold at once, and picking either alone gets it
        wrong. Across offers the dwell must SPAN the band — a long description
        holds attention longer than a short one, and a fixed dwell on every page
        is the tell the whole mechanism exists to avoid. Within one offer there
        must still be a spread to draw from, so the pair is never collapsed to a
        single number: an earlier version capped floor and ceiling to the same
        constant, which would have made every long offer read for exactly the
        same duration.

        So the length picks a midpoint inside the band, and the returned pair
        straddles it by +/-25%, clipped back to the band's own edges.
        """
        floor_s, ceiling_s = self._READ_BAND_S
        span = min(max(desc_len / max(self._READ_LONG_CHARS, 1), 0.0), 1.0)
        midpoint = floor_s + (ceiling_s - floor_s) * span
        low = max(floor_s, midpoint * 0.75)
        high = min(ceiling_s, midpoint * 1.25)
        return low, max(low, high)

    def _init_pacing(self, board_env_prefix: str) -> None:
        """Resolve the pace knobs.

        A per-board override wins over the global one, so a board that tolerates
        less can be dialled back without touching the others.
        """
        self._pace = _float_env(
            f"{board_env_prefix}_HUMAN_PACE",
            _float_env("AGENT_HUMAN_PACE", DEFAULT_HUMAN_PACE),
        )
        self._human_budget_s = _float_env(
            f"{board_env_prefix}_HUMAN_BUDGET_S",
            _float_env("AGENT_HUMAN_BUDGET_S", DEFAULT_HUMAN_BUDGET_S),
        )
        self._run_started_at = None
        self._actions_since_break = 0

    def _start_pacing(self, run_token: Optional[str] = None) -> None:
        """Start (or restart) the budget clock for a run.

        Idempotency is scoped to the RUN, not the process. Workers are built once
        per process (create_agent behind a cached_property) and LocalDispatcher
        reuses that process across runs, so a clock that only ever set itself
        once meant the first run burned the budget and every run after it found
        _over_budget() already true — silently disabling all pacing for the life
        of the process. Prod (one Cloud Run Job process per run) hid this; dev
        showed it as "the pacing does nothing".

        Both browser tracks of one run share a token, so the submit track does not
        restart the scrape track's clock, and a human-review resume continues on
        the same budget. A missing token resets the clock: over-pacing a run is a
        bounded, visible failure, whereas silently not pacing at all is not.
        """
        if run_token is None or run_token != self._pacing_run_token:
            self._pacing_run_token = run_token
            self._run_started_at = time.monotonic()
            self._actions_since_break = 0

    def _over_budget(self) -> bool:
        """True once this run has burned through its human-pacing budget. Past
        that point every deliberate pause collapses to a no-op so the run still
        finishes inside the Cloud Run Job task timeout."""
        if self._run_started_at is None:
            return False
        return (time.monotonic() - self._run_started_at) > self._human_budget_s

    def _budget_remaining_s(self) -> float:
        if self._run_started_at is None:
            return self._human_budget_s
        return max(0.0, self._human_budget_s - (time.monotonic() - self._run_started_at))

    def _scaled(self, seconds: float, tier: Tier) -> float:
        return seconds * tier.value * self._pace

    # -----------------------------------------------------------------------
    # Pauses
    # -----------------------------------------------------------------------

    async def _pause(self, state, min_s: float, max_s: float, tier: Tier = Tier.NAV):
        """A deliberate idle stretch that keeps the heartbeat alive.

        The agent is declared dead once its heartbeat goes stale
        (AGENT_HEARTBEAT_STALE_SECONDS = 180), so anything longer than a few
        seconds has to beat while it waits — which is exactly what
        human_long_pause's on_tick does. Every pause in a worker must go through
        here rather than a bare asyncio.sleep.

        No single pause may exceed PAUSE_CEILING_S, whatever its tier and
        arguments say. Enforced here rather than trusted to call sites because a
        tier multiplier changes every site at once: the last retune turned one
        `_pause(8, 20, SUBMIT)` into a four-and-a-half minute wait without
        anybody editing that line.
        """
        if self._over_budget():
            return

        low = min(self._scaled(min_s, tier), PAUSE_CEILING_S)
        high = min(self._scaled(max_s, tier), PAUSE_CEILING_S)

        # Never let one pause eat the whole remaining budget.
        remaining = self._budget_remaining_s()
        low = min(low, remaining)
        high = min(high, remaining)
        if high <= 0:
            return

        await human_long_pause(
            low,
            high,
            on_tick=lambda: self._beat(state),
            tick_every=30.0,
            page=getattr(self, "page", None),
        )

    async def _micro(self, min_ms: int = 120, max_ms: int = 450, tier: Tier = Tier.REFLEX):
        """Sub-second hesitation. Short enough to skip the heartbeat machinery,
        and deliberately unscaled at REFLEX — this is the texture between
        actions, not a decision."""
        low = self._scaled(min_ms / 1000, tier)
        high = self._scaled(max_ms / 1000, tier)
        await asyncio.sleep(random.uniform(low, high))

    async def _maybe_break(self, state, label: str):
        """Occasionally step away. Real sessions aren't uniform: people stall,
        read something else, come back.

        Cadence-aware rather than a flat coin flip — the chance climbs with how
        long the worker has been going without a break, so breaks cluster after
        sustained activity the way attention actually lapses.
        """
        if self._over_budget():
            return

        self._actions_since_break += 1
        # ~4% after one action, rising to near-certain by ~20.
        probability = min(0.85, 0.04 + (self._actions_since_break ** 1.5) / 90)
        if random.random() > probability:
            return

        self._actions_since_break = 0
        self._plog(f"stepping away for a moment ({label})")
        await self._pause(state, 18.0, 55.0, tier=Tier.NAV)

    async def _distracted_stop(self, state, probability: float = 0.22):
        """The random stop: the cursor halts and nothing happens for a stretch.

        Distinct from a break. A break is stepping away; this is attention
        drifting mid-task — the thing that makes a session's action timeline
        ragged instead of metronomic.
        """
        if self._over_budget() or random.random() > probability:
            return

        page = getattr(self, "page", None)
        await self._pause(state, 1.2, 4.0, tier=Tier.NAV)
        if page is not None and random.random() < 0.5:
            try:
                await human_idle_drift(page, moves=random.randint(1, 2))
            except Exception:
                pass  # cosmetic only — never break a run over it

    # -----------------------------------------------------------------------
    # Pre-scrape texture
    # -----------------------------------------------------------------------

    async def _arrival_browse(self, state, label: str = "landing"):
        """Look around on arrival, before doing the thing we came to do.

        Everything deliberate used to live in the card loop, so a run went
        launch → nav → login → search → scrape in well under a minute and only
        then started behaving like a person. The opening seconds of a session
        are exactly what a board looks at, so the texture has to start here.

        Deliberately bounded (see _settle) — this comes out of the same budget
        as the card loop, and the card loop is where the time is better spent.
        """
        if self._over_budget():
            return

        page = getattr(self, "page", None)
        self._plog(f"looking around the page ({label})")

        for _ in range(random.randint(1, 3)):
            if page is not None:
                try:
                    await human_scroll(page, distance=random.randint(120, 420))
                except Exception:
                    pass  # cosmetic only — never break a run over it
            await self._pause(state, 0.6, 1.8, tier=Tier.SCROLL)

        if page is not None and random.random() < 0.6:
            try:
                await human_idle_drift(page, moves=random.randint(1, 3))
            except Exception:
                pass

        await self._distracted_stop(state)

    async def _settle(self, state, label: str):
        """A beat at a phase boundary — the pause between finishing one thing
        and starting the next. Short by design: NAV tier, not CARD."""
        if self._over_budget():
            return
        self._plog(f"pausing before the next step ({label})")
        await self._pause(state, 1.5, 4.0, tier=Tier.NAV)

    # -----------------------------------------------------------------------
    # Input primitives — the only door to the browser
    # -----------------------------------------------------------------------
    # Everything a worker does to a page goes through one of these. Not for
    # tidiness: `human_behavior` is a bag of free functions, so nothing stopped a
    # worker calling `locator.click()` directly, and roughly a third of them did
    # — including every login submit and every final application submit, the two
    # moments a board is most certainly watching. A raw `.click()` teleports the
    # cursor and fires mousedown/mouseup with no approach and no dwell; a raw
    # `.fill()` sets the value through `Input.insertText` and produces no key
    # events whatsoever.
    #
    # Routing through here also gives the input backend a single seam to swap
    # (see AGENT_INPUT_BACKEND), which is impossible while call sites hold
    # Playwright locators and call them directly.
    #
    # `tests/test_input_routing.py` enforces this by walking the workers' ASTs.

    # -----------------------------------------------------------------------
    # Progress
    # -----------------------------------------------------------------------

    def _progress(self, state, node: str, done: int = None, total: int = None) -> int:
        """This worker's percentage at `node`, for the frame about to be emitted.

        Both inputs come off the state the master already sends: `action_intent`
        picks the track, and the subscription picks the band table, because a
        BASIC run does scrape AND submit in one execution while a PREMIUM run
        stops at human_review and submits in a second one.

        Three boards emit these concurrently and the frontend takes Math.max, so
        a slow board can never drag the bar back — the fastest defines it and the
        rest are ignored, which is the intended behaviour and not a race.
        """
        from auto_apply_app.infrastructures.agent.stage_codes import progress_for

        track = "submit" if state.get("action_intent") == "SUBMIT" else "launch"
        subscription = state.get("subscription")
        account_type = getattr(subscription, "account_type", None)
        is_premium = getattr(account_type, "name", "") == "PREMIUM"
        return progress_for(node, track, is_premium, done=done, total=total)

    async def _click(self, locator, hesitation: bool = True,
                     retry_on_popup: bool = True) -> None:
        """Click an element the way a hand does: wheel it into view, approach it
        along a curve, hover, then press and release with a dwell.

        `retry_on_popup=False` for actions that must not happen twice. A click
        that opens a stray window is retried once by default, and the window is
        closed either way — but a submit button repeated is an application sent
        twice, which no cleanup undoes.
        """
        await human_click(locator, hesitation=hesitation, retry_on_popup=retry_on_popup)

    async def _type(self, locator, text: str, min_delay: int = None, max_delay: int = None) -> None:
        """Type into a field key by key. Never `fill()` — see the module note.

        The tempo scales with how much there is to write. Somebody entering a
        job title picks their way through it; somebody putting down a cover
        letter they have already composed types fluently and does not pause to
        choose each word. Left at the short-field tempo, a 1500-character letter
        would take upwards of five minutes and eat the pacing budget — so long
        text gets a faster, still-jittered rhythm. Callers can override.
        """
        long_form = len(text or "") > FAST_TYPING_LONG_FORM_CHARS

        # See AGENT_FAST_TYPING at the top of this module. Test-only.
        mode = fast_typing_mode()
        if mode == "all" or (mode == "long" and long_form):
            self._plog(
                f"FAST TYPING (AGENT_FAST_TYPING={mode}): filling {len(text or '')} "
                f"chars instantly — NO key events, not for a real run"
            )
            logger.warning(
                "AGENT_FAST_TYPING=%s is set: this field is being filled, not typed. "
                "The board sees no keystrokes at all.", mode,
            )
            try:
                await locator.fill(text or "")
                return
            except Exception:
                # Falling through rather than failing: a field `fill()` cannot
                # drive (a contenteditable, a masked input) should still get
                # filled the slow way rather than silently stay empty.
                logger.warning("fast fill failed -> typing this field normally",
                               exc_info=True)

        if min_delay is None or max_delay is None:
            min_delay = min_delay if min_delay is not None else (35 if long_form else 90)
            max_delay = max_delay if max_delay is not None else (110 if long_form else 300)
        await human_type(locator, text, min_delay=min_delay, max_delay=max_delay)

    async def _press(self, key: str, locator=None) -> None:
        """Press a single key (Enter, Tab, Escape) with a real dwell.

        Playwright's `delay` sits BETWEEN keydown and keyup, so passing one here
        is how the key gets held rather than struck instantaneously — a
        zero-length press is not a duration any finger produces.
        """
        dwell_s = random.uniform(0.045, 0.130)
        page = locator.page if locator is not None else getattr(self, "page", None)
        if page is None:
            return
        await get_backend(page).key_press(key, dwell_s, locator=locator)

    async def _scroll_to(self, locator) -> bool:
        """Wheel an element into view instead of jumping the scroll position."""
        return await human_scroll_to(locator, pace=min(self._pace, 3.0))

    async def _select(self, locator, value) -> None:
        """Choose an option in a native `<select>`.

        Honest limitation: a native dropdown's option list is drawn by the OS,
        not the page, so there is nothing in the DOM to move a cursor onto. The
        best available imitation is to approach and focus the control like a
        person, then let Playwright commit the value — the page still sees the
        focus, the click, and the change event in the right order.
        """
        await self._scroll_to(locator)
        await self._click(locator, hesitation=False)
        await self._micro(180, 620, tier=Tier.NAV)
        await locator.select_option(value)
        await self._micro()

    async def _check(self, locator) -> None:
        """Tick a checkbox or radio by clicking it, not by setting it.

        `.check()` resolves to a direct state change when it decides the element
        is already actionable; clicking it is what a person does, and it leaves
        the same event trail as any other click.
        """
        await self._click(locator, hesitation=False)
        try:
            if not await locator.is_checked():
                await locator.check(force=True)
        except Exception:
            # Not a checkable control (a styled label, say) — the click stands.
            pass

    async def _uncheck(self, locator) -> None:
        """Clear a checkbox by clicking it, for the same reason as _check."""
        if not await locator.is_checked():
            return
        await self._click(locator, hesitation=False)
        try:
            if await locator.is_checked():
                await locator.uncheck(force=True)
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Browser session: display, then input backend
    # -----------------------------------------------------------------------
    # These live here rather than in each worker because all three launch two
    # browser tracks apiece — six near-identical launch sites — and the display
    # has to be created BEFORE the launch while the backend can only be mounted
    # AFTER the page exists. Splitting that across six copies is how the tracks
    # drifted apart the last time (one set has_touch, the other did not).

    _x_display = None

    async def _browser_launch_kwargs(self, base: dict, fingerprint=None) -> dict:
        """Merge the display into a worker's own launch options.

        Under the CDP backend this returns `base` untouched, so the default path
        is exactly what it was. Under x11 the browser goes headful — a headless
        shell has no window for a pointer to be over — and its window is pinned
        somewhere deterministic so the viewport can be mapped onto the screen.

        WHERE it goes headful depends on whether a screen already exists. On a
        dev box it inherits `$DISPLAY`, so the window is one you can actually
        watch; in the container there is no `$DISPLAY`, so a private Xvfb is
        started. Getting this wrong is not a small thing in either direction: an
        Xvfb on a dev box makes the run invisible, and inheriting a display that
        is not there makes the browser fail to launch at all.

        Fails soft: if no display can be had, the run continues headless on CDP.
        A worker that cannot reach an X server should still apply for jobs.
        """
        kwargs = dict(base)
        self._x_display = None

        if resolve_backend_name() != BACKEND_X11:
            return kwargs

        try:
            persona_w, persona_h = screen_size_for(fingerprint)
            self._x_display = open_display(persona_w, persona_h)
        except Exception as exc:
            self._plog(f"X display unavailable ({exc}) -> staying on the CDP backend")
            logger.warning("no usable X display, falling back to CDP input", exc_info=True)
            self._x_display = None
            return kwargs

        # Sized against the display we ACTUALLY got. `open_display` only lends an
        # existing screen when the persona fits on it, so this is either the
        # persona's own framebuffer or a screen at least as large.
        screen_w, screen_h = self._x_display.width, self._x_display.height

        x, y, win_w, win_h = window_geometry_for(fingerprint, screen_w, screen_h)
        kwargs["headless"] = False
        kwargs["env"] = {**os.environ, "DISPLAY": self._x_display.display}
        kwargs["args"] = list(kwargs.get("args") or []) + [
            f"--window-position={x},{y}",
            f"--window-size={win_w},{win_h}",
        ]
        where = "existing display" if getattr(self._x_display, "borrowed", False) else "Xvfb"
        self._plog(
            f"headful on {self._x_display.display} ({screen_w}x{screen_h}, {where}) "
            f"for OS-level input"
        )
        return kwargs

    def _fit_to_display(self, fingerprint):
        """Shrink the persona's viewport if this display cannot show it whole.

        A no-op everywhere that matters: production has no display to borrow, so
        its Xvfb is built to the persona's own size and nothing is clamped. It
        only bites on a dev box, where the screen is whatever the developer has
        and the alternative is not seeing the run at all.
        """
        display = self._x_display
        if display is None or not getattr(display, "borrowed", False):
            return fingerprint
        return fit_fingerprint_to_display(
            fingerprint, display.width, display.height, self._plog
        )

    async def _suppress_popups(self, context) -> None:
        """Stop a click ever opening a second window.

        Called unconditionally, unlike the fingerprint's init script which is
        gated on a fingerprint existing — a run without one is already degraded
        and should not also lose control of its display.

        Cheap and total: `window.open` and `target="_blank"` both become a
        navigation in the current page, so no OS window is created and there is
        no focus to lose. Closing a stray afterwards cannot achieve the same
        thing, because on a window-managed display focus cannot be taken back.
        """
        try:
            await context.add_init_script(SUPPRESS_POPUPS_JS)
        except Exception:
            logger.warning("could not install the popup suppressor", exc_info=True)

    async def _conserve_bandwidth(self, context) -> None:
        """Stop paying residential rates for pixels the agent never looks at.

        MUST be registered LAST, after any other context.route on this context.
        Playwright tries the most recently added handler first, and HelloWork's
        _block_tracking ends its chain with route.continue_() -- register this
        one before it and every request would be served by that handler and this
        one would never see a single byte.

        Images are FULFILLED with a transparent GIF rather than aborted. Both
        save the same bytes, but a failed image is a visible defect and a
        measurable one: onload never fires, <img> collapses to nothing, and the
        lazy-loading scroll these boards use can stall on it. A 43-byte local
        response looks like a successful fetch to every one of those.

        Video and fonts are aborted outright -- there is no cheap stand-in for
        them, and neither carries anything the agent reads.

        No-op unless AGENT_BLOCK_HEAVY_ASSETS is set, so an unconfigured run
        behaves exactly as before.
        """
        blocked = blocked_asset_types()
        if not blocked:
            return

        saved = {"image": 0, "media": 0, "font": 0}

        async def _handler(route):
            request = route.request
            url = request.url
            try:
                if request.resource_type in blocked and not any(
                    host in url for host in ASSET_ALLOW_HOSTS
                ):
                    saved[request.resource_type] += 1
                    if request.resource_type == "image":
                        await route.fulfill(
                            status=200,
                            content_type="image/gif",
                            body=_TRANSPARENT_GIF,
                        )
                    else:
                        await route.abort()
                    return
                # fallback(), not continue_(): continue_ ends the chain and would
                # silently disable every handler registered before this one.
                await route.fallback()
            except Exception:
                # A route that raises leaves the request hanging until it times
                # out, which is far worse than a wasted megabyte.
                logger.debug("asset route failed for %s", url, exc_info=True)
                try:
                    await route.fallback()
                except Exception:
                    pass

        try:
            await context.route("**/*", _handler)
            self._blocked_assets = saved
            self._plog(f"blocking heavy assets: {', '.join(sorted(blocked))}")
        except Exception:
            logger.warning("could not install the asset blocker", exc_info=True)

    def _log_bandwidth_saved(self) -> None:
        """One line at the end of a run, so the setting can be judged on numbers
        rather than on faith. Counts requests, not bytes -- a blocked request
        never reports a size."""
        saved = getattr(self, "_blocked_assets", None)
        if saved and any(saved.values()):
            self._plog(
                "assets blocked: "
                + ", ".join(f"{n} {k}" for k, n in saved.items() if n)
            )
    async def _mount_input(self) -> str:
        """Attach the input backend to `self.page`. Call once the page exists.

        Returns the backend actually mounted, which is not always the one asked
        for — a mapping that cannot be verified falls back to CDP rather than
        clicking confidently in the wrong place for a whole run.
        """
        page = getattr(self, "page", None)
        if page is None or self._x_display is None:
            return "cdp"

        from auto_apply_app.infrastructures.agent.input.x11_backend import (
            X11InputBackend,
            X11Unavailable,
        )

        try:
            backend = await X11InputBackend.create(page, display_name=self._x_display.display)
        except X11Unavailable as exc:
            self._plog(f"OS-level input unavailable ({exc}) -> using CDP")
            logger.warning("x11 backend unavailable: %s", exc)
            return "cdp"

        attach_backend(page, backend)
        # One listener per context, here because this already runs exactly once
        # per page in every worker. See watch_for_stray_pages for why it is not
        # per click.
        watch_for_stray_pages(page)
        if not await backend.verify():
            self._plog("OS-level input failed its coordinate check -> falling back to CDP")
            detach_backend(page)
            await backend.close()
            return "cdp"

        self._plog("OS-level input active (XTEST)")
        return BACKEND_X11

    async def _close_display(self) -> None:
        """Tear down in the reverse order, and never raise: this runs in cleanup
        paths that are already handling a failure."""
        page = getattr(self, "page", None)
        if page is not None:
            backend = detach_backend(page)
            if backend is not None:
                try:
                    await backend.close()
                except Exception:
                    logger.debug("input backend close failed", exc_info=True)
        if self._x_display is not None:
            try:
                self._x_display.stop()
            except Exception:
                logger.debug("Xvfb stop failed", exc_info=True)
            self._x_display = None

    async def _upload(self, locator, files) -> None:
        """Attach a file.

        `set_input_files` is unavoidable: the file picker is an OS dialog that no
        browser automation reaches. What IS controllable is the time it takes —
        a CV appearing on the form the instant the button is clicked is a tell,
        so the pause that a person spends in the picker happens here.
        """
        await asyncio.sleep(random.uniform(1.8, 5.5) * min(self._pace, 2.0))
        await locator.set_input_files(files)
        await self._micro(300, 900, tier=Tier.NAV)
