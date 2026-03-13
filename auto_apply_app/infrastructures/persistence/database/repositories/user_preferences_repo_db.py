# =============================================================================
# user_preferences_repo_db.py
# =============================================================================
from uuid import UUID
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auto_apply_app.domain.entities.user_preferences import UserPreferences
from auto_apply_app.infrastructures.persistence.database.models.schema import UserPreferencesDB


class UserPreferencesRepoDB:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_user_id(self, user_id: UUID) -> Optional[UserPreferences]:
        result = await self.session.execute(
            select(UserPreferencesDB).where(UserPreferencesDB.user_id == user_id)
        )
        pref_db = result.scalar_one_or_none()
        return self._map_to_entity(pref_db) if pref_db else None

    async def save(self, preferences: UserPreferences) -> None:
        pref_db = UserPreferencesDB(
            user_id=preferences.user_id,
            is_full_automation=preferences.is_full_automation,
            active_boards=preferences.active_boards,
            creativity_level=preferences.creativity_level,
        )
        await self.session.merge(pref_db)

    def _map_to_entity(self, pref_db: UserPreferencesDB) -> UserPreferences:
        return UserPreferences(
            id=pref_db.user_id,  # UserPreferences extends Entity, reuse user_id as id
            user_id=pref_db.user_id,
            is_full_automation=pref_db.is_full_automation,
            active_boards=pref_db.active_boards,
            creativity_level=pref_db.creativity_level,
        )


