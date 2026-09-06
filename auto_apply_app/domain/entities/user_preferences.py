# auto_apply_app/domain/entities/user_preferences.py
from dataclasses import dataclass, field
from uuid import UUID
from typing import Dict

from auto_apply_app.domain.entities.entity import Entity

@dataclass
class UserPreferences(Entity):
    """
    Represents user-specific application preferences.
    These are NOT authentication credentials, but behavioral settings.
    """
    user_id: UUID
    
    # Automation Strategy
    is_full_automation: bool = True
    
    # Active Boards
    active_boards: Dict[str, bool] = field(default_factory=lambda: {
        'hellowork': True,
        'wttj': False,
        'apec': False,
        'indeed': False
    })
    
    # AI Configuration
    creativity_level: int = 8  # 0-10 scale
    
    # ✅ NEW: AI Model Selection (Defaulting to gemini)
    ai_model: str = "gemini" 
    
    # Computed Properties
    @property
    def llm_temperature(self) -> float:
        """Convert 0-10 creativity to 0.0-1.0 temperature"""
        return self.creativity_level / 10.0
    
    @property
    def browser_headless(self) -> bool:
        """DEPRECATED — reads as its own opposite. Use `run_browser_headless`.

        It returns `is_full_automation`, and every worker then negated it:
        `headless = not preferences.browser_headless`. Two inversions cancelling
        out means the name has never described the value, and a reader checking
        whether a run is headless got the wrong answer from the property that
        claims to say so.
        """
        return self.is_full_automation

    @property
    def run_browser_headless(self) -> bool:
        """Whether this run's browser should be headless.

        Says what it means, and defaults SAFE. The old chain defaulted the other
        way: `is_full_automation` is True on a fresh in-memory entity, so a user
        with no preferences row — which `run_context_use_cases` constructs on the
        spot — resolved to a headful launch. In Cloud Run there is no X server,
        so that browser cannot start at all.

        Headful is now something a run opts into by having a display, not
        something it falls into by having no preferences.
        """
        return not self.is_full_automation
    
    # Business Logic
    def is_board_active(self, board_name: str) -> bool:
        return self.active_boards.get(board_name.lower(), False)
    
    def get_active_boards(self) -> list[str]:
        return [board for board, active in self.active_boards.items() if active]
    
    def update_board_status(self, board_name: str, is_active: bool) -> None:
        self.active_boards[board_name.lower()] = is_active
    
    def set_creativity(self, level: int) -> None:
        if not 0 <= level <= 10:
            raise ValueError("Creativity level must be between 0 and 10")
        self.creativity_level = level

    # ✅ NEW: Domain validation for AI model
    def set_ai_model(self, model_name: str) -> None:
        """Set the preferred AI model"""
        valid_models = ["gemini", "claude", "chatgpt"]
        if model_name.lower() not in valid_models:
            raise ValueError(f"AI model must be one of {valid_models}")
        self.ai_model = model_name.lower()
        
    def toggle_automation_mode(self) -> None:
        self.is_full_automation = not self.is_full_automation