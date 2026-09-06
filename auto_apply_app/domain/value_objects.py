from enum import Enum

class ClientType(Enum):
  FREE = "FREE"
  BASIC = "BASIC"
  PREMIUM = "PREMIUM"


class ContractType(Enum):   
   INTERNSHIP = "Stage"
   APPRENTICESHIP = "Alternance"
   FREELANCE = "Freelance"
   TEMPORARY = "CDD"
   FULL_TIME = "CDI"
   INDEPENDENT = "Independant"



class ApplicationStatus(Enum):
    FOUND = "FOUND"                # Scraped, raw data
    IN_PROGRESS = "IN_PROGRESS"    # Being processed (e.g., writing CL)
    GENERATED = "GENERATED_CL"     # Cover letter written, waiting for Review (Premium) or Auto-Send (Basic)
    APPROVED = "APPROVED_CL"       # User (or Logic) confirmed it's ready to send
    SUBMITTED = "SUBMITTED"        # Successfully sent
    FAILED = "FAILED"              # Technical error
    REJECTED = "REJECTED"
    

class SearchStatus(Enum):
   PENDING = "PENDING"
   SEARCHING = "SEARCHING"
   PAUSED = "PAUSED"
   COMPLETED = "COMPLETED"
   CANCELLED = "CANCELLED"
   FAILED = "FAILED" 

class JobBoard(Enum):
    WTTJ = "wttj"
    HELLOWORK = "hellowork"
    APEC = "apec"
    JOBTEASER = "jobteaser"
    INDEED = "indeed"


class CreditTxKind(Enum):
    CONSUME = "CONSUME"      # credits spent by an agent run (delta < 0)
    REPLENISH = "REPLENISH"  # credits granted on checkout / invoice.paid (delta > 0)


class MessageKind(Enum):
    """One value per lifecycle message.

    These strings are persisted in message_log and are half of its uniqueness
    constraint, so renaming a member would re-send that message to everyone who
    already received it. Add new kinds; do not rename old ones.
    """
    NEW_CUSTOMER_CHECKIN = "NEW_CUSTOMER_CHECKIN"    # 3 days after a plan is bought
    FREE_ACCOUNT_REMINDER = "FREE_ACCOUNT_REMINDER"  # 3 days after a free signup
