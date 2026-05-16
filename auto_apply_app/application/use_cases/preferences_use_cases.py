import logging
from dataclasses import dataclass

from auto_apply_app.application.service_ports.encryption_port import EncryptionServicePort
from auto_apply_app.application.dtos.preferences_dtos import UpdateUserPreferencesRequest
from auto_apply_app.domain.entities.user_preferences import UserPreferences
from auto_apply_app.domain.entities.board_credentials import BoardCredential
from auto_apply_app.application.repositories.unit_of_work import UnitOfWork
from auto_apply_app.application.dtos.preferences_dtos import (
    GetUserPreferencesRequest, 
    UserPreferencesResponse
)
from auto_apply_app.application.common.result import Result, Error

logger = logging.getLogger(__name__)

@dataclass
class GetUserPreferencesUseCase:
    
    uow: UnitOfWork

    async def execute(self, request: GetUserPreferencesRequest) -> Result[UserPreferencesResponse]:
        try:
            # 1. Extract validated parameters
            params = request.to_execution_params()
            user_id = params["user_id"]

            # 2. Start Transaction (Read-only)
            async with self.uow as uow:
                
                # Fetch Data from Multiple Repositories
                prefs = await uow.user_pref_repo.get_by_user_id(user_id)
                creds = await uow.board_cred_repo.get_all_by_user(user_id)
                
                # 3. Map to Response DTO using your factory method
                response = UserPreferencesResponse.from_entity(prefs, creds)
                
                return Result.success(response)

        except Exception:
            logger.exception("GetUserPreferencesUseCase failed")
            return Result.failure(Error.system_error("An unexpected error occurred while retrieving user preferences."))

        
@dataclass
class UpdateUserPreferencesUseCase:
    uow: UnitOfWork
    encryption_service: EncryptionServicePort

    async def execute(self, request: UpdateUserPreferencesRequest) -> Result[None]:
        try:
            params = request.to_execution_params()
            user_id = params["user_id"]
            new_credentials = params.get("credentials")

            async with self.uow as uow:
                
                current_prefs = await uow.user_pref_repo.get_by_user_id(user_id)
                
                if not current_prefs:
                    current_prefs = UserPreferences(user_id=user_id)

                # Update Domain Entity
                current_prefs.is_full_automation = params["is_full_automation"]
                current_prefs.set_creativity(params["creativity_level"]) 
                current_prefs.set_ai_model(params["ai_model"]) # ✅ NEW
                current_prefs.active_boards = params["active_boards"]
                
                await uow.user_pref_repo.save(current_prefs)

                # --- PART B: Credentials (Remains exactly the same) ---
                if new_credentials:                    
                    for board_name, cred_dto in new_credentials.items():
                        existing_cred = await uow.board_cred_repo.get_by_user_and_board(user_id, board_name)
                        
                        if existing_cred:
                            new_login_enc = existing_cred.login_encrypted
                            new_pass_enc = existing_cred.password_encrypted
                            has_changes = False

                            if cred_dto.login: 
                                new_login_enc = await self.encryption_service.encrypt(cred_dto.login)
                                has_changes = True
                            
                            if cred_dto.password:
                                new_pass_enc = await self.encryption_service.encrypt(cred_dto.password)
                                has_changes = True

                            if has_changes:
                                existing_cred.update_credentials(new_login_enc, new_pass_enc)
                                await uow.board_cred_repo.save(existing_cred)
                        
                        else:                            
                            if not cred_dto.login or not cred_dto.password:
                                continue

                            encrypted_login = await self.encryption_service.encrypt(cred_dto.login)
                            encrypted_password = await self.encryption_service.encrypt(cred_dto.password)

                            new_cred = BoardCredential(
                                user_id=user_id,
                                job_board=board_name,
                                login_encrypted=encrypted_login,
                                password_encrypted=encrypted_password
                            )
                            await uow.board_cred_repo.save(new_cred)

                await uow.commit()
                return Result.success(value={"message": "Preferences Successfully Updated"})

        except ValueError as e:
            return Result.failure(Error.validation_error(str(e)))
        except Exception:
            logger.exception("UpdateUserPreferencesUseCase failed")
            return Result.failure(Error.system_error("An unexpected error occurred while updating user preferences."))