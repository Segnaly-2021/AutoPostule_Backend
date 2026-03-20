from dataclasses import dataclass
from typing import Optional

from auto_apply_app.domain.entities.entity import Entity


@dataclass
class User(Entity):
  firstname: str
  lastname: str
  email: str 
  resume_path: Optional[str] = None
  resume_file_name: Optional[str] = None
  current_position: Optional[str] = None  
  current_company: Optional[str] = None
  phone_number: Optional[str] = None
  school_type: Optional[str] = None
  graduation_year: Optional[str] = None
  major: Optional[str] = None
  study_level: Optional[str] = None
 