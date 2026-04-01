from dataclasses import dataclass
from typing import Optional, Dict, Self
from pydantic import EmailStr
from uuid import UUID

from auto_apply_app.domain.entities.user import User

@dataclass(frozen=True)
class CreateUserRequest:
    """This class is a data carrier from the Application Layer -> Domain Layer"""
    user_firstname: str
    user_lastname: str
    user_email: EmailStr
    user_resume_dir: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.user_firstname.strip():
            raise ValueError("Firstname is required")
        
        if not self.user_lastname.strip():
            raise ValueError("Lastname is required")
        
        if not self.user_email:
            raise ValueError("Email not valid")

    def to_execution_params(self) -> Dict:
        params = {
            "firstname": self.user_firstname, # Fixed typo 'fistname' to 'firstname'
            "lastname": self.user_lastname,
            "email": self.user_email
        }

        if self.user_resume_dir: # Fixed reference from self.resume_dir
            params["resume_dir"] = self.user_resume_dir

        return params


@dataclass(frozen=True)
class GetUserRequest:
    """Data carrier: Application Layer -> Domain Layer"""
    user_id: str

    def __post_init__(self) -> None:
        if not self.user_id.strip():
            raise ValueError("User ID is required")

    def to_execution_params(self) -> Dict:
        return {"user_id": UUID(self.user_id)}


@dataclass(frozen=True)
class UpdateUserRequest:
    """Request data for an update from App Layer -> to Domain Layer"""
    user_id: str
    user_firstname: str = None
    user_lastname: str = None
    user_email: EmailStr = None
    user_address: Optional[str] = None  # <-- NEW FIELD
    user_current_position: str = None
    user_current_company: str = None
    user_resume_dir: Optional[str] = None 
    user_resume_file_name: Optional[str] = None
    user_phone_number: Optional[str] = None
    user_school_type: Optional[str] = None
    user_graduation_year: Optional[str] = None
    user_major: Optional[str] = None
    user_study_level: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.user_id.strip():
            raise ValueError("User ID is required")
        
        if self.user_firstname is not None and len(self.user_firstname) > 50:
            raise ValueError("Firstname cannot exceed 50 characters")
        
        if self.user_lastname is not None and len(self.user_lastname) > 50:
            raise ValueError("Lastname cannot exceed 50 characters")   

    def to_execution_params(self) -> dict:
        """Convert request data to use case parameters."""
        params = {"user_id": UUID(self.user_id)}

        if self.user_firstname is not None:
            params["firstname"] = self.user_firstname.strip()
        if self.user_lastname is not None:
            params["lastname"] = self.user_lastname.strip()
        if self.user_email is not None:
            params["email"] = self.user_email 
        if self.user_address is not None:  # <-- NEW MAPPING
            params["address"] = self.user_address 
        if self.user_resume_dir is not None:
            params["resume_path"] = self.user_resume_dir  
        if self.user_resume_file_name is not None:
            params["resume_file_name"] = self.user_resume_file_name  
        if self.user_current_position is not None:
            params["current_position"] = self.user_current_position
        if self.user_current_company is not None:
            params["current_company"] = self.user_current_company
        if self.user_phone_number is not None:
            params["phone_number"] = self.user_phone_number
        if self.user_school_type is not None:
            params["school_type"] = self.user_school_type
        if self.user_graduation_year is not None:
            params["graduation_year"] = self.user_graduation_year
        if self.user_major is not None:
            params["major"] = self.user_major
        if self.user_study_level is not None:
            params["study_level"] = self.user_study_level

        return params

  
@dataclass(frozen=True)
class UserResponse:
    """This class is a data carrier from our Domain layer -> to the Application layer"""
    res_id: str
    res_fname: str
    res_lname: str
    res_email: EmailStr
    res_address: Optional[str] = None  # <-- NEW FIELD
    res_resume_path: Optional[str] = None 
    res_resume_file_name: Optional[str] = None
    res_phone_number: Optional[str] = None    
    res_current_position: Optional[str] = None
    res_current_company: Optional[str] = None
    res_school_type: Optional[str] = None
    res_graduation_year: Optional[str] = None
    res_major: Optional[str] = None
    res_study_level: Optional[str] = None

    @classmethod
    def from_entity(cls, user: User) -> Self:
        return cls(
            res_id=str(user.id),
            res_fname=user.firstname,
            res_lname=user.lastname,
            res_email=user.email,
            res_address=user.address if user.address else None,  # <-- NEW MAPPING
            res_resume_path=user.resume_path if user.resume_path else None, 
            res_resume_file_name=user.resume_file_name if user.resume_file_name else None,     
            res_phone_number=user.phone_number if user.phone_number else None,    
            res_current_position=user.current_position if user.current_position else None,
            res_current_company=user.current_company if user.current_company else None,
            res_school_type=user.school_type if user.school_type else None,
            res_graduation_year=user.graduation_year if user.graduation_year else None,
            res_major=user.major if user.major else None, 
            res_study_level=user.study_level if user.study_level else None
        )