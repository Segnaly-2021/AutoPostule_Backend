from typing import Optional
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auto_apply_app.application.repositories.auth_repo import AuthRepository
from auto_apply_app.domain.entities.auth_user import AuthUser
from auto_apply_app.infrastructures.persistence.database.models.schema import AuthUserDB


class AuthRepoDB(AuthRepository):
    """Database implementation of AuthRepository using SQLAlchemy."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, auth_user: AuthUser) -> AuthUser:
        """
        Persist AuthUser. Using 'merge' allows this to handle
        both the initial creation and future updates (password changes,
        verification code issuance, attempt counters, etc.).
        """
        db_auth = AuthUserDB(
            id=auth_user.id,  # Explicit ID so merge() knows whether to UPDATE
            user_id=auth_user.user_id,
            email=auth_user.email,
            password_hash=auth_user.password_hash,
            is_active=auth_user.is_active,
            is_verified=auth_user.is_verified,
            created_at=auth_user.created_at,
            updated_at=auth_user.updated_at,
            last_login=auth_user.last_login,
            # --- Email verification ---
            verification_code_hash=auth_user.verification_code_hash,
            verification_code_expires_at=auth_user.verification_code_expires_at,
            verification_attempts=auth_user.verification_attempts,
            # --- Pending email change ---
            pending_email=auth_user.pending_email,
        )
        # merge() checks if the PK exists; updates if it does, inserts if it doesn't.
        await self.session.merge(db_auth)
        return auth_user

    async def get_by_email(self, email: str) -> Optional[AuthUser]:
        stmt = select(AuthUserDB).where(AuthUserDB.email == email)
        result = await self.session.execute(stmt)
        db_auth = result.scalar_one_or_none()

        return self._map_to_entity(db_auth) if db_auth else None

    async def get_by_id(self, user_id: UUID) -> Optional[AuthUser]:
        # user_id is unique but not the PK -> select(), not session.get()
        stmt = select(AuthUserDB).where(AuthUserDB.user_id == user_id)
        result = await self.session.execute(stmt)
        db_auth = result.scalar_one_or_none()

        return self._map_to_entity(db_auth) if db_auth else None

    def _map_to_entity(self, db_auth: AuthUserDB) -> AuthUser:
        """Convert Database model to Domain Entity."""
        auth = AuthUser(
            user_id=db_auth.user_id,
            email=db_auth.email,
            password_hash=db_auth.password_hash,
            is_active=db_auth.is_active,
            is_verified=db_auth.is_verified,
            created_at=db_auth.created_at,
            updated_at=db_auth.updated_at,
            last_login=db_auth.last_login,
            # --- Email verification ---
            verification_code_hash=db_auth.verification_code_hash,
            verification_code_expires_at=db_auth.verification_code_expires_at,
            verification_attempts=db_auth.verification_attempts,
            # --- Pending email change ---
            pending_email=db_auth.pending_email,
        )
        # Map the real PK back onto the entity
        auth.id = db_auth.id
        return auth