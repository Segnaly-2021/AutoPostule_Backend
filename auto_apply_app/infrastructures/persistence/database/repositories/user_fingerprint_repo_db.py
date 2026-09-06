# =============================================================================
# user_fingerprint_repo_db.py
# =============================================================================
from datetime import datetime, timezone
from uuid import UUID
from typing import List, Optional

from sqlalchemy import select, delete, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from auto_apply_app.domain.entities.user_fingerprint import UserFingerprint
from auto_apply_app.infrastructures.persistence.database.models.schema import UserFingerprintDB
from auto_apply_app.application.repositories.user_fingerprint_repo import UserFingerprintRepository


class UserFingerprintRepoDB(UserFingerprintRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_by_user(self, user_id: UUID, include_retired: bool = False) -> List[UserFingerprint]:
        stmt = select(UserFingerprintDB).where(UserFingerprintDB.user_id == user_id)
        if not include_retired:
            stmt = stmt.where(UserFingerprintDB.retired_at.is_(None))
        result = await self.session.execute(stmt.order_by(UserFingerprintDB.slot))
        return [self._map_to_entity(row) for row in result.scalars().all()]

    async def get_by_user_and_board(self, user_id: UUID, board: str) -> Optional[UserFingerprint]:
        result = await self.session.execute(
            select(UserFingerprintDB)
            .where(UserFingerprintDB.user_id == user_id)
            .where(UserFingerprintDB.board == board)
            .where(UserFingerprintDB.retired_at.is_(None))
            .order_by(UserFingerprintDB.slot)
            .limit(1)
        )
        fp_db = result.scalar_one_or_none()
        return self._map_to_entity(fp_db) if fp_db else None

    async def save(self, fingerprint: UserFingerprint) -> UserFingerprint:
        """Upsert on (user_id, slot).

        Explicitly NOT session.merge(). merge() keys on the primary key, and a
        freshly-generated entity carries a brand-new uuid4 from Entity's
        default_factory — so against an existing (user_id, slot) it attempted an
        INSERT and tripped the unique constraint instead of updating. That is
        precisely why rotation could not work before.
        """
        values = {
            "id": fingerprint.id,
            "user_id": fingerprint.user_id,
            "slot": fingerprint.slot,
            "board": fingerprint.board,
            "session_count": fingerprint.session_count,
            "last_used_at": fingerprint.last_used_at,
            "retired_at": fingerprint.retired_at,
            "platform": fingerprint.platform,
            "chrome_major": fingerprint.chrome_major,
            "viewport_width": fingerprint.viewport_width,
            "viewport_height": fingerprint.viewport_height,
            "screen_width": fingerprint.screen_width,
            "screen_height": fingerprint.screen_height,
            "device_scale_factor": fingerprint.device_scale_factor,
            "locale": fingerprint.locale,
            "timezone_id": fingerprint.timezone_id,
            "hardware_concurrency": fingerprint.hardware_concurrency,
            "device_memory": fingerprint.device_memory,
            "webgl_vendor": fingerprint.webgl_vendor,
            "webgl_renderer": fingerprint.webgl_renderer,
            "canvas_seed": fingerprint.canvas_seed,
            "audio_seed": fingerprint.audio_seed,
        }
        if fingerprint.created_at is not None:
            values["created_at"] = fingerprint.created_at

        stmt = pg_insert(UserFingerprintDB).values(**values)
        # On conflict keep the EXISTING row's id and created_at: the persona's id
        # is what the cookie jar and the sticky proxy session are keyed on, so a
        # concurrent insert must never renumber a device out from under them.
        update_cols = {
            k: getattr(stmt.excluded, k)
            for k in values
            if k not in ("id", "user_id", "slot", "created_at")
        }
        stmt = stmt.on_conflict_do_update(
            constraint="uq_user_fingerprints_user_slot",
            set_=update_cols,
        ).returning(UserFingerprintDB)

        result = await self.session.execute(stmt)
        return self._map_to_entity(result.scalar_one())

    async def touch_used(self, fingerprint_id: UUID) -> None:
        await self.session.execute(
            update(UserFingerprintDB)
            .where(UserFingerprintDB.id == fingerprint_id)
            .values(
                last_used_at=datetime.now(timezone.utc),
                session_count=UserFingerprintDB.session_count + 1,
            )
        )

    async def retire(self, fingerprint_id: UUID) -> None:
        await self.session.execute(
            update(UserFingerprintDB)
            .where(UserFingerprintDB.id == fingerprint_id)
            .where(UserFingerprintDB.retired_at.is_(None))
            .values(retired_at=datetime.now(timezone.utc), board=None)
        )

    async def purge_retired(self, user_id: UUID, keep_last: int) -> None:
        """Drop the oldest retired rows, newest `keep_last` kept.

        Ordered by slot, not retired_at: slot is monotonic per user and never
        null, so it orders rows retired inside the same second — which per_run
        mode produces routinely.
        """
        keep_last = max(0, keep_last)
        doomed = (
            select(UserFingerprintDB.id)
            .where(UserFingerprintDB.user_id == user_id)
            .where(UserFingerprintDB.retired_at.is_not(None))
            .order_by(UserFingerprintDB.slot.desc())
            .offset(keep_last)
            .scalar_subquery()
        )
        await self.session.execute(
            delete(UserFingerprintDB).where(UserFingerprintDB.id.in_(doomed))
        )

    async def delete(self, user_id: UUID) -> None:
        await self.session.execute(
            delete(UserFingerprintDB).where(UserFingerprintDB.user_id == user_id)
        )

    def _map_to_entity(self, fp_db: UserFingerprintDB) -> UserFingerprint:
        fp = UserFingerprint(
            user_id=fp_db.user_id,
            viewport_width=fp_db.viewport_width,
            viewport_height=fp_db.viewport_height,
            screen_width=fp_db.screen_width,
            screen_height=fp_db.screen_height,
            device_scale_factor=fp_db.device_scale_factor,
            locale=fp_db.locale,
            timezone_id=fp_db.timezone_id,
            hardware_concurrency=fp_db.hardware_concurrency,
            device_memory=fp_db.device_memory,
            platform=fp_db.platform,
            chrome_major=fp_db.chrome_major,
            webgl_vendor=fp_db.webgl_vendor,
            webgl_renderer=fp_db.webgl_renderer,
            canvas_seed=fp_db.canvas_seed,
            audio_seed=fp_db.audio_seed,
            slot=fp_db.slot,
            board=fp_db.board,
            session_count=fp_db.session_count,
            created_at=fp_db.created_at,
            last_used_at=fp_db.last_used_at,
            retired_at=fp_db.retired_at,
        )

        fp.id = fp_db.id
        return fp
