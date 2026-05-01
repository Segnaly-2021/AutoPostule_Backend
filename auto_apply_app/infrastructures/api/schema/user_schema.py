import re
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, field_validator

# --- Reusable Password Validator ---
def validate_secure_password(v: str) -> str:
    if len(v) < 8:
        raise ValueError("Password must be at least 8 characters long.")
    if not re.search(r"[A-Z]", v):
        raise ValueError("Password must contain an uppercase letter.")
    if not re.search(r"\d", v):
        raise ValueError("Password must contain a number.")
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", v):
        raise ValueError("Password must contain a special character.")
    return v


# --- Registration & Login ---
class RegisterSchema(BaseModel):
    """Schema for user registration."""
    firstname: str = Field(min_length=1, max_length=50)
    lastname: str = Field(min_length=1, max_length=50)
    email: EmailStr
    password: str = Field(min_length=8)

    @field_validator('password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        return validate_secure_password(v)


class LoginSchema(BaseModel):
    """
    Schema for user login.
    NOTE: We do NOT enforce strict complexity here. 
    This ensures legacy users with older, weaker passwords are not locked out.
    """
    email: EmailStr
    password: str 


# --- Password Management ---
class ChangePasswordSchema(BaseModel):
    """
    Schema for changing password from inside the app settings.
    """
    old_password: str
    new_password: str = Field(min_length=8)

    @field_validator('new_password')
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        return validate_secure_password(v)


# --- Forgot / Reset Password (NEW) ---
class ForgotPasswordRequestSchema(BaseModel):
    """Schema for requesting a password reset email."""
    email: EmailStr


class ResetPasswordConfirmSchema(BaseModel):
    """Schema for submitting a new password using an email token."""
    token: str = Field(min_length=1)
    new_password: str = Field(min_length=8)

    @field_validator('new_password')
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        return validate_secure_password(v)



class UserUpdateSchema(BaseModel):
    """Schema for partial updates to the user profile."""
    firstname: Optional[str] = Field(None, min_length=0, max_length=50)
    lastname: Optional[str] = Field(None, min_length=0, max_length=50)
    email: Optional[EmailStr] = None
    resume_path: Optional[str] = None
    current_position: Optional[str] = None
    current_company: Optional[str] = None
    address: Optional[str] = None
    linkedin_url: Optional[str] = None
    phone_number: Optional[str] = None
    school_type: Optional[str] = None
    graduation_year: Optional[str] = None
    major: Optional[str] = None
    study_level: Optional[str] = None
 
