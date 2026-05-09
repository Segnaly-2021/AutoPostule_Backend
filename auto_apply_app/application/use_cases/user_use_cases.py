from dataclasses import dataclass
from uuid import UUID
import datetime
import logging



from auto_apply_app.application.dtos.operations import DeletionOutcome
from auto_apply_app.application.common.result import Error, Result
from auto_apply_app.domain.entities.user import User
from auto_apply_app.domain.exceptions import (
  UserNotFoundError,
  ValidationError,
  BusinessRuleViolation,
  InvalidTokenException
)
from auto_apply_app.application.dtos.user_dtos import (   
  UpdateUserRequest,
  GetUserRequest,
  UserResponse
)
from auto_apply_app.application.dtos.auth_user_dtos import (
  RegisterUserRequest, 
  LoginResponse, 
  ChangePasswordRequest,
  ForgotPasswordRequest, 
  ResetPasswordRequest
)


from auto_apply_app.application.service_ports.email_service_port import EmailServicePort
from auto_apply_app.application.repositories.unit_of_work import UnitOfWork
from auto_apply_app.application.service_ports.password_service_port import PasswordServicePort
from auto_apply_app.application.service_ports.file_storage_port import FileStoragePort
from auto_apply_app.application.dtos.auth_user_dtos import LoginRequest
from auto_apply_app.application.service_ports.token_provider_port import TokenProviderPort
from auto_apply_app.domain.entities.auth_user import AuthUser
from auto_apply_app.domain.entities.user_preferences import UserPreferences
from auto_apply_app.domain.entities.user_subscription import UserSubscription

from auto_apply_app.application.repositories.token_blacklist import TokenBlacklistRepository
from auto_apply_app.domain.value_objects import ClientType


logger = logging.getLogger(__name__)


@dataclass
class RegisterUserUseCase:
    uow: UnitOfWork
    password_service: PasswordServicePort
    token_provider: TokenProviderPort                  # NEW
    email_service: EmailServicePort                    # NEW

    async def execute(self, request: RegisterUserRequest) -> Result:
        try:
            params = request.to_execution_params()

            async with self.uow:
                existing_auth = await self.uow.auth_repo.get_by_email(params["email"])
                if existing_auth:
                    return Result.failure(Error.conflict("User with this email already exists"))

                user = User(
                    firstname=params["firstname"],
                    lastname=params["lastname"],
                    email=params["email"],
                    phone_number=None,
                    address=None,
                    school_type=None,
                    graduation_year=None,
                    major=None,
                    study_level=None,
                )

                raw_password = params.pop("password")
                hashed_password = self.password_service.get_password_hash(raw_password)
                user_id = user.id

                auth_user = AuthUser(
                    user_id=user_id,
                    email=params["email"],
                    password_hash=hashed_password,
                )

                sub_user = UserSubscription(
                    user_id=user_id,
                    email=params["email"],                    
                )

                user_prefs = UserPreferences(user_id=user.id)

                await self.uow.user_repo.save(user)
                await self.uow.auth_repo.save(auth_user)
                await self.uow.subscription_repo.save(sub_user)
                await self.uow.user_pref_repo.save(user_prefs)

            # 4. Generate verification token + send email (outside UoW — non-critical)
            try:
                verification_token = self.token_provider.encode_token(
                    user_id=user_id,
                    claims={"email": params["email"], "purpose": "email_verification"},
                    expires_delta=datetime.timedelta(hours=24),
                )
                await self.email_service.send_verification_email(
                    to_email=params["email"],
                    verification_token=verification_token,
                )
            except Exception:
                # Don't fail registration if email fails — user can request resend.
                logger.exception("Failed to send verification email to %s", params["email"])

            return Result.success(UserResponse.from_entity(user))

        except ValidationError as e:
            return Result.failure(Error.validation_error(str(e)))
        except Exception:
            logger.exception("CRITICAL: Failed to register user")
            return Result.failure(Error.system_error("Could not complete registration."))


