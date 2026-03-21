import operator
from typing import TypedDict, List, Optional, Annotated, Dict

# Domain Entities
from auto_apply_app.domain.entities.user import User
from auto_apply_app.domain.entities.user_subscription import UserSubscription
from auto_apply_app.domain.entities.user_preferences import UserPreferences
from auto_apply_app.domain.entities.board_credentials import BoardCredential
from auto_apply_app.domain.entities.job_search import JobSearch
from auto_apply_app.domain.entities.job_offer import JobOffer



def keep_master(old, new):
    """Reducer: Always keeps the original data from the Master Agent."""
    return old

def take_latest(old, new):
    """Reducer: Keeps the most recent status update."""
    return new

class JobApplicationState(TypedDict):
    # --- Protect these from parallel merge crashes ---
    user: Annotated[User, keep_master]
    subscription: Annotated[UserSubscription, keep_master] 
    job_search: Annotated[JobSearch, keep_master]
    preferences: Annotated[UserPreferences, keep_master] 
    credentials: Annotated[Optional[Dict[str, BoardCredential]], keep_master]

    # --- Allow these to be updated by workers ---
    action_intent: Annotated[str, take_latest] 
    status: Annotated[str, take_latest] 
    current_url: Annotated[str, take_latest]
    is_logged_in: Annotated[bool, take_latest]
    error: Annotated[Optional[str], take_latest]

    # --- Workload Management ---
    max_jobs: Annotated[int, keep_master]
    worker_job_limit: Annotated[int, keep_master]

    # --- The Lists (Annotated to MERGE them) ---
    found_raw_offers: Annotated[List[JobOffer], operator.add]
    processed_offers: Annotated[List[JobOffer], operator.add]
    submitted_offers: Annotated[List[JobOffer], operator.add]