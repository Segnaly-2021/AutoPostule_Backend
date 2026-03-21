import operator
from typing import TypedDict, List, Optional, Annotated, Dict

# Domain Entities
from auto_apply_app.domain.entities.user import User
from auto_apply_app.domain.entities.user_subscription import UserSubscription
from auto_apply_app.domain.entities.user_preferences import UserPreferences
from auto_apply_app.domain.entities.board_credentials import BoardCredential
from auto_apply_app.domain.entities.job_search import JobSearch
from auto_apply_app.domain.entities.job_offer import JobOffer

# --- REDUCER HELPERS ---

def keep_original(old, new):
    """Used for static data. Always keeps the initial value from the Master."""
    return old

def take_latest(old, new):
    """Used for status/logs. Keeps the most recent update, preventing crashes."""
    return new

class JobApplicationState(TypedDict):
    # --- DOMAIN CONTEXT (Annotated to prevent parallel merge crashes) ---
    user: Annotated[User, keep_original]
    subscription: Annotated[UserSubscription, keep_original] 
    job_search: Annotated[JobSearch, keep_original]
    preferences: Annotated[UserPreferences, keep_original] 
    credentials: Annotated[Optional[Dict[str, BoardCredential]], keep_original]
    
    # --- CONTROL FLAGS ---
    # We use take_latest so workers can update these without crashing the Master
    action_intent: Annotated[str, take_latest] 
    status: Annotated[str, take_latest] 
    current_url: Annotated[str, take_latest]
    is_logged_in: Annotated[bool, take_latest]
    error: Annotated[Optional[str], take_latest]

    # --- WORKLOAD ---
    max_jobs: Annotated[int, keep_original]
    worker_job_limit: Annotated[int, keep_original]

    # --- PROCESS BUFFER (Annotated to MERGE lists) ---
    # operator.add is the most important reducer: it combines the 3 worker 
    # lists into one big list instead of overwriting.
    found_raw_offers: Annotated[List[JobOffer], operator.add]
    processed_offers: Annotated[List[JobOffer], operator.add]
    submitted_offers: Annotated[List[JobOffer], operator.add]