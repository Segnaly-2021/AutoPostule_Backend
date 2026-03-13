# =============================================================================
# board_credentials_repo_db.py
# =============================================================================
from uuid import UUID
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auto_apply_app.domain.entities.board_credentials import BoardCredential
from auto_apply_app.infrastructures.persistence.database.models.schema import BoardCredentialDB


class BoardCredentialRepoDB:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_user_and_board(
        self, user_id: UUID, job_board: str
    ) -> Optional[BoardCredential]:
        result = await self.session.execute(
            select(BoardCredentialDB)
            .where(BoardCredentialDB.user_id == user_id)
            .where(BoardCredentialDB.job_board == job_board.lower())
        )
        cred_db = result.scalar_one_or_none()
        return self._map_to_entity(cred_db) if cred_db else None

    async def save(self, credential: BoardCredential) -> None:
        cred_db = BoardCredentialDB(
            id=credential.id,
            user_id=credential.user_id,
            job_board=credential.job_board.lower(),
            login_encrypted=credential.login_encrypted,
            password_encrypted=credential.password_encrypted,
            is_verified=credential.is_verified,
            last_verified_at=credential.last_verified_at,
            created_at=credential.created_at,
            updated_at=credential.updated_at,
        )
        await self.session.merge(cred_db)

    async def delete(self, user_id: UUID, job_board: str) -> None:
        result = await self.session.execute(
            select(BoardCredentialDB)
            .where(BoardCredentialDB.user_id == user_id)
            .where(BoardCredentialDB.job_board == job_board.lower())
        )
        cred_db = result.scalar_one_or_none()
        if cred_db:
            await self.session.delete(cred_db)

    def _map_to_entity(self, cred_db: BoardCredentialDB) -> BoardCredential:
        return BoardCredential(
            id=cred_db.id,
            user_id=cred_db.user_id,
            job_board=cred_db.job_board,
            login_encrypted=cred_db.login_encrypted,
            password_encrypted=cred_db.password_encrypted,
            is_verified=cred_db.is_verified,
            last_verified_at=cred_db.last_verified_at,
            created_at=cred_db.created_at,
            updated_at=cred_db.updated_at,
        )