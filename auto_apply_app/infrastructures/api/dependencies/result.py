# ==============================================================================
# FILE: auto_apply_app/infrastructures/api/dependencies/result.py
#
# WHAT IT DOES:
#   Converts a controller's OperationResult into an HTTP outcome: it returns the
#   success value, or raises an HTTPException whose status comes from the error code.
#   Routers call it as their last step.
#
# USED BY:
#   - auto_apply_app/infrastructures/api/routers/admin.py: result-to-HTTP conversion
#   - auto_apply_app/infrastructures/api/routers/agent.py: result-to-HTTP conversion
#   - auto_apply_app/infrastructures/api/routers/agent_state.py: result-to-HTTP conversion
#   - auto_apply_app/infrastructures/api/routers/announcement.py: result-to-HTTP conversion
#   - auto_apply_app/infrastructures/api/routers/application.py: result-to-HTTP conversion
#   - auto_apply_app/infrastructures/api/routers/free_search.py: result-to-HTTP conversion
#   - auto_apply_app/infrastructures/api/routers/preferences.py: result-to-HTTP conversion
#   - auto_apply_app/infrastructures/api/routers/subscription.py: result-to-HTTP conversion
#   - ... and 1 more
#
# EXTERNAL DEPENDENCIES:
#   - fastapi: HTTPException and status codes
# ==============================================================================
# auto_apply_app/infrastructures/api/dependencies/result.py
from fastapi import HTTPException, status

from auto_apply_app.interfaces.viewmodels.base import OperationResult


def handle_result(result: OperationResult):
    if result.is_success:
        return result.success

    status_mapping = {
        "VALIDATION_ERROR": status.HTTP_400_BAD_REQUEST,
        "UNAUTHORIZED": status.HTTP_401_UNAUTHORIZED,
        "NOT_FOUND": status.HTTP_404_NOT_FOUND,
        "CONFLICT": status.HTTP_409_CONFLICT,
        "TOO_MANY_REQUESTS": status.HTTP_429_TOO_MANY_REQUESTS,
        "BUSINESS_RULE_VIOLATION": status.HTTP_422_UNPROCESSABLE_ENTITY,
    }

    err = result.error
    # Any error code not listed above becomes a 500.
    http_status = status_mapping.get(err.code, status.HTTP_500_INTERNAL_SERVER_ERROR)

    # Structured body — `reason` is the stable key the frontend maps to EN/FR.
    # `message` is developer-facing (logs / debugging), not shown to users.
    detail = {
        "reason": err.reason,        # e.g. "expired_code" — the contract
        "code": err.code,            # category, e.g. "UNAUTHORIZED"
        "message": err.message,      # dev-facing only
    }
    if err.details:
        detail["details"] = err.details   # e.g. {"retry_after": 42}

    raise HTTPException(status_code=http_status, detail=detail)