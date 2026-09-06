# auto_apply_app/infrastructures/agent/stage_codes.py
from enum import Enum


class StageCode(str, Enum):
    # --- Worker stages ---
    INITIALIZING_BROWSER = "INITIALIZING_BROWSER"
    NAVIGATING = "NAVIGATING"
    AUTHENTICATING = "AUTHENTICATING"
    SEARCHING = "SEARCHING"
    EXTRACTING_DATA = "EXTRACTING_DATA"
    SUBMITTING = "SUBMITTING"
    CLEANING_UP = "CLEANING_UP"

    # --- Master stages ---
    LAUNCHING_WORKERS = "LAUNCHING_WORKERS"
    GENERATING_LETTERS = "GENERATING_LETTERS"
    DISPATCHING = "DISPATCHING"
    WAITING_REVIEW = "WAITING_REVIEW"
    LAUNCHING_SUBMISSION = "LAUNCHING_SUBMISSION"
    SAVING_RESULTS = "SAVING_RESULTS"

    # --- Terminal states ---
    COMPLETE = "COMPLETE"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    NO_JOBS = "NO_JOBS"


# ---------------------------------------------------------------------------
# Progress bands
#
# The bar is owned by the BACKEND: every frame carries `progress_percent` and
# the frontend just takes `Math.max` of what arrives. That already gives the
# multi-worker rule for free — three boards run in parallel, the fastest defines
# the bar, the slower ones can never drag it back.
#
# Bands, not points. `scrape` and `submit` are single graph nodes but most of
# the wall clock, so giving each node one fixed percentage would jump the bar and
# then freeze it for minutes — the exact complaint this replaces. Those two
# interpolate on a COUNT instead.
#
# What counts is deliberate:
#   - scrape advances per KEEPER (an easy-apply offer that reached the list),
#     never per card opened and never per page. AutoPostule only supports
#     applications completed on the board itself, so a card that turns out to be
#     an external form is work the user gets nothing from. The loop is already
#     keeper-driven (`while len(found) < worker_job_limit`), so the denominator
#     is known before it starts.
#   - submit advances per SUBMISSION, denominator from the master.
#
# The plan changes the path. BASIC runs scrape and submit in ONE execution;
# PREMIUM stops at human_review and submits in a SECOND one (master_agent.py:917).
# So the launch bands differ by plan, and the premium submit run gets the whole
# 0-100 to itself.
# ---------------------------------------------------------------------------

# (start, end) per node. Nodes are named identically in all three workers, which
# is what lets one table serve every board.
BASIC_LAUNCH_BANDS = {
    "start": (0, 5),
    "nav": (5, 10),
    "login": (10, 15),
    "search": (15, 20),
    "scrape": (20, 55),      # interpolates on keepers
    "letters": (55, 65),     # master: GENERATING_LETTERS
    "submit": (65, 95),      # interpolates on submissions
    "cleanup": (95, 100),
}

PREMIUM_LAUNCH_BANDS = {
    "start": (0, 5),
    "nav": (5, 12),
    "login": (12, 20),
    "search": (20, 30),
    "scrape": (30, 70),      # interpolates on keepers
    "letters": (70, 80),
    "review": (80, 80),      # rests here until the user acts
}

# Premium session 2. A run of its own, so it owns the full range.
SUBMIT_BANDS = {
    "start_with_session": (0, 10),
    "submit": (10, 95),      # interpolates on submissions
    "cleanup": (95, 100),
}


def bands_for(track: str, is_premium: bool) -> dict:
    """The band table for this run. `track` is "launch" or "submit"."""
    if track == "submit":
        return SUBMIT_BANDS
    return PREMIUM_LAUNCH_BANDS if is_premium else BASIC_LAUNCH_BANDS


def progress_for(node: str, track: str, is_premium: bool,
                 done: int = None, total: int = None) -> int:
    """Percentage for a node, interpolated on a count where one is given.

    `done`/`total` only mean something for the two counting nodes; everywhere
    else the node's START is the answer, because a point-node's percentage is
    what it reports on ENTRY. A missing or zero total falls back to the start of
    the band rather than dividing by zero — a worker that found nothing should
    hold, not jump to the end.
    """
    band = bands_for(track, is_premium).get(node)
    if band is None:
        return 0
    start, end = band
    if not done or not total:
        return int(start)
    ratio = min(1.0, max(0.0, done / total))
    return int(start + (end - start) * ratio)
