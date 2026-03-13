from fastapi import HTTPException, status

from auto_apply_app.interfaces.viewmodels.base import OperationResult


# Helper to translate OperationResult to FastAPI Response
def handle_result(result: OperationResult):
    if result.is_success:
        return result.success
    
    # Mapping internal codes to HTTP Statuses
    status_mapping = {
        "VALIDATION_ERROR": status.HTTP_400_BAD_REQUEST,
        "UNAUTHORIZED": status.HTTP_401_UNAUTHORIZED,
        "NOT_FOUND": status.HTTP_404_NOT_FOUND,
        "CONFLICT": status.HTTP_409_CONFLICT,
    }
    
    http_status = status_mapping.get(result.error.code, status.HTTP_500_INTERNAL_SERVER_ERROR)
    raise HTTPException(status_code=http_status, detail=result.error.message)