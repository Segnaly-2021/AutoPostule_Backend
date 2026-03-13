# auto_apply_app/infrastructures/api/schema/preferences_schema.py

from pydantic import BaseModel, Field
from typing import Dict, Optional

class BoardCredentialInput(BaseModel):
    """Schema for board credentials input"""
    login: str = Field(..., min_length=1, description="Login/Email for the job board")
    password: str = Field(..., min_length=1, description="Password for the job board")

class CredentialStatus(BaseModel):
    """Schema for credential status output (never exposes actual values)"""
    login: str = ""  # Always empty
    password: str = ""  # Always empty
    configured: bool = Field(default=False, description="Whether credentials are saved")

class PreferencesResponseSchema(BaseModel):
    """Schema for preferences response"""
    isFullAutomation: bool
    creativity: int
    aiModel: str  # ✅ NEW: Add the AI model to the response
    boards: Dict[str, bool]
    credentials: Dict[str, CredentialStatus] 
    
    class Config:
        json_schema_extra = {
            "example": {
                "isFullAutomation": False,
                "creativity": 8,
                "aiModel": "gemini", # ✅ NEW
                "boards": {
                    "hellowork": True,
                    "wttj": True,
                    "apec": False
                },
                "credentials": {
                    "hellowork": {
                        "login": "",
                        "password": "",
                        "configured": True
                    },
                    "wttj": {
                        "login": "",
                        "password": "",
                        "configured": False
                    },
                    "apec": {
                        "login": "",
                        "password": "",
                        "configured": False
                    }
                }
            }
        }

class UpdatePreferencesSchema(BaseModel):
    """
    Schema for updating user preferences.
    Matches the structure sent by the React Settings component.
    """
    isFullAutomation: bool = Field(
        default=False,
        description="Full automation (headless) vs Semi automation (visible browser)"
    )
    
    creativity: int = Field(
        default=8,
        ge=0,
        le=10,
        description="AI creativity level (0-10)"
    )
    
    # ✅ NEW: Validate the input from the frontend
    aiModel: str = Field(
        default="gemini",
        description="Preferred AI model (e.g., gemini, claude, chatgpt)"
    )
    
    boards: Dict[str, bool] = Field(
        default_factory=lambda: {'hellowork': True, 'wttj': True, 'apec': False},
        description="Active job boards"
    )
    
    credentials: Optional[Dict[str, BoardCredentialInput]] = Field(
        default=None,
        description="Job board credentials (only required for full automation)"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "isFullAutomation": True,
                "creativity": 8,
                "aiModel": "gemini", # ✅ NEW
                "boards": {
                    "hellowork": True,
                    "wttj": True,
                    "apec": False
                },
                "credentials": {
                    "hellowork": {
                        "login": "user@example.com",
                        "password": "secure_password"
                    }
                }
            }
        }