@dataclass
class LoginUserUseCase:
    password_service: PasswordServicePort
    token_provider: TokenProviderPort
    uow: UnitOfWork

    async def execute(self, request: LoginRequest) -> Result:
        try:
            params = request.to_execution_params()

            async with self.uow:
                auth_user = await self.uow.auth_repo.get_by_email(params["email"])
                
                if not auth_user:
                    # Same generic response for "not found" vs "wrong password" — anti-enumeration
                    return Result.failure(Error.unauthorized("Invalid credentials"))

                if not self.password_service.verify(params["password"], auth_user.password_hash):
                    return Result.failure(Error.unauthorized("Invalid credentials"))

                # NEW: block unverified accounts
                if not auth_user.is_verified:
                    return Result.failure(Error.unauthorized(
                        "Please verify your email before logging in. "
                        "Check your inbox for the verification link."
                    ))

                auth_user.record_login()
                await self.uow.auth_repo.save(auth_user)

                token = self.token_provider.encode_token(
                    user_id=auth_user.user_id,
                    claims={"email": auth_user.email},
                )

            return Result.success(LoginResponse(access_token=token, token_type="Bearer"))

        except ValueError as e:
            return Result.failure(Error.validation_error(str(e)))
        except Exception:
            logger.exception("CRITICAL: Failed to login user")
            return Result.failure(Error.system_error("Could not complete login."))



@dataclass
class RequestPasswordResetUseCase:
    uow: UnitOfWork
    token_provider: TokenProviderPort
    email_service: EmailServicePort

    async def execute(self, request: ForgotPasswordRequest) -> Result:
        try:
            async with self.uow:
                # 1. Find the user
                auth_user = await self.uow.auth_repo.get_by_email(request.email)
                
                # SECURITY NOTE: Do not reveal if the email exists to prevent enumeration.
                if not auth_user:
                    return Result.success({"message": "If an account exists, a reset link has been sent."})

                # 2. Generate a 15-minute reset token
                reset_token = self.token_provider.encode_token(
                    user_id=auth_user.user_id,
                    claims={"email": auth_user.email, "purpose": "password_reset"},
                    expires_delta=datetime.timedelta(minutes=15)
                )

                # 3. Send the email
                await self.email_service.send_password_reset_email(
                    to_email=auth_user.email, 
                    reset_token=reset_token
                )

            # Return a dictionary instead of a raw string
            return Result.success({"message": "If an account exists, a reset link has been sent."})

        except Exception as e:
            # 🚨 ADD THIS LINE to expose the real error in GCP logs!
            logger.exception(f"CRITICAL: Failed to reset password for user {request.email}")
            return Result.failure(Error.system_error(str(e)))


@dataclass
class ConfirmPasswordResetUseCase:
    uow: UnitOfWork
    token_provider: TokenProviderPort
    password_service: PasswordServicePort

    async def execute(self, request: ResetPasswordRequest) -> Result:
        try:
            # 1. Validate Token
            payload = self.token_provider.decode_token(request.token)
            
            # Security check: Ensure this is a reset token
            if payload.get("purpose") != "password_reset":
                return Result.failure(Error.unauthorized("Invalid token type."))
                
            user_id = payload.get("sub")
            if not user_id:
                return Result.failure(Error.unauthorized("Invalid token payload."))

            async with self.uow as uow:
                # 2. Fetch User
                auth_user = await uow.auth_repo.get_by_id(UUID(user_id))
                if not auth_user:
                    return Result.failure(Error.not_found("User", str(user_id)))

                # 3. Hash New Password
                new_hashed_password = self.password_service.get_password_hash(request.new_password)

                # 4. Update Domain Entity
                auth_user.change_password(new_hashed_password)

                # 5. Persist
                await uow.auth_repo.save(auth_user)

            # Return a dictionary instead of a raw string
            return Result.success({"message": "Password reset successfully."})

        except InvalidTokenException:
            return Result.failure(Error.unauthorized("Token is invalid or has expired."))
        except Exception as e:
            # 🚨 ADD THIS LINE to expose the real error in GCP logs!
            logger.exception(f"CRITICAL: Failed to confirm password reset for user {request.email}")
            return Result.failure(Error.system_error(str(e)))


