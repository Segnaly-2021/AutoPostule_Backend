from dataclasses import dataclass
from uuid import UUID
import datetime
import logging
import secrets
import re

from auto_apply_app.application.dtos.operations import DeletionOutcome
from auto_apply_app.application.common.result import Error, ErrorReason, Result
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
    VerifyCodeRequest,
    ResendVerificationRequest,
    ChangePasswordRequest,
    ForgotPasswordRequest, 
    ResetPasswordRequest
)

from auto_apply_app.application.service_ports.email_service_port import EmailServicePort
from auto_apply_app.application.repositories.unit_of_work import UnitOfWork
from auto_apply_app.application.service_ports.password_service_port import PasswordServicePort
from auto_apply_app.application.service_ports.rate_limiter_port import RateLimiterPort
from auto_apply_app.application.service_ports.file_storage_port import FileStoragePort
from auto_apply_app.application.dtos.auth_user_dtos import LoginRequest
from auto_apply_app.application.service_ports.token_provider_port import TokenProviderPort
from auto_apply_app.domain.entities.auth_user import AuthUser
from auto_apply_app.domain.entities.user_preferences import UserPreferences
from auto_apply_app.domain.entities.user_subscription import UserSubscription

from auto_apply_app.application.repositories.token_blacklist import TokenBlacklistRepository


logger = logging.getLogger(__name__)


# Helper function for resume upload use case
# Magic bytes for a real PDF: %PDF-1.x
_PDF_MAGIC = b"%PDF-"
_MAX_RESUME_SIZE = 5 * 1024 * 1024   # 5MB hard cap on backend
_ALLOWED_CONTENT_TYPES = {"application/pdf"}
_FILENAME_MAX_LEN = 100


def _sanitize_filename(filename: str) -> str:
    """
    Strip any path components and disallow weird characters.
    Returns a safe filename like 'My_Resume.pdf' or 'resume.pdf' as fallback.
    """
    if not filename:
        return "resume.pdf"

    # Take only the basename — drops '../', '/', '\', etc.
    base = filename.replace("\\", "/").split("/")[-1]

    # Allow only alphanumerics, dots, dashes, underscores, spaces. Replace the rest.
    base = re.sub(r"[^A-Za-z0-9._\- ]", "_", base)

    # Collapse repeated underscores
    base = re.sub(r"_{2,}", "_", base).strip()

    # Force .pdf extension
    if not base.lower().endswith(".pdf"):
        base = f"{base}.pdf"

    # Cap length
    if len(base) > _FILENAME_MAX_LEN:
        base = base[-_FILENAME_MAX_LEN:]

    return base or "resume.pdf"

# Resend cooldown: 1 request per 60s per email (independent of backend-internal Resend API).
RESEND_COOLDOWN_SECONDS = 60
 
 
def _generate_verification_code() -> str:
    """Generate a 6-digit verification code, zero-padded. Uses `secrets` for CSPRNG."""
    return f"{secrets.randbelow(1_000_000):06d}"
 
 
# ============================================================================
# REGISTER — modified to issue a code instead of a JWT link
# ============================================================================
 
