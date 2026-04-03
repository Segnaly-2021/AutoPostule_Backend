from dataclasses import dataclass
from typing import Dict
from pydantic import EmailStr





@dataclass(frozen=True)
class RegisterUserRequest:
    """
    Data carrier for creating the Auth credentials.
    """
    auth_email: EmailStr
    auth_password: str
    firstname: str
    lastname: str

    def __post_init__(self) -> None:
        if not self.auth_email:
            raise ValueError("Email is required")
        
        if not self.auth_password or len(self.auth_password) < 8:
            raise ValueError("Password must be at least 8 characters")
        
        if not self.firstname:
            raise ValueError("Firstname is required")
        
        if not self.firstname:
            raise ValueError("Lastname is required")

    def to_execution_params(self) -> Dict:
        """
        Returns all parameters needed for execution.
        """
        return {
            "email": self.auth_email,                      
            "password": self.auth_password,
            "firstname": self.firstname,  
            "lastname": self.lastname
        }
    
  

@dataclass(frozen=True)
class LoginRequest:
    """
    Data carrier for authentication attempts. 
    Does not need strict complexity validation, just existence checks.
    """
    auth_email: EmailStr
    auth_password: str

    def __post_init__(self) -> None:
        if not self.auth_email:
            raise ValueError("Email is required")
        
        if not self.auth_password:
            raise ValueError("Password is required")

    def to_execution_params(self) -> Dict:
        return {
            "email": self.auth_email,
            "password": self.auth_password
        }
    


@dataclass(frozen=True)
class ChangePasswordRequest:
    """
    Data carrier for password change requests.
    Requires the old password for security verification and the new password to set.
    """
    user_id: str
    old_password: str
    new_password: str

    def __post_init__(self) -> None:
        if not self.user_id:
            raise ValueError("user ID is required")
        
        if not self.old_password:
            raise ValueError("Old password is required")
        
        if not self.new_password:
            raise ValueError("New password is required")
        
        if self.old_password == self.new_password:
            raise ValueError("New password must be different from the old password")

    def to_execution_params(self) -> Dict:
        return {
            "user_id": self.user_id,
            "old_password": self.old_password,
            "new_password": self.new_password
        }



@dataclass
class LoginResponse:
    access_token: str
    token_type: str = "Bearer"



@dataclass(frozen=True)
class LogoutRequest:
    token: str
    
    def to_execution_params(self) -> Dict:
        return {"token": self.token}
    

@dataclass(frozen=True)
class ForgotPasswordRequest:
    email: str

@dataclass(frozen=True)
class ResetPasswordRequest:
    token: str
    new_password: str
    

