# auto_apply_app/application/use_cases/fingerprint_use_cases.py
from dataclasses import dataclass
from uuid import UUID

from auto_apply_app.application.repositories.unit_of_work import UnitOfWork
from auto_apply_app.application.common.result import Result, Error
from auto_apply_app.domain.entities.user_fingerprint import UserFingerprint
from auto_apply_app.infrastructures.agent.fingerprint_generation_service import (
    FingerprintGenerationService,
)


@dataclass
class GetOrCreateUserFingerprintUseCase:
    
    uow: UnitOfWork
    generator: FingerprintGenerationService

    async def execute(self, user_id: UUID) -> Result[UserFingerprint]:
        try:
            async with self.uow as uow:
                
                existing = await uow.user_fingerprint_repo.get_by_user_id(user_id)
                
                if existing:
                    return Result.success(existing)
                
                new_fingerprint = self.generator.generate_for_user(user_id)
                await uow.user_fingerprint_repo.save(new_fingerprint)
                
                return Result.success(new_fingerprint)

        except Exception as e:
            return Result.failure(Error.system_error(str(e)))