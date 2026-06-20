from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from uuid import UUID
from typing import Optional

from auto_apply_app.domain.entities.entity import Entity
from auto_apply_app.domain.exceptions import BusinessRuleViolation


# Verification policy constants — kept on the entity so they live with the business rules.
MAX_VERIFICATION_ATTEMPTS = 5
VERIFICATION_CODE_TTL_MINUTES = 15


@dataclass
class AuthUser(Entity):

    email: str
    password_hash: str
    user_id: UUID  # The link to the Domain User Profile

    is_active: bool = True
    is_verified: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_login: Optional[datetime] = None

    # --- Email verification (code-based) ---
    verification_code_hash: Optional[str] = None
    verification_code_expires_at: Optional[datetime] = None
    verification_attempts: int = 0

    # --- Pending email change (verification-gated) ---
    pending_email: Optional[str] = None

    def change_password(self, new_password_hash: str) -> None:
        self.password_hash = new_password_hash
        self.updated_at = datetime.now(timezone.utc)

    def record_login(self) -> None:
        self.last_login = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Email verification
    # ------------------------------------------------------------------

    def set_verification_code(self, code_hash: str) -> None:
        """
        Issue a new verification code. Resets the attempts counter and the expiry.
        Called on registration and on every successful resend.
        """
        if self.is_verified:
            raise BusinessRuleViolation("Account is already verified.")

        self.verification_code_hash = code_hash
        self.verification_code_expires_at = (
            datetime.now(timezone.utc) + timedelta(minutes=VERIFICATION_CODE_TTL_MINUTES)
        )
        self.verification_attempts = 0
        self.updated_at = datetime.now(timezone.utc)

    def is_verification_code_expired(self) -> bool:
        if self.verification_code_expires_at is None:
            return True
        return datetime.now(timezone.utc) >= self.verification_code_expires_at

    def has_pending_verification(self) -> bool:
        return (
            not self.is_verified
            and self.verification_code_hash is not None
            and not self.is_verification_code_expired()
        )

    def register_failed_attempt(self) -> None:
        """Increment the failed-attempts counter. Caller decides what to do at the cap."""
        self.verification_attempts += 1
        self.updated_at = datetime.now(timezone.utc)

    def has_exceeded_attempts(self) -> bool:
        return self.verification_attempts >= MAX_VERIFICATION_ATTEMPTS

    def mark_verified(self) -> None:
        """Clear the code, mark as verified, record the login (verify == implicit login)."""
        self.is_verified = True
        self.verification_code_hash = None
        self.verification_code_expires_at = None
        self.verification_attempts = 0
        self.last_login = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)

    def clear_verification_code(self) -> None:
        """Invalidate the current code without verifying. Used when attempts are exceeded."""
        self.verification_code_hash = None
        self.verification_code_expires_at = None
        self.verification_attempts = 0
        self.updated_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Email change (verification-gated)
    #
    # Reuses the verification_code_* columns. Safe to share with registration
    # verification: an email change only happens on an already-verified account
    # (is_verified == True), while registration verification only runs while
    # is_verified == False — the two states are mutually exclusive.
    # ------------------------------------------------------------------

    def set_email_change_code(self, new_email: str, code_hash: str) -> None:
        """Stage a pending email change and issue a verification code for it."""
        self.pending_email = new_email
        self.verification_code_hash = code_hash
        self.verification_code_expires_at = (
            datetime.now(timezone.utc) + timedelta(minutes=VERIFICATION_CODE_TTL_MINUTES)
        )
        self.verification_attempts = 0
        self.updated_at = datetime.now(timezone.utc)

    def has_pending_email_change(self) -> bool:
        return (
            self.pending_email is not None
            and self.verification_code_hash is not None
            and not self.is_verification_code_expired()
        )

    def apply_email_change(self) -> str:
        """Promote pending_email to the live email and clear all code state. Returns the new email."""
        new_email = self.pending_email
        self.email = new_email
        self.pending_email = None
        self.verification_code_hash = None
        self.verification_code_expires_at = None
        self.verification_attempts = 0
        self.updated_at = datetime.now(timezone.utc)
        return new_email

    def clear_email_change(self) -> None:
        """Abort a pending email change (e.g. when attempts are exceeded)."""
        self.pending_email = None
        self.verification_code_hash = None
        self.verification_code_expires_at = None
        self.verification_attempts = 0
        self.updated_at = datetime.now(timezone.utc)