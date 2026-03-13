from typing import TypedDict, List

# Domain Entities
from auto_apply_app.domain.entities.user import User
#from auto_apply_app.domain.entities.user_subscription import UserSubscription
from auto_apply_app.domain.entities.job_search import JobSearch
from auto_apply_app.domain.entities.job_offer import JobOffer

class JobApplicationState(TypedDict):
    # --- DOMAIN CONTEXT (Inputs) ---
    user: User
    #subs: UserSubscription
    job_search: JobSearch

    # --- PROCESS BUFFER ---
    # Scraped jobs waiting to be validated by the Use Case
    found_raw_offers: List[JobOffer] 
    processed_offers: List[JobOffer]

    # --- TECHNICAL STATE ---
    current_url: str
    is_logged_in: bool
    status: str