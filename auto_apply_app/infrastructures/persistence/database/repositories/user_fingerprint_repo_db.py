# =============================================================================
# user_fingerprint_repo_db.py
# =============================================================================
from uuid import UUID
from typing import Optional
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from auto_apply_app.domain.entities.user_fingerprint import UserFingerprint
from auto_apply_app.infrastructures.persistence.database.models.schema import UserFingerprintDB
from auto_apply_app.application.repositories.user_fingerprint_repo import UserFingerprintRepository


class UserFingerprintRepoDB(UserFingerprintRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_user_id(self, user_id: UUID) -> Optional[UserFingerprint]:
        result = await self.session.execute(
            select(UserFingerprintDB).where(UserFingerprintDB.user_id == user_id)
        )
        fp_db = result.scalar_one_or_none()
        return self._map_to_entity(fp_db) if fp_db else None

    async def delete(self, user_id: UUID) -> None:
        await self.session.execute(
            delete(UserFingerprintDB).where(UserFingerprintDB.user_id == user_id)
        )

    async def save(self, fingerprint: UserFingerprint) -> None:
        fp_db = UserFingerprintDB(
            id=fingerprint.id,
            user_id=fingerprint.user_id,
            user_agent=fingerprint.user_agent,
            viewport_width=fingerprint.viewport_width,
            viewport_height=fingerprint.viewport_height,
            device_scale_factor=fingerprint.device_scale_factor,
            locale=fingerprint.locale,
            timezone_id=fingerprint.timezone_id,
            hardware_concurrency=fingerprint.hardware_concurrency,
            platform=fingerprint.platform,
            webgl_vendor=fingerprint.webgl_vendor,
            webgl_renderer=fingerprint.webgl_renderer,
        )
        await self.session.merge(fp_db)

    def _map_to_entity(self, fp_db: UserFingerprintDB) -> UserFingerprint:
        fp = UserFingerprint(
            user_id=fp_db.user_id,
            user_agent=fp_db.user_agent,
            viewport_width=fp_db.viewport_width,
            viewport_height=fp_db.viewport_height,
            device_scale_factor=fp_db.device_scale_factor,
            locale=fp_db.locale,
            timezone_id=fp_db.timezone_id,
            hardware_concurrency=fp_db.hardware_concurrency,
            platform=fp_db.platform,
            webgl_vendor=fp_db.webgl_vendor,
            webgl_renderer=fp_db.webgl_renderer,
        )

        fp.id = fp_db.id
        return fp