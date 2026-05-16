# auto_apply_app/application/use_cases/fingerprint_use_cases.py
import logging
from dataclasses import dataclass
from uuid import UUID

from auto_apply_app.application.repositories.unit_of_work import UnitOfWork
from auto_apply_app.application.service_ports.fingerprint_generator_port import (
    FingerprintGeneratorPort,
)
from auto_apply_app.application.common.result import Result, Error
from auto_apply_app.domain.entities.user_fingerprint import UserFingerprint

logger = logging.getLogger(__name__)

@dataclass
class GetOrCreateUserFingerprintUseCase:
    
    uow: UnitOfWork
    generator: FingerprintGeneratorPort

    async def execute(self, user_id: UUID) -> Result[UserFingerprint]:
        try:
            async with self.uow as uow:
                
                existing = await uow.user_fingerprint_repo.get_by_user_id(user_id)
                
                if existing:
                    return Result.success(existing)
                
                new_fingerprint = self.generator.generate_for_user(user_id)
                await uow.user_fingerprint_repo.save(new_fingerprint)
                
                return Result.success(new_fingerprint)

        except Exception:
            # Securely log the raw database/system exception to the backend console
            logger.exception(f"GetOrCreateUserFingerprintUseCase failed for user {user_id}")
            # Return a safe, sanitized message to the interface layer
            return Result.failure(
                Error.system_error("An unexpected error occurred while processing the user fingerprint.")
            )