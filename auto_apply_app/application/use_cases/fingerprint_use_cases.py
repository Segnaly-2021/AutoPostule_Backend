# auto_apply_app/application/use_cases/fingerprint_use_cases.py
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import UUID

from auto_apply_app.application.repositories.unit_of_work import UnitOfWorkFactory
from auto_apply_app.application.service_ports.fingerprint_generator_port import (
    FingerprintGeneratorPort,
)
from auto_apply_app.application.common.result import Result, Error
from auto_apply_app.domain.entities.user_fingerprint import UserFingerprint

logger = logging.getLogger(__name__)


DEFAULT_POOL_SIZE = 2
DEFAULT_DEVICE_MAX_AGE_DAYS = 3
DEFAULT_DEVICE_MAX_SESSIONS = 120
DEFAULT_RETIRED_KEEP = 20

# Rotation modes (FINGERPRINT_MODE).
MODE_POOL = "pool"
MODE_PER_RUN = "per_run"

_PER_RUN_ALIASES = {"per_run", "per-run", "perrun", "session", "per_session", "per-session"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def rotation_mode() -> str:
    """Read FINGERPRINT_MODE. Anything unrecognised falls back to the pool.

    Read per call rather than cached at import: the worker is long-lived, and a
    mode that can only be changed by a redeploy is not a mode you can A/B.
    """
    raw = (os.getenv("FINGERPRINT_MODE") or MODE_POOL).strip().lower()
    return MODE_PER_RUN if raw in _PER_RUN_ALIASES else MODE_POOL


@dataclass
class RunFingerprint:
    """What a run needs to boot its browser.

    `retired_ids` rides along so the caller can drop the GCS cookie jars keyed on
    personas that just aged out — those jars belong to a device that no longer
    exists and will never be presented again.
    """

    fingerprint: UserFingerprint
    retired_ids: list


@dataclass
class ResolveRunFingerprintUseCase:
    """Resolves the browser identity for ONE run on ONE board.

    Replaces GetOrCreateUserFingerprintUseCase, which returned a fingerprint that
    was a pure function of the user id and therefore never changed for the life
    of the account.

    Two modes, selected by FINGERPRINT_MODE:

    `pool` (default) — rotation happens at two speeds:

    - Fast (every run): the returned session variant re-rolls window size and
      canvas/audio noise. Not persisted.
    - Slow (every few days): a persona that has aged out or been used enough
      times is retired and replaced. The age ceiling is deliberately short
      (FINGERPRINT_DEVICE_MAX_AGE_DAYS, default 3) so a device never persists
      long enough to become a durable identifier across runs.

    Between those, the device itself is stable and pinned to its board — because
    the login cookies and the sticky exit IP are both keyed on the persona, and a
    board seeing one account arrive from a different machine each visit is a
    worse signal than a fingerprint that holds still.

    `per_run` — the whole device is re-minted every run: new platform, GPU, cores,
    screen, and therefore a new persona id. Consequences, all of them intended and
    none of them free:

    - The cookie jar is keyed on the persona id, so every run starts logged out
      and pays a full login.
    - The sticky proxy session is keyed on the persona id too, so every run
      arrives from a different exit IP.
    - The board sees one account hopping machines every visit, which is the
      correlation this design normally avoids on purpose.

    It exists to be measured against `pool`, not because it is safer. Keep it
    behind the env flag.
    """

    uow_factory: UnitOfWorkFactory
    generator: FingerprintGeneratorPort

    async def execute(
        self, user_id: UUID, board: str, run_token: str
    ) -> Result[RunFingerprint]:
        try:
            mode = rotation_mode()

            async with self.uow_factory() as uow:
                repo = uow.user_fingerprint_repo

                if mode == MODE_PER_RUN:
                    device, retired_ids = await self._mint_per_run_device(
                        repo, user_id, board
                    )
                else:
                    device, retired_ids = await self._resolve_pooled_device(
                        repo, user_id, board
                    )

                await repo.touch_used(device.id)
                await repo.purge_retired(
                    user_id, _int_env("FINGERPRINT_RETIRED_KEEP", DEFAULT_RETIRED_KEEP)
                )

            return Result.success(
                RunFingerprint(
                    fingerprint=self.generator.derive_session_variant(device, run_token),
                    retired_ids=retired_ids,
                )
            )

        except Exception:
            logger.exception(
                "ResolveRunFingerprintUseCase failed for user %s board %s", user_id, board
            )
            return Result.failure(
                Error.system_error("An unexpected error occurred while processing the user fingerprint.")
            )

    # -----------------------------------------------------------------------
    # Modes
    # -----------------------------------------------------------------------

    async def _mint_per_run_device(self, repo, user_id: UUID, board: str):
        """A brand-new device for this run, on a brand-new slot.

        The board's outgoing persona is retired rather than overwritten: `save`
        upserts on (user_id, slot) and keeps the existing row's id, so reusing the
        slot would hand the new device the OLD persona id — and with it the old
        cookie jar and the old exit IP. That is the one combination that is worse
        than either mode: a jar and an IP that belonged to a machine which no
        longer exists, replayed under a different fingerprint.

        Retiring also feeds `retired_ids`, which is what gets the dead jars
        deleted from the bucket instead of orphaned.
        """
        retired_ids = []
        for persona in await repo.list_by_user(user_id):
            if persona.board == board:
                await repo.retire(persona.id)
                retired_ids.append(persona.id)

        # include_retired so the new slot number clears every row this user has
        # ever held — (user_id, slot) is unique regardless of retirement.
        all_rows = await repo.list_by_user(user_id, include_retired=True)
        device = self.generator.generate_device(user_id, existing=all_rows)
        device.board = board
        device = await repo.save(device)

        logger.info(
            "per_run fingerprint: minted slot=%s for user %s board %s (retired %d)",
            device.slot, user_id, board, len(retired_ids),
        )
        return device, retired_ids

    async def _resolve_pooled_device(self, repo, user_id: UUID, board: str):
        """The default: a small pool of stable devices, each pinned to a board."""
        pool_size = max(1, _int_env("FINGERPRINT_POOL_SIZE", DEFAULT_POOL_SIZE))
        max_age_days = _int_env("FINGERPRINT_DEVICE_MAX_AGE_DAYS", DEFAULT_DEVICE_MAX_AGE_DAYS)
        max_sessions = _int_env("FINGERPRINT_DEVICE_MAX_SESSIONS", DEFAULT_DEVICE_MAX_SESSIONS)

        personas = await repo.list_by_user(user_id)

        # --- Slow rotation: retire anything worn out ---------------
        retired_ids = []
        for persona in list(personas):
            if self._is_worn_out(persona, max_age_days, max_sessions):
                await repo.retire(persona.id)
                retired_ids.append(persona.id)
                personas.remove(persona)
                logger.info(
                    "Retiring fingerprint slot=%s for user %s (age/session threshold)",
                    persona.slot, user_id,
                )

        # --- Refill the pool --------------------------------------
        # Pass the retired ones as `existing` too, so a replacement
        # doesn't reuse the slot number of a device we just retired.
        all_rows = await repo.list_by_user(user_id, include_retired=True)
        while len(personas) < pool_size:
            device = self.generator.generate_device(user_id, existing=all_rows)
            saved = await repo.save(device)
            personas.append(saved)
            all_rows.append(saved)

        # --- Pick this board's persona ----------------------------
        device = next((p for p in personas if p.board == board), None)
        if device is None:
            device = self._claim_persona_for_board(personas, board)
            device.board = board
            device = await repo.save(device)

        return device, retired_ids

    @staticmethod
    def _is_worn_out(persona: UserFingerprint, max_age_days: int, max_sessions: int) -> bool:
        if persona.session_count >= max_sessions:
            return True
        created = persona.created_at
        if created is None:
            return False
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - created > timedelta(days=max_age_days)

    @staticmethod
    def _claim_persona_for_board(personas, board: str) -> UserFingerprint:
        """Assign an unpinned persona if there is one, otherwise share the
        least-recently-used device. Sharing beats minting an unbounded number of
        machines for a user who has enabled more boards than the pool size."""
        unpinned = [p for p in personas if p.board is None]
        if unpinned:
            return unpinned[0]
        return min(
            personas,
            key=lambda p: (p.last_used_at or datetime.min.replace(tzinfo=timezone.utc)),
        )
