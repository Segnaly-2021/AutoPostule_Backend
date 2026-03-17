# =============================================================================
# user_repo_db.py
# =============================================================================
from uuid import UUID
from typing import List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auto_apply_app.domain.entities.user import User
from auto_apply_app.application.repositories.user_repo import UserRepository
from auto_apply_app.infrastructures.persistence.database.models.schema import UserDB
from auto_apply_app.domain.exceptions import UserNotFoundError


class UserRepoDB(UserRepository):
    IMMUTABLE_FIELDS = frozenset({"id"})

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, user_id: UUID) -> User:
        result = await self.session.execute(
            select(UserDB).where(UserDB.id == user_id)
        )
        user_db = result.scalar_one_or_none()
        if user_db is None:
            raise UserNotFoundError(f"User {user_id} not found")
        return self._map_to_entity(user_db)

    # ✅ FIXED: Added missing get_by_email method
    async def get_by_email(self, email: str) -> User:
        result = await self.session.execute(
            select(UserDB).where(UserDB.email == email)
        )
        user_db = result.scalar_one_or_none()
        if user_db is None:
            raise UserNotFoundError(f"User with email {email} not found")
        return self._map_to_entity(user_db)

    # ✅ FIXED: Made skip/take optional so it safely matches InMemory interface
    async def get_all(self, skip: int = 0, take: int = 100) -> List[User]:
        result = await self.session.execute(select(UserDB).offset(skip).limit(take))
        return [self._map_to_entity(u) for u in result.scalars().all()]

    async def save(self, user: User) -> None:
        """Upsert — handles both create and update."""
        user_db = UserDB(
            id=user.id,
            firstname=user.firstname,
            lastname=user.lastname,
            email=user.email,
            resume_path=user.resume_path,
            phone_number=user.phone_number,
            current_position=user.current_position,
            current_company=user.current_company,
            school_type=user.school_type,
            graduation_year=user.graduation_year,
            major=user.major,
            study_level=user.study_level,
        )
        await self.session.merge(user_db)

    async def delete(self, user_id: UUID) -> None:
        result = await self.session.execute(select(UserDB).where(UserDB.id == user_id))
        user_db = result.scalar_one_or_none()
        if user_db is None:
            raise UserNotFoundError(f"User {user_id} not found")
        await self.session.delete(user_db)

    # ✅ FIXED: Return type is now User, perfectly matching the In-Memory repo
    async def update(self, user_id: UUID, data: dict) -> User:
        result = await self.session.execute(select(UserDB).where(UserDB.id == user_id))
        user_db = result.scalar_one_or_none()
        if user_db is None:
            raise UserNotFoundError(f"User {user_id} not found")
            
        for key, value in data.items():
            if key not in self.IMMUTABLE_FIELDS and hasattr(user_db, key):
                setattr(user_db, key, value)
                
        return self._map_to_entity(user_db)

    def _map_to_entity(self, user_db: UserDB) -> User:
        # 1. Instantiate without the 'id' argument
        user = User(
            firstname=user_db.firstname,
            lastname=user_db.lastname,
            email=user_db.email,
            resume_path=user_db.resume_path,
            resume_file_name=user_db.resume_file_name, # <-- Added this!
            phone_number=user_db.phone_number,
            current_position=user_db.current_position,
            current_company=user_db.current_company,
            school_type=user_db.school_type,
            graduation_year=user_db.graduation_year,
            major=user_db.major,
            study_level=user_db.study_level,
        )
        
        # 2. Overwrite the auto-generated UUID with the real one from the DB
        user.id = user_db.id 
        
        return user