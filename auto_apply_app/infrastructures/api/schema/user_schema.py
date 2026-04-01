# interfaces/api/schemas/auth_schemas.py
from pydantic import BaseModel, EmailStr, Field
from typing import Optional


class RegisterSchema(BaseModel):
    """Schema for user registration."""
    firstname: str = Field(min_length=1, max_length=50)
    lastname: str = Field(min_length=1, max_length=50)
    email: EmailStr
    password: str = Field(min_length=8)


class LoginSchema(BaseModel):
    """Schema for user login."""
    email: EmailStr
    password: str = Field(min_length=8)


class ChangePasswordSchema(BaseModel):
    """
    Schema for changing password.
    
    ⚠️ SECURITY NOTE: user_id is NOT included here.
    It will be extracted from the JWT token to prevent users
    from changing other users' passwords.
    """
    old_password: str = Field(min_length=8)
    new_password: str = Field(min_length=8)



class UserUpdateSchema(BaseModel):
    """Schema for partial updates to the user profile."""
    firstname: Optional[str] = Field(None, min_length=0, max_length=50)
    lastname: Optional[str] = Field(None, min_length=0, max_length=50)
    email: Optional[EmailStr] = None
    resume_path: Optional[str] = None
    current_position: Optional[str] = None
    current_company: Optional[str] = None
    address: Optional[str] = None
    phone_number: Optional[str] = None
    school_type: Optional[str] = None
    graduation_year: Optional[str] = None
    major: Optional[str] = None
    study_level: Optional[str] = None
 
