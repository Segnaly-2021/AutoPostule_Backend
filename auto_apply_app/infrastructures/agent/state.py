# ==============================================================================
# FILE: auto_apply_app/infrastructures/agent/state.py
#
# WHAT IT DOES:
#   Defines JobApplicationState, the shared memory of the LangGraph run. The master
#   and all board workers read it, and each node returns a partial update to it.
#   Each field declares a "reducer" that decides how updates merge, which is what
#   makes it safe for parallel workers to write the same field.
#
# USED BY:
#   - auto_apply_app/infrastructures/agent/master/master_agent.py: master graph state
#   - auto_apply_app/infrastructures/agent/workers/apec/apec_worker.py: worker subgraph state
#   - auto_apply_app/infrastructures/agent/workers/hellowork/hw_worker.py: worker subgraph state
#   - auto_apply_app/infrastructures/agent/workers/wttj/wttj_worker.py: worker subgraph state
#   - auto_apply_app/infrastructures/agent/workers/teaser/teaser_worker.py: worker subgraph state
#   - auto_apply_app/infrastructures/agent/workers/hellowork/hw_worker_v1.py: legacy worker
#   - auto_apply_app/infrastructures/agent/basic_agent/master/master_agent.py: dead legacy master
#   - auto_apply_app/infrastructures/agent/basic_agent/workers/apec/apec_worker.py: dead legacy worker
#   - ... and 2 more
#
# EXTERNAL DEPENDENCIES:
#   - langgraph: not imported here, but it reads the Annotated reducers below
# ==============================================================================
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


# HOW REDUCERS WORK: `Annotated[Type, fn]` tells LangGraph to compute
# `new_value = fn(current_value, update)` for that field. Without a reducer, two
# parallel branches writing the same field in one step raise InvalidUpdateError.
#   keep_first   -> the first value stays forever
#   take_latest  -> the last non-None write wins (order between parallel workers is not fixed)
#   operator.add -> lists are concatenated, so each worker's results accumulate
#
# Reducers also apply to `aupdate_state`, which matters on a human-review resume:
# see the note in MasterAgent.resume_job_search.
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
    # Run-wide number of submissions to attempt, fixed by the master before
    # the fan-out. The denominator of the submit progress band.
    submit_total: Annotated[Optional[int], take_latest]

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