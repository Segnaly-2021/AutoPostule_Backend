import operator
from typing import TypedDict, List, Optional, Annotated, Dict

from auto_apply_app.domain.entities.user import User
from auto_apply_app.domain.entities.user_subscription import UserSubscription
from auto_apply_app.domain.entities.user_preferences import UserPreferences
from auto_apply_app.domain.entities.board_credentials import BoardCredential
from auto_apply_app.domain.entities.user_fingerprint import UserFingerprint
from auto_apply_app.domain.entities.job_search import JobSearch
from auto_apply_app.domain.entities.job_offer import JobOffer


def keep_first(old, new):
    """
    For fields that should never be overwritten by workers.
    Workers echo these back unchanged — we always keep the original.
    If old is None (first write), accept the new value.
    """
    if old is None:
        return new
    return old  # always keep the master's original value


def take_latest(old, new):
    """
    For fields that represent current status/progress.
    Always take the most recent non-None value.
    """
    if new is None:
        return old
    return new


class JobApplicationState(TypedDict):
    # --- IMMUTABLE MASTER DATA ---
    # Workers echo these back unchanged. keep_first ensures
    # receiving 3 identical copies from 3 parallel workers doesn't crash.
    user: Annotated[User, keep_first]
    subscription: Annotated[UserSubscription, keep_first]
    job_search: Annotated[JobSearch, keep_first]
    preferences: Annotated[UserPreferences, keep_first]
    credentials: Annotated[Optional[Dict[str, BoardCredential]], keep_first]
    # Per-board browser identity. A map, not a single value: each board is pinned
    # to its own device persona, so the three workers in one run present three
    # different machines. Keyed by the canonical board key ("apec", "hellowork",
    # "wttj") — see Worker._board_key.
    user_fingerprints: Annotated[Optional[Dict[str, UserFingerprint]], keep_first]
    # Per-board proxy, keyed the same way. The sticky exit IP is derived from the
    # persona id, so a board's device keeps its IP across runs instead of the
    # fingerprint and the IP rotating on unrelated schedules.
    proxy_configs: Annotated[Optional[Dict[str, dict]], keep_first]
    # Identifies ONE execution. Seeds the per-session fingerprint variant, so a
    # human-review resume of the same run reproduces the same browser instead of
    # appearing to swap machines mid-session.
    run_token: Annotated[Optional[str], keep_first]
    max_jobs: Annotated[int, take_latest]
    worker_job_limit: Annotated[int, take_latest]

    # --- MUTABLE STATUS FIELDS ---
    # Last worker to update wins — fine for status/url tracking
    action_intent: Annotated[str, take_latest]
    status: Annotated[str, take_latest]
    current_url: Annotated[str, take_latest]
    is_logged_in: Annotated[bool, take_latest]
    error: Annotated[Optional[str], take_latest]
    error_code: Annotated[Optional[str], take_latest] # 🚨 NEW: Added for translation tracking

    # --- PARALLEL MERGE LISTS ---
    # operator.add safely concatenates results from all workers
    found_raw_offers: Annotated[List[JobOffer], operator.add]
    processed_offers: Annotated[List[JobOffer], operator.add]
    submitted_offers: Annotated[List[JobOffer], operator.add]