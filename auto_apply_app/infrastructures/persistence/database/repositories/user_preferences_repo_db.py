# =============================================================================
# user_preferences_repo_db.py
# =============================================================================
from uuid import UUID
from typing import Optional
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from auto_apply_app.domain.entities.user_preferences import UserPreferences
from auto_apply_app.infrastructures.persistence.database.models.schema import UserPreferencesDB
from auto_apply_app.application.repositories.preferences_repo import UserPreferencesRepository # ✅ Added import


class UserPreferencesRepoDB(UserPreferencesRepository): # ✅ Added inheritance
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_user_id(self, user_id: UUID) -> Optional[UserPreferences]:
        result = await self.session.execute(
            select(UserPreferencesDB).where(UserPreferencesDB.user_id == user_id)
        )
        pref_db = result.scalar_one_or_none()
        return self._map_to_entity(pref_db) if pref_db else None

    

    # ✅ Added missing delete method
    async def delete(self, user_id: UUID) -> None:
        await self.session.execute(
            delete(UserPreferencesDB).where(UserPreferencesDB.user_id == user_id)
        )
        # Note: session.commit() is handled by your Unit of Work

    async def save(self, preferences: UserPreferences) -> None:
        pref_db = UserPreferencesDB(
            id=preferences.id, # 🚨 FIX 1: Crucial for merge()
            user_id=preferences.user_id,
            is_full_automation=preferences.is_full_automation,
            active_boards=preferences.active_boards,
            creativity_level=preferences.creativity_level,
            ai_model=preferences.ai_model, 
        )
        await self.session.merge(pref_db)

    def _map_to_entity(self, pref_db: UserPreferencesDB) -> UserPreferences:
        pref = UserPreferences(
            user_id=pref_db.user_id,
            is_full_automation=pref_db.is_full_automation,
            # 🚨 FIX 3: Strip InstrumentedDict before it hits LangGraph!
            active_boards=dict(pref_db.active_boards) if pref_db.active_boards else {},
            creativity_level=pref_db.creativity_level,
            ai_model=pref_db.ai_model, 
        )

        pref.id = pref_db.id
        return pref