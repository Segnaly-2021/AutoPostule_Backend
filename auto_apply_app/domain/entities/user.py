# ==============================================================================
# FILE: auto_apply_app/domain/entities/user.py
#
# WHAT IT DOES:
#   The user's profile: identity, contact details, resume location, work and
#   education. Workers use it to fill application forms. Login data is in AuthUser.
#
# USED BY:
#   - auto_apply_app/application/use_cases/user_use_cases.py: account flows
#   - auto_apply_app/application/use_cases/run_context_use_cases.py: loads for a run
#   - auto_apply_app/application/dtos/user_dtos.py: maps to responses
#   - auto_apply_app/application/repositories/user_repo.py: repository interface
#   - auto_apply_app/application/service_ports/agent_port.py: agent method signatures
#   - auto_apply_app/infrastructures/agent/master/master_agent.py: run orchestration
#   - auto_apply_app/infrastructures/agent/state.py: agent state type
#   - auto_apply_app/infrastructures/persistence/database/repositories/user_repo_db.py: persistence
#   - ... and 4 more
#
# EXTERNAL DEPENDENCIES:
#   - None (standard library only)
# ==============================================================================
from dataclasses import dataclass
from typing import Optional

from auto_apply_app.domain.entities.entity import Entity


@dataclass
class User(Entity):
  firstname: str
  lastname: str
  email: str 
  address: Optional[str] = None
  resume_path: Optional[str] = None
  resume_file_name: Optional[str] = None
  current_position: Optional[str] = None  
  linkedin_url: Optional[str] = None
  current_company: Optional[str] = None
  phone_number: Optional[str] = None
  school_type: Optional[str] = None
  graduation_year: Optional[str] = None
  major: Optional[str] = None
  study_level: Optional[str] = None
 