"""
This module contains the core error handling and result types for the application layer.
These types provide a consistent way to handle success and failure cases across all use cases.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Generic, TypeVar, Self



T = TypeVar('T')  # Success type


class ErrorCode(Enum):
    """Enumeration of possible error codes in the application layer."""

    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    BUSINESS_RULE_VIOLATION = "BUSINESS_RULE_VIOLATION"
    UNAUTHORIZED = "UNAUTHORIZED"
    CONFLICT = "CONFLICT"
    SYSTEMERROR = "SYSTEMERROR"
    TOO_MANY_REQUESTS = "TOO_MANY_REQUESTS"



class ErrorReason(Enum):
    """
    Specific, machine-readable failure reasons.
    Stable contract with the frontend — these strings are i18n keys.
    Never translate or rephrase these; the frontend maps them to EN/FR.
    """
    # Email verification
    INVALID_CODE = "invalid_code"
    EXPIRED_CODE = "expired_code"
    TOO_MANY_ATTEMPTS = "too_many_attempts"
    EMAIL_ALREADY_EXISTS = "email_already_exists"

    # Login
    INVALID_CREDENTIALS = "invalid_credentials"
    EMAIL_NOT_VERIFIED = "email_not_verified"

    # Rate limiting
    RATE_LIMITED = "rate_limited"

    # Password reset
    INVALID_TOKEN = "invalid_token"
    EXPIRED_TOKEN = "expired_token"

    # Account / password management
    INVALID_OLD_PASSWORD = "invalid_old_password"
    RESOURCE_NOT_FOUND = "resource_not_found"
    VALIDATION_ERROR = "validation_error"

    # Resume upload
    EMPTY_FILE = "empty_file"
    FILE_TOO_LARGE = "file_too_large"
    INVALID_FILE_TYPE = "invalid_file_type"
    INVALID_PDF = "invalid_pdf"
    BUSINESS_RULE_VIOLATION = "business_rule_violation"

    



# 🚨 Single source of truth for your default English fallbacks
DEFAULT_ERROR_MESSAGES = {
    ErrorCode.NOT_FOUND: "The requested resource could not be found.",
    ErrorCode.VALIDATION_ERROR: "The provided data is invalid.",
    ErrorCode.BUSINESS_RULE_VIOLATION: "A business rule was violated.",
    ErrorCode.UNAUTHORIZED: "You are not authorized to perform this action.",
    ErrorCode.CONFLICT: "A conflict occurred with the current state of the resource.",
    ErrorCode.SYSTEMERROR: "An unexpected system error occurred. Please try again later.",
    ErrorCode.TOO_MANY_REQUESTS: "You have exceeded the allowed number of requests. Please try again later."
}


@dataclass(frozen=True)
class Error:
    """
    Represents an error that occurred during use case execution.

    This class provides a standardized way to represent errors across the application layer,
    including the specific type of error (via ErrorCode) and any additional context.

    Attributes:
        code: The type of error that occurred
        message: A human-readable description of the error
        details: Optional additional context about the error
    """

    code: ErrorCode
    message: str    
    details: Optional[dict[str, Any]] = None
    reason: Optional[ErrorReason] = None

    @classmethod
    def conflict(cls, message: Optional[str] = None, reason: Optional[ErrorReason] = None) -> Self:
        return cls(
            code=ErrorCode.CONFLICT,
            message=message or DEFAULT_ERROR_MESSAGES[ErrorCode.CONFLICT],
            reason=reason
        )

    @classmethod
    def not_found(cls, entity: Optional[str] = None, entity_id: Optional[str] = None,
                  reason: Optional[ErrorReason] = None) -> Self:
        """Create a NOT_FOUND error for a specific entity."""
        if entity and entity_id:
            msg = f"{entity} with id {entity_id} not found."
        elif entity:
            msg = f"{entity} not found."
        else:
            msg = DEFAULT_ERROR_MESSAGES[ErrorCode.NOT_FOUND]

        return cls(
            code=ErrorCode.NOT_FOUND,
            message=msg,
            reason=reason,
        )
    @classmethod
    def validation_error(cls, message: Optional[str] = None,
                         reason: Optional[ErrorReason] = None) -> Self:
        return cls(
            code=ErrorCode.VALIDATION_ERROR,
            message=message or DEFAULT_ERROR_MESSAGES[ErrorCode.VALIDATION_ERROR],
            reason=reason,
        )

    
    @classmethod
    def business_rule_violation(cls, message: Optional[str] = None,
                               reason: Optional[ErrorReason] = None) -> Self:
        return cls(
            code=ErrorCode.BUSINESS_RULE_VIOLATION,
            message=message or DEFAULT_ERROR_MESSAGES[ErrorCode.BUSINESS_RULE_VIOLATION],
            reason=reason,
        )
    

    @classmethod
    def system_error(cls, message: Optional[str] = None) -> Self:
        """Create a SYSTEMERROR. Uses a generic default message if none is provided."""
        return cls(
            code=ErrorCode.SYSTEMERROR, 
            message=message or DEFAULT_ERROR_MESSAGES[ErrorCode.SYSTEMERROR]
        )
    
    @classmethod
    def unauthorized(cls, message: Optional[str] = None, reason: Optional[ErrorReason] = None) -> Self:
        """Create an UNAUTHORIZED error."""
        return cls(
            code=ErrorCode.UNAUTHORIZED, 
            message=message or DEFAULT_ERROR_MESSAGES[ErrorCode.UNAUTHORIZED],
            reason=reason
        )

    @classmethod
    def too_many_requests(
        cls,
        message: Optional[str] = None,
        reason: Optional[ErrorReason] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> Self:
        return cls(
            code=ErrorCode.TOO_MANY_REQUESTS,
            message=message or DEFAULT_ERROR_MESSAGES[ErrorCode.TOO_MANY_REQUESTS],
            reason=reason,
            details=details,
        )


@dataclass(frozen=True)
class Result(Generic[T]):
    """
    Represents the outcome of a use case execution as an Either type.

    This class encapsulates the result of an operation, which can either be a success
    containing a value of type T, or a failure containing an Error. It enforces that
    only one of these states can exist at a time, providing a clear and type-safe way
    to handle operation results.

    Attributes:
        _value: The success value of the operation, if successful.
        _error: The error information, if the operation failed.

    Methods:
        is_success: Returns True if the result is a success, False if it is a failure.
        value: Returns the success value if the result is a success, raises ValueError otherwise.
        error: Returns the error if the result is a failure, raises ValueError otherwise.
        success: Class method to create a successful result.
        failure: Class method to create a failed result.
    """

    _value: Optional[T] = None
    _error: Optional[Error] = None

    def __post_init__(self):
        if (self._value is None and self._error is None) or \
           (self._value is not None and self._error is not None):
            raise ValueError("Either value or error must be provided, but not both")

    @property
    def is_success(self) -> bool:
        """Check if the result represents a successful operation."""
        return self._value is not None

    @property
    def value(self) -> T:
        """Get the success value. Raises ValueError if result is an error."""
        if self._value is None:
            raise ValueError("Cannot access value on error result")
        return self._value

    @property
    def error(self) -> Error:
        """Get the error value. Raises ValueError if result is successful."""
        if self._error is None:
            raise ValueError("Cannot access error on success result")
        return self._error

    @classmethod
    def success(cls, value: T) -> 'Result[T]':
        """Create a successful result with the given value."""
        return cls(_value=value)

    @classmethod
    def failure(cls, error: Error) -> 'Result[T]':
        """Create a failed result with the given error."""
        return cls(_error=error)