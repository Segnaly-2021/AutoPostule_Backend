# auto_apply_app/interfaces/viewmodels/preferences_vm.py
from dataclasses import dataclass, field
from typing import Dict

@dataclass
class CredentialViewModel:
    """Represents credential status without exposing actual values"""
    login: str = ""  # Always empty (never expose actual login)
    password: str = ""  # Always empty (never expose actual password)
    configured: bool = False  # Indicates if credentials exist

@dataclass
class PreferencesViewModel:
    """
    View Model for the Settings/Preferences page.
    Matches the exact JSON structure required by the React 'Settings' component.
    """
    isFullAutomation: bool
    creativity: int
    aiModel: str  # ✅ NEW: The AI model string (e.g., 'gemini', 'claude')
    boards: Dict[str, bool]
    credentials: Dict[str, CredentialViewModel] = field(default_factory=dict)