@dataclass
class RegisterUserUseCase:
    uow: UnitOfWork
    password_service: PasswordServicePort
    email_service: EmailServicePort

    async def execute(self, request: RegisterUserRequest) -> Result:
        try:
            params = request.to_execution_params()

            # Generate code BEFORE the transaction so we can hash + persist atomically.
            raw_code = _generate_verification_code()
            code_hash = self.password_service.get_password_hash(raw_code)

            async with self.uow:
                existing_auth = await self.uow.auth_repo.get_by_email(params["email"])
                if existing_auth:
                    logger.info("Registration rejected: email already exists %s", params["email"])
                    return Result.failure(Error.conflict(
                        message="Registration: email already exists",
                        reason=ErrorReason.EMAIL_ALREADY_EXISTS,
                    ))

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
                # Issue the verification code on the entity (sets hash, expiry, attempts=0)
                auth_user.set_verification_code(code_hash)

                sub_user = UserSubscription(user_id=user_id, email=params["email"])
                user_prefs = UserPreferences(user_id=user.id)

                await self.uow.user_repo.save(user)
                await self.uow.auth_repo.save(auth_user)
                await self.uow.subscription_repo.save(sub_user)
                await self.uow.user_pref_repo.save(user_prefs)

            # Send code outside UoW — email failure must not roll back registration.
            try:
                await self.email_service.send_verification_email(
                    to_email=params["email"],
                    code=raw_code,
                )
            except Exception:
                logger.exception("Registration: failed to send verification email to %s", params["email"])

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
                    logger.info("Login failed: this email does not exist - %s", params["email"])
                    return Result.failure(Error.unauthorized(
                        message="Login failed: email does not exist",
                        reason=ErrorReason.INVALID_CREDENTIALS,
                    ))

                if not self.password_service.verify(params["password"], auth_user.password_hash):
                    logger.info("Login failed: password not correct - %s", params["email"])
                    return Result.failure(Error.unauthorized(
                        message="Login failed: password not correct",
                        reason=ErrorReason.INVALID_CREDENTIALS,
                    ))

                if not auth_user.is_verified:
                    logger.info("Login failed: account not verified yet - %s", params["email"])
                    return Result.failure(Error.unauthorized(
                        message="Login failed: account not verified yet",
                        reason=ErrorReason.EMAIL_NOT_VERIFIED,
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
            logger.exception("CRITICAL: Login failed: something went wrong")
            return Result.failure(Error.system_error(message="Login failed: unexpected error"))

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

                # SECURITY NOTE: Do not reveal whether the email exists — anti-enumeration.
                if not auth_user:
                    logger.info("Password reset requested for unknown email (silently ignored)")
                    return Result.success({"message": "If an account exists, a reset link has been sent."})

                # 2. Generate a 15-minute reset token
                reset_token = self.token_provider.encode_token(
                    user_id=auth_user.user_id,
                    claims={"email": auth_user.email, "purpose": "password_reset"},
                    expires_delta=datetime.timedelta(minutes=15)
                )

                # 3. Send the email
                try:
                    await self.email_service.send_password_reset_email(
                        to_email=auth_user.email,
                        reset_token=reset_token,
                    )
                except Exception:
                    logger.exception("Password reset: failed to send reset email to %s", auth_user.email)

            logger.info("Password reset email dispatched")
            return Result.success({"message": "If an account exists, a reset link has been sent."})

        except Exception:
            logger.exception("CRITICAL: Password reset request crashed")
            return Result.failure(Error.system_error(
                message="Password reset request: unexpected error",
            ))

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
                logger.warning("Password reset failed: wrong token purpose")
                return Result.failure(Error.unauthorized(
                    message="Password reset: token purpose is not 'password_reset'",
                    reason=ErrorReason.INVALID_TOKEN,
                ))

            user_id = payload.get("sub")
            if not user_id:
                logger.warning("Password reset failed: token payload missing subject")
                return Result.failure(Error.unauthorized(
                    message="Password reset: token payload missing 'sub'",
                    reason=ErrorReason.INVALID_TOKEN,
                ))

            async with self.uow as uow:
                # 2. Fetch User
                auth_user = await uow.auth_repo.get_by_id(UUID(user_id))
                if not auth_user:
                    logger.warning("Password reset failed: no user for valid token sub=%s", user_id)
                    return Result.failure(Error.unauthorized(
                        message="Password reset: no user matches token subject",
                        reason=ErrorReason.INVALID_TOKEN,
                    ))

                # 3. Hash New Password
                new_hashed_password = self.password_service.get_password_hash(request.new_password)

                # 4. Update Domain Entity
                auth_user.change_password(new_hashed_password)

                # 5. Persist
                await uow.auth_repo.save(auth_user)

            return Result.success({"message": "Password reset successfully."})

        except InvalidTokenException:
            logger.info("Password reset failed: token invalid or expired")
            return Result.failure(Error.unauthorized(
                message="Password reset: token invalid or expired",
                reason=ErrorReason.INVALID_TOKEN,
            ))
        except Exception:
            logger.exception("CRITICAL: Password reset confirmation crashed")
            return Result.failure(Error.system_error(
                message="Password reset: unexpected error",
            ))


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
                await self.token_blacklist_repo.blacklist_token(token_id=jti, ttl_seconds=ttl)
                
        except InvalidTokenException:
            # If the token is already invalid (malformed, expired, fake), 
            # logging out is technically a "success" (the user session is dead).
            # We fail silently here.
            pass
        except Exception:
            logger.exception("Failed during logout token invalidation.")
            # Still returning nothing as we fail silently for logouts

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
                    # Authenticated user not found in auth table — should never happen.
                    logger.error(
                        "Change password failed: authenticated user not found user_id=%s",
                        params["user_id"],
                    )
                    return Result.failure(Error.not_found(
                        entity="User",
                        entity_id=str(params["user_id"]),
                        reason=ErrorReason.RESOURCE_NOT_FOUND,
                    ))

                # 2. Security Check: Verify OLD Password
                if not self.password_service.verify(params["old_password"], auth_user.password_hash):
                    logger.info(
                        "Change password failed: incorrect old password user_id=%s",
                        params["user_id"],
                    )
                    return Result.failure(Error.unauthorized(
                        message="Change password: old password is incorrect",
                        reason=ErrorReason.INVALID_OLD_PASSWORD,
                    ))

                # 3. Hash the NEW Password
                new_hashed_password = self.password_service.get_password_hash(params["new_password"])

                # 4. Domain Logic: Update Entity
                auth_user.change_password(new_hashed_password)

                # 5. Persist Changes
                await uow.auth_repo.save(auth_user)

            return Result.success("Password changed successfully")

        except ValidationError as e:
            logger.info("Change password failed: validation error - %s", e)
            return Result.failure(Error.validation_error(
                message=str(e),
                reason=ErrorReason.VALIDATION_ERROR,
            ))
        except Exception:
            logger.exception("CRITICAL: Change password crashed for user_id=%s", params.get("user_id"))
            return Result.failure(Error.system_error(
                message="Change password: unexpected error",
            ))

@dataclass
class GetUserUseCase:

    uow: UnitOfWork

    async def execute(self, request: GetUserRequest) -> Result:
        try:
            params = request.to_execution_params()
            async with self.uow as uow:
                user = await uow.user_repo.get(params["user_id"])
            return Result.success(UserResponse.from_entity(user))

        except UserNotFoundError:
            logger.error("Get user failed: user not found user_id=%s", params["user_id"])
            return Result.failure(Error.not_found(
                entity="User",
                entity_id=str(params["user_id"]),
                reason=ErrorReason.RESOURCE_NOT_FOUND,
            ))
        except Exception:
            logger.exception("Get user crashed for user_id=%s", params.get("user_id"))
            return Result.failure(Error.system_error(
                message="Get user: unexpected error",
            ))

@dataclass
class UploadUserResumeUseCase:
    uow: UnitOfWork
    storage_port: FileStoragePort

    async def execute(
        self,
        user_id: str,
        file_bytes: bytes,
        content_type: str,
        original_filename: str,
    ) -> Result:
        try:
            # 1. Size cap (5MB)
            if len(file_bytes) == 0:
                return Result.failure(Error.validation_error("Empty file."))
            if len(file_bytes) > _MAX_RESUME_SIZE:
                return Result.failure(
                    Error.validation_error("Resume file size must be under 5MB.")
                )

            # 2. Content-Type whitelist
            if content_type not in _ALLOWED_CONTENT_TYPES:
                return Result.failure(
                    Error.validation_error("Only PDF resumes are accepted.")
                )

            # 3. Magic-byte check — confirms it's actually a PDF, not a renamed file
            if not file_bytes.startswith(_PDF_MAGIC):
                logger.warning(
                    "Resume upload rejected: invalid PDF magic bytes for user_id=%s", user_id
                )
                return Result.failure(
                    Error.validation_error("File is not a valid PDF.")
                )

            # 4. Filename sanitization
            safe_filename = _sanitize_filename(original_filename)

            async with self.uow as uow:
                user = await uow.user_repo.get(UUID(user_id.strip()))
                if not user:
                    return Result.failure(Error.not_found("User", str(user_id)))

                storage_path = await self.storage_port.upload_file(
                    user_id=str(user.id),
                    file_bytes=file_bytes,
                    content_type="application/pdf",   # force, ignore client-supplied
                    extension="pdf",
                )

                user.resume_path = storage_path
                user.resume_file_name = safe_filename
                await uow.user_repo.save(user)
                await uow.commit()

            return Result.success({
                "message": "Resume uploaded successfully",
                "resume_path": storage_path,
                "resume_file_name": safe_filename,
            })

        except Exception:
            logger.exception("Failed to process resume upload for user_id=%s", user_id)
            return Result.failure(Error.system_error("Failed to process resume."))



@dataclass
class UpdateUserUseCase:

    uow: UnitOfWork

    async def execute(self, request: UpdateUserRequest) -> Result:
        user_id = None
        try:
            async with self.uow as uow:
                params = request.to_execution_params()
                user_id = params.pop("user_id")
                user = await uow.user_repo.update(user_id, params)
            return Result.success(UserResponse.from_entity(user))

        except UserNotFoundError:
            logger.error("Update user failed: user not found user_id=%s", user_id)
            return Result.failure(Error.not_found(
                entity="User",
                entity_id=str(user_id),
                reason=ErrorReason.RESOURCE_NOT_FOUND,
            ))
        except ValidationError as e:
            logger.info("Update user failed: validation error user_id=%s - %s", user_id, e)
            return Result.failure(Error.validation_error(
                message=str(e),
                reason=ErrorReason.VALIDATION_ERROR,
            ))
        except BusinessRuleViolation as e:
            logger.info("Update user failed: business rule user_id=%s - %s", user_id, e)
            return Result.failure(Error.business_rule_violation(
                message=str(e),
                reason=ErrorReason.BUSINESS_RULE_VIOLATION,
            ))
        except Exception:
            logger.exception("Update user crashed for user_id=%s", user_id)
            return Result.failure(Error.system_error(
                message="Update user: unexpected error",
            ))


@dataclass
class DeleteUserUseCase:
    """Use case for deleting a user"""
    uow: UnitOfWork
    storage_port: FileStoragePort  # ← inject the storage port

    async def execute(self, request: GetUserRequest) -> Result:
        params = {}
        try:
            async with self.uow as uow:
                params = request.to_execution_params()
                user_id = params["user_id"]

                # Capture the resume path BEFORE deleting the row
                user = await uow.user_repo.get(user_id)
                if not user:
                    raise UserNotFoundError()
                resume_path = user.resume_path

                await uow.user_repo.delete(user_id)

            # DB delete committed successfully — now remove the physical file.
            if resume_path:
                try:
                    await self.storage_port.delete_file(resume_path)
                except Exception:
                    # Don't fail the whole deletion if file cleanup fails;
                    # log it so it can be reconciled.
                    logger.exception(
                        "User row deleted but resume file cleanup failed for user_id=%s path=%s",
                        user_id, resume_path,
                    )

            return Result.success(DeletionOutcome(params["user_id"]))

        except UserNotFoundError:
            logger.error("Delete user failed: user not found user_id=%s", params.get("user_id"))
            return Result.failure(Error.not_found(
                entity="User",
                entity_id=str(params.get("user_id")),
                reason=ErrorReason.RESOURCE_NOT_FOUND,
            ))
        except Exception:
            logger.exception("Delete user crashed for user_id=%s", params.get("user_id"))
            return Result.failure(Error.system_error(
                message="Delete user: unexpected error",
            ))



@dataclass
class VerifyCodeUseCase:
    """
    Verifies the 6-digit code and, on success, logs the user in by returning a JWT.
    Replaces VerifyEmailUseCase.
    """
    uow: UnitOfWork
    password_service: PasswordServicePort
    token_provider: TokenProviderPort
 
    async def execute(self, request: VerifyCodeRequest) -> Result:
        try:
            params = request.to_execution_params()
            email = params["email"]
            code = params["code"]

            async with self.uow:
                auth_user = await self.uow.auth_repo.get_by_email(email)

                if not auth_user:
                    logger.info("Email verification failed: no account for %s", email)
                    return Result.failure(Error.unauthorized(
                        message="Email verification: no account for given email",
                        reason=ErrorReason.INVALID_CODE,
                    ))

                if auth_user.is_verified:
                    logger.info("Email verification failed: already verified %s", email)
                    return Result.failure(Error.unauthorized(
                        message="Email verification: account already verified",
                        reason=ErrorReason.INVALID_CODE,
                    ))

                if not auth_user.has_pending_verification():
                    logger.info("Email verification failed: expired/no pending code for %s", email)
                    return Result.failure(Error.unauthorized(
                        message="Email verification: no pending or expired code",
                        reason=ErrorReason.EXPIRED_CODE,
                    ))

                if auth_user.has_exceeded_attempts():
                    auth_user.clear_verification_code()
                    await self.uow.auth_repo.save(auth_user)
                    logger.warning("Email verification failed: too many attempts %s", email)
                    return Result.failure(Error.unauthorized(
                        message="Email verification: exceeded attempts",
                        reason=ErrorReason.TOO_MANY_ATTEMPTS,
                    ))

                if not self.password_service.verify(code, auth_user.verification_code_hash):
                    auth_user.register_failed_attempt()
                    if auth_user.has_exceeded_attempts():
                        auth_user.clear_verification_code()
                        await self.uow.auth_repo.save(auth_user)
                        logger.warning("Email verification failed: too many attempts (final) %s", email)
                        return Result.failure(Error.unauthorized(
                            message="Email verification: exceeded attempts after wrong code",
                            reason=ErrorReason.TOO_MANY_ATTEMPTS,
                        ))
                    await self.uow.auth_repo.save(auth_user)
                    logger.info("Email verification failed: wrong code %s", email)
                    return Result.failure(Error.unauthorized(
                        message="Email verification: code mismatch",
                        reason=ErrorReason.INVALID_CODE,
                    ))

                auth_user.mark_verified()
                await self.uow.auth_repo.save(auth_user)
                token = self.token_provider.encode_token(
                    user_id=auth_user.user_id,
                    claims={"email": auth_user.email},
                )

            return Result.success(LoginResponse(access_token=token, token_type="Bearer"))

        except BusinessRuleViolation as e:
            return Result.failure(Error.business_rule_violation(str(e)))
        except Exception:
            logger.exception("CRITICAL: Failed to verify code")
            return Result.failure(Error.system_error("Could not verify code."))


@dataclass
class ResendVerificationEmailUseCase:
    """
    Resends a 6-digit verification code, rate-limited via Redis (1/60s per email).
    Anti-enumeration: same response whether or not the email exists / is verified.
    """
    uow: UnitOfWork
    password_service: PasswordServicePort
    email_service: EmailServicePort
    rate_limiter: RateLimiterPort

    async def execute(self, request: ResendVerificationRequest) -> Result:
        try:
            params = request.to_execution_params()
            normalized_email = params["email"]
            generic_response = {
                "message": "If an account exists and is not yet verified, a new code has been sent."
            }

            # 1) Rate limit FIRST — prevents email enumeration via timing too.
            rate_key = f"resend_verification:{normalized_email}"
            allowed, retry_after = await self.rate_limiter.try_acquire(
                key=rate_key,
                window_seconds=RESEND_COOLDOWN_SECONDS,
            )
            if not allowed:
                logger.info(
                    "Resend verification blocked: rate limit hit for %s (retry_after=%ss)",
                    normalized_email, retry_after,
                )
                return Result.failure(
                    Error.too_many_requests(
                        message="Resend verification: rate limit cooldown active",
                        reason=ErrorReason.RATE_LIMITED,
                        details={"retry_after": retry_after},
                    )
                )

            # 2) Generate code + hash outside the transaction.
            raw_code = _generate_verification_code()
            code_hash = self.password_service.get_password_hash(raw_code)

            should_send = False
            async with self.uow:
                auth_user = await self.uow.auth_repo.get_by_email(normalized_email)

                if auth_user and not auth_user.is_verified:
                    auth_user.set_verification_code(code_hash)
                    await self.uow.auth_repo.save(auth_user)
                    should_send = True

            if should_send:
                try:
                    await self.email_service.send_verification_email(
                        to_email=normalized_email,
                        code=raw_code,
                    )
                except Exception:
                    logger.exception(
                        "Resend verification: failed to send email to %s", normalized_email
                    )

            return Result.success(generic_response)

        except Exception:
            logger.exception("CRITICAL: Resend verification failed unexpectedly")
            return Result.failure(Error.system_error(
                message="Resend verification: unexpected error",
            ))