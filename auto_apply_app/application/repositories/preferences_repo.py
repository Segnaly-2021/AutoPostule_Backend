# ==============================================================================
# FILE: auto_apply_app/application/repositories/preferences_repo.py
#
# WHAT IT DOES:
#   Interface for loading, saving and deleting each user's agent preferences.
#
# USED BY:
#   - auto_apply_app/application/repositories/unit_of_work.py: exposes it as user_pref_repo
#   - auto_apply_app/infrastructures/persistence/database/repositories/user_preferences_repo_db.py: implementation
#   - auto_apply_app/infrastructures/persistence/in_memory/memory.py: in-memory implementation
#
# EXTERNAL DEPENDENCIES:
#   - None (standard library only)
# ==============================================================================
from abc import ABC, abstractmethod
from uuid import UUID
from typing import Optional


from auto_apply_app.domain.entities.user_preferences import UserPreferences

class UserPreferencesRepository(ABC):
    """
    Repository for managing user preferences.
    """
    
    @abstractmethod
    async def get_by_user_id(self, user_id: UUID) -> Optional[UserPreferences]:
        """Retrieve preferences for a specific user"""
        pass
    
    @abstractmethod
    async def save(self, preferences: UserPreferences) -> None:
        """Save or update user preferences"""
        pass
    
    @abstractmethod
    async def delete(self, user_id: UUID) -> None:
        """Delete user preferences (e.g., when user is deleted)"""
        pass