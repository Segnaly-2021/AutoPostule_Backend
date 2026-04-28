from enum import Enum

class ClientType(Enum):
  FREE = "FREE"
  BASIC = "BASIC"
  PREMIUM = "PREMIUM"


class ContractType(Enum):   
   INTERNSHIP = "Stage"
   APPRENTICESHIP = "Alternance"
   FREELANCE = "freelance"
   TEMPORARY = "CDD"
   FULL_TIME = "CDI"



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


 

  
