import operator
from typing import TypedDict, List, Optional, Annotated, Dict

from auto_apply_app.domain.entities.user import User
from auto_apply_app.domain.entities.user_subscription import UserSubscription
from auto_apply_app.domain.entities.user_preferences import UserPreferences
from auto_apply_app.domain.entities.board_credentials import BoardCredential
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
    max_jobs: Annotated[int, keep_first]
    worker_job_limit: Annotated[int, keep_first]

    # --- MUTABLE STATUS FIELDS ---
    # Last worker to update wins — fine for status/url tracking
    action_intent: Annotated[str, take_latest]
    status: Annotated[str, take_latest]
    current_url: Annotated[str, take_latest]
    is_logged_in: Annotated[bool, take_latest]
    error: Annotated[Optional[str], take_latest]

    # --- PARALLEL MERGE LISTS ---
    # operator.add safely concatenates results from all workers
    found_raw_offers: Annotated[List[JobOffer], operator.add]
    processed_offers: Annotated[List[JobOffer], operator.add]
    submitted_offers: Annotated[List[JobOffer], operator.add]