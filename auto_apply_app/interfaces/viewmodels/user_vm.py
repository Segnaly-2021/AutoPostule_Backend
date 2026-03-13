from dataclasses import dataclass
from typing import Optional 


@dataclass(frozen=True)
class UserViewModel:
  id: str
  full_name: str
  firstname: str
  lastname: str
  email: str
  initials: str
  phone_number: str = None
  resume_path: str = None
  current_position: str = None
  current_company: str = None
  school_type: str = None
  graduation_year: str = None
  major: str  = None
  study_level: str = None
  

@dataclass(frozen=True)
class LoginViewModel:
    token: str
    token_type: str = "Bearer"

@dataclass(frozen=True)
class SubViewModel:
    account: Optional[str]  # "FREE", "BASIC", "PREMIUM"
    next_billing_date: Optional[str]
    start_date: Optional[str]
    exp_date: Optional[str]
    is_active: Optional[bool]
    cancel_at: Optional[str]
    message: Optional[str]
    
    # ✅ ADD: Helper property for frontend
    @property
    def is_premium(self) -> bool:
        return self.account == "PREMIUM"
    
    @property
    def can_review_jobs(self) -> bool:
        return self.account == "PREMIUM"