@dataclass
class LogoutUseCase:    
        
    token_provider: TokenProviderPort
    token_blacklist_repo: TokenBlacklistRepository


    async def execute(self, token: str) -> None:
        """
        Invalidates the given token so it cannot be used again.
        """
        try:
            
            # 1. Extract the unique ID (JTI)
            jti = self.token_provider.get_token_id(token)
            
            # 2. Calculate how much time is left on the token
            ttl = self.token_provider.get_token_ttl(token)

            # 3. If the token is valid and has time remaining, blacklist it.
            #    If ttl is 0, it's already expired, so no need to blacklist.
            if jti and ttl > 0:
                self.token_blacklist_repo.blacklist_token(token_id=jti, ttl_seconds=ttl)
                
        except InvalidTokenException:
            # If the token is already invalid (malformed, expired, fake), 
            # logging out is technically a "success" (the user session is dead).
            # We fail silently here.
            pass





@dataclass
class ChangePasswordUseCase:
    
    password_service: PasswordServicePort
    uow: UnitOfWork

    async def execute(self, request: ChangePasswordRequest) -> Result:
        try:
            params = request.to_execution_params()
            async with self.uow as uow:
                # 1. Fetch User by ID (from Token)
                auth_user = await uow.auth_repo.get_by_id(params["user_id"])
                
                if not auth_user:
                    return Result.failure(Error.not_found("User", str(params["user_id"])))

                # 2. Security Check: Verify OLD Password
                # This ensures the user actually owns the account
                if not self.password_service.verify(params["old_password"], auth_user.password_hash):
                    return Result.failure(Error.unauthorized("Invalid old password"))

                # 3. Hash the NEW Password
                new_hashed_password = self.password_service.get_password_hash(params["new_password"])

                # 4. Domain Logic: Update Entity
                # This handles setting the new hash and updating 'updated_at'
                auth_user.change_password(new_hashed_password)

                # 5. Persist Changes
                await uow.auth_repo.save(auth_user)

            # 6. Response
            return Result.success("Password changed successfully")

        except ValidationError as e:
            return Result.failure(Error.validation_error(str(e)))
            
        except Exception as e:
            # Catch-all for database errors or unexpected issues
            return Result.failure(Error.system_error(str(e)))


@dataclass
class GetUserUseCase:

    uow: UnitOfWork

    async def execute(self, request: GetUserRequest) -> Result:
        """
        Execute the use case.

        Args:
        request: a GetUserRequest object containing the unique identifier of the user 

        Returns Result containing:
        - UserResponse if successful
        - Error information

        """
        try:
            params = request.to_execution_params()
            async with self.uow as uow:                
                user = await uow.user_repo.get(params["user_id"])      
            return Result.success(UserResponse.from_entity(user))

        except UserNotFoundError:
            return Result.failure(Error.not_found("User", str(params["user_id"])))



@dataclass
class UploadUserResumeUseCase:
    uow: UnitOfWork
    storage_port: FileStoragePort

    async def execute(self, user_id: str, file_bytes: bytes, content_type: str, original_filename: str) -> Result:
        try:
            # 1. Validation
            if content_type != "application/pdf" and not original_filename.lower().endswith(".pdf"):
                return Result.failure(Error.validation_error("Only PDF resumes are accepted."))

            # 2MB Limit check (optional but recommended for LLM context windows)
            if len(file_bytes) > 2 * 1024 * 1024:
                return Result.failure(Error.validation_error("Resume file size must be under 2MB."))

            async with self.uow as uow:
                # 2. Fetch User
                user = await uow.user_repo.get(UUID(user_id.strip()))
                if not user:
                    return Result.failure(Error.not_found("User", str(user_id)))

                # 3. Upload to Cloud (Machine Naming logic)
                # This automatically overwrites any existing resume for this user in the bucket
                storage_path = await self.storage_port.upload_file(
                    user_id=str(user.id),
                    file_bytes=file_bytes,
                    content_type=content_type,
                    extension="pdf"
                )

                # 4. Update Domain Entity (Human Naming logic)
                user.resume_path = storage_path
                user.resume_file_name = original_filename
                
                await uow.user_repo.save(user)
                await uow.commit()

            return Result.success({
                "message": "Resume uploaded successfully",
                "resume_path": storage_path,
                "resume_file_name": original_filename
            })

        except Exception as e:
            return Result.failure(Error.system_error(f"Failed to process resume: {str(e)}"))




