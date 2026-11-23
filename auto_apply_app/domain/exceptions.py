# ==============================================================================
# FILE: auto_apply_app/domain/exceptions.py
#
# WHAT IT DOES:
#   Business-level exceptions (not found, validation, rule violation, bad token).
#   Repositories and entities raise them; use cases usually convert them to Result.
#
# USED BY:
#   - auto_apply_app/application/use_cases/user_use_cases.py: handles auth and user errors
#   - auto_apply_app/application/use_cases/job_offer_use_cases.py: handles job errors
#   - auto_apply_app/application/use_cases/announcement_use_cases.py: handles validation
#   - auto_apply_app/domain/entities/auth_user.py: raises rule violations
#   - auto_apply_app/domain/entities/job_offer.py: raises missing posting id
#   - auto_apply_app/infrastructures/api/dependencies/auth_deps.py: maps token errors to 401
#   - auto_apply_app/infrastructures/authentication/token_provider.py: raises token errors
#   - auto_apply_app/infrastructures/persistence/database/repositories/user_repo_db.py: raises not found
#   - ... and 6 more
#
# EXTERNAL DEPENDENCIES:
#   - None (standard library only)
# ==============================================================================
from uuid import UUID


class DomainError(Exception):
    """Base class for domain-specific errors."""

    pass


class JobNotFoundError(DomainError):
    """Raised when attempting to access a job that doesn't exist."""

    def __init__(self, job_id: UUID) -> None:
        self.job_id = job_id
        super().__init__(f"Task with id {job_id} not found")


class UserNotFoundError(DomainError):
    """Raised when attempting to access a user that doesn't exist."""

    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id
        super().__init__(f"User with id {user_id} not found")


class JobSearchNotFoundError(DomainError):
    """Raised when attempting to access a job search that doesn't exist."""

    def __init__(self, search_id: UUID) -> None:
        self.search_id = search_id
        super().__init__(f"Job search  with id {search_id} not found")

class JobPostingIdNotSetError(DomainError):
    """Raised when attempting to access a job posting id that is not set"""

    pass

class ValidationError(DomainError):
    """Raised when domain validation rules are violated."""

    pass


class BusinessRuleViolation(DomainError):
    """Raised when a business rule is violated."""

    pass


class InvalidTokenException(DomainError):    
    """Raised when token decoding fails or expires."""
    pass