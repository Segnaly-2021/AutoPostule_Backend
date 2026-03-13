import operator
from typing import TypedDict, List, Optional, Annotated, Dict

# Domain Entities
from auto_apply_app.domain.entities.user import User
from auto_apply_app.domain.entities.user_subscription import UserSubscription
from auto_apply_app.domain.entities.user_preferences import UserPreferences
from auto_apply_app.domain.entities.board_credentials import BoardCredential
from auto_apply_app.domain.entities.job_search import JobSearch
from auto_apply_app.domain.entities.job_offer import JobOffer

class JobApplicationState(TypedDict):
    # --- DOMAIN CONTEXT (Inputs) ---
    user: User
    subscription: UserSubscription 
    job_search: JobSearch
    preferences: UserPreferences 
    
    # 🚨 [NEW] Action Intent (The remote control from the Master)
    # Expected values: "SCRAPE" or "SUBMIT"
    action_intent: str 

    # --- PROCESS BUFFER ---
    # 🚨 [UPDATED] LangGraph Reducers for Parallel Execution
    # operator.add ensures that if APEC and HelloWork run simultaneously,
    # their outputs are merged into one giant list instead of overwriting each other.
    found_raw_offers: Annotated[List[JobOffer], operator.add]
    
    processed_offers: Annotated[List[JobOffer], operator.add]

    # 🚨 [NEW] The "Outbox" for jobs that actually got submitted
    submitted_offers: Annotated[List[JobOffer], operator.add]

    # [NEW] Workload Management
    max_jobs: int # e.g., 20 for Basic, 60 for Premium
    worker_job_limit: int # The split number (e.g., max_jobs // active_boards)

    # --- TECHNICAL STATE ---
    current_url: str
    is_logged_in: bool
    status: str 
    credentials: Optional[Dict[str, BoardCredential]] = None
    error: Optional[str]