@dataclass
class UpdateUserUseCase:

  uow: UnitOfWork


  async def execute(self, request: UpdateUserRequest) -> Result:
    try:
        async with self.uow as uow:
            params = request.to_execution_params() 
            user_id = params.pop("user_id")                  
            user = await uow.user_repo.update(user_id, params)
        return Result.success(UserResponse.from_entity(user))

    except UserNotFoundError:
        return Result.failure(Error.not_found("User", str(user_id)))
    except ValidationError as e:
        return Result.failure(Error.validation_error(str(e)))
    except BusinessRuleViolation as e:
        return Result.failure(Error.business_rule_violation(str(e)))



@dataclass
class DeleteUserUseCase:
  """Use case for deleting a user"""
  uow: UnitOfWork


  async def execute(self, request: GetUserRequest) -> Result:
    """
    Execute the use case.

      Args:
        request: a GetUserRequest object that contains the unique identifier of the user to delete

      Returns:
        Result containing DeletionResult if successful

    """
    
    try:
      async with self.uow as uow:
        params = request.to_execution_params()
        await uow.user_repo.delete(params["user_id"])
      return Result.success(DeletionOutcome(params["user_id"]))
    
    except UserNotFoundError:
      return Result.failure(Error.not_found("User", str(params["user_id"])))


@dataclass
class VerifyEmailUseCase:
    """Verifies an email using the token sent during registration."""
    uow: UnitOfWork
    token_provider: TokenProviderPort

    async def execute(self, token: str) -> Result:
        try:
            payload = self.token_provider.decode_token(token)

            if payload.get("purpose") != "email_verification":
                return Result.failure(Error.unauthorized("Invalid token type."))

            user_id = payload.get("sub")
            if not user_id:
                return Result.failure(Error.unauthorized("Invalid token payload."))

            async with self.uow as uow:
                auth_user = await uow.auth_repo.get_by_id(UUID(user_id))
                if not auth_user:
                    return Result.failure(Error.not_found("User", str(user_id)))

                if auth_user.is_verified:
                    return Result.success({"message": "Email already verified."})

                auth_user.is_verified = True
                await uow.auth_repo.save(auth_user)

            return Result.success({"message": "Email verified successfully."})

        except InvalidTokenException:
            return Result.failure(Error.unauthorized("Verification link is invalid or has expired."))
        except Exception:
            logger.exception("CRITICAL: Failed to verify email")
            return Result.failure(Error.system_error("Could not verify email."))


@dataclass
class ResendVerificationEmailUseCase:
    """Resends the verification email if the user hasn't verified yet."""
    uow: UnitOfWork
    token_provider: TokenProviderPort
    email_service: EmailServicePort

    async def execute(self, email: str) -> Result:
        try:
            async with self.uow:
                auth_user = await self.uow.auth_repo.get_by_email(email)

                # Anti-enumeration: same response whether or not email exists
                if not auth_user:
                    return Result.success({
                        "message": "If an account exists and is not yet verified, a new email has been sent."
                    })

                if auth_user.is_verified:
                    return Result.success({
                        "message": "If an account exists and is not yet verified, a new email has been sent."
                    })

                verification_token = self.token_provider.encode_token(
                    user_id=auth_user.user_id,
                    claims={"email": auth_user.email, "purpose": "email_verification"},
                    expires_delta=datetime.timedelta(hours=24),
                )

            # Send outside UoW
            try:
                await self.email_service.send_verification_email(
                    to_email=auth_user.email,
                    verification_token=verification_token,
                )
            except Exception:
                logger.exception("Failed to resend verification email to %s", email)

            return Result.success({
                "message": "If an account exists and is not yet verified, a new email has been sent."
            })

        except Exception:
            logger.exception("CRITICAL: Failed to resend verification email")
            return Result.failure(Error.system_error("Could not resend verification email."))







    

         
