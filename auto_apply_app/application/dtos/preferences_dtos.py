# auto_apply_app/application/dtos/preferences_dtos.py
from dataclasses import dataclass
from typing import Dict, Optional, List, Any, Self
from uuid import UUID

from auto_apply_app.domain.entities.user_preferences import UserPreferences
from auto_apply_app.domain.entities.board_credentials import BoardCredential

@dataclass(frozen=True)
class BoardCredentialDTO:
    login: str
    password: str

@dataclass(frozen=True)
class CredentialStatusDTO:
    login: str = ""  
    password: str = "" 
    configured: bool = False

@dataclass(frozen=True)
class UpdateUserPreferencesRequest:
    user_id: str
    is_full_automation: bool
    creativity_level: int
    ai_model: str  # ✅ NEW
    active_boards: Dict[str, bool]
    credentials: Optional[Dict[str, BoardCredentialDTO]] = None

    def __post_init__(self) -> None:
        if not self.user_id.strip():
            raise ValueError("User ID is required")
        
        if not 0 <= self.creativity_level <= 10:
            raise ValueError("Creativity level must be between 0 and 10")
            
        # ✅ NEW: Fast validation at the edge
        valid_models = ["gemini", "claude", "chatgpt"]
        if self.ai_model.lower() not in valid_models:
            raise ValueError(f"AI model must be one of {valid_models}")

    def to_execution_params(self) -> Dict[str, Any]:
        return {
            "user_id": UUID(self.user_id),
            "is_full_automation": self.is_full_automation,
            "creativity_level": self.creativity_level,
            "ai_model": self.ai_model.lower(), # ✅ NEW
            "active_boards": self.active_boards,
            "credentials": self.credentials
        }

@dataclass(frozen=True)
class GetUserPreferencesRequest:
    user_id: str

    def __post_init__(self) -> None:
        if not self.user_id.strip():
            raise ValueError("User ID is required")

    def to_execution_params(self) -> Dict[str, Any]:
        return {"user_id": UUID(self.user_id)}

@dataclass(frozen=True)
class UserPreferencesResponse:
    is_full_automation: bool
    creativity_level: int
    ai_model: str # ✅ NEW
    active_boards: Dict[str, bool]
    credentials: Dict[str, CredentialStatusDTO] 

    @classmethod
    def from_entity(
        cls, 
        prefs: Optional[UserPreferences], 
        credentials: List[BoardCredential]
    ) -> Self:
        
        if prefs:
            is_full = prefs.is_full_automation
            creativity = prefs.creativity_level
            ai_model = prefs.ai_model # ✅ NEW
            active_boards = prefs.active_boards
        else:
            default_prefs = UserPreferences(user_id=UUID('00000000-0000-0000-0000-000000000000'))
            is_full = default_prefs.is_full_automation
            creativity = default_prefs.creativity_level
            ai_model = default_prefs.ai_model # ✅ NEW
            active_boards = default_prefs.active_boards

        all_boards = ['hellowork', 'wttj', 'apec']
        credentials_structure = {}
        configured_boards = {c.job_board.lower(): c for c in credentials}
        
        for board in all_boards:
            is_configured = board in configured_boards and bool(
                configured_boards[board].login_encrypted and 
                configured_boards[board].password_encrypted
            )
            
            credentials_structure[board] = CredentialStatusDTO(
                login="",  
                password="", 
                configured=is_configured
            )

        return cls(
            is_full_automation=is_full,
            creativity_level=creativity,
            ai_model=ai_model, # ✅ NEW
            active_boards=active_boards,
            credentials=credentials_structure 
        )