from fastapi import APIRouter, Depends, HTTPException, status
from typing import Annotated

from auto_apply_app.interfaces.viewmodels.base import OperationResult
from auto_apply_app.infrastructures.configuration.container import Application
from auto_apply_app.interfaces.controllers.auth_controllers import AuthController
from auto_apply_app.interfaces.controllers.user_controllers import UserController
from auto_apply_app.interfaces.viewmodels.user_vm import UserViewModel
from auto_apply_app.infrastructures.api.dependencies.container_dep import get_container
from auto_apply_app.infrastructures.api.dependencies.auth_deps import CurrentUserId, authorize_user_access
from auto_apply_app.infrastructures.api.schema.user_schema import UserUpdateSchema

router = APIRouter()

# This dependency function acts as the "bridge" to your assembly line
def get_auth_controller(
    app_container: Annotated[Application, Depends(get_container)]
) -> AuthController:    
    return app_container.auth_controller


def get_user_controller(
    app_container: Annotated[Application, Depends(get_container)]
) -> UserController:     
    return app_container.user_controller

UserControllerDep = Annotated[UserController, Depends(get_user_controller)]

AuthControllerDep = Annotated[AuthController, Depends(get_auth_controller)]

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






@router.get(
    "/{user_id}",
    response_model=UserViewModel,
    summary="Get user profile by ID",
    description="Retrieve a specific user's profile (must be own profile)"
)
async def get_user_profile(
    user_id: str,
    current_user_id: CurrentUserId,
    user_controller: UserControllerDep
):
    """
    Get a user profile by ID.
    
    **Authorization**: Users can only retrieve their own profile.
    
    Requires valid JWT token in Authorization header.
    
    Raises:
        403: If attempting to access another user's profile
        404: If user not found
    """
    # ✅ AUTHORIZATION CHECK: Ensure user can only access their own data
    authorize_user_access(user_id, current_user_id)
    
    result = await user_controller.handle_get(user_id)
    return handle_result(result)





@router.patch(
    "update/{user_id}",
    response_model=UserViewModel,
    summary="Update user profile by ID",
    description="Update a specific user's profile (must be own profile)"
)
async def update_user_profile(
    user_id: str,
    data: UserUpdateSchema,
    current_user_id: CurrentUserId,
    user_controller: UserControllerDep
):
    """
    Update a user's profile by ID.
    
    **Authorization**: Users can only update their own profile.
    
    Requires valid JWT token in Authorization header.
    Only provided fields will be updated (partial update).
    
    Raises:
        403: If attempting to update another user's profile
        404: If user not found
    """
    # ✅ AUTHORIZATION CHECK: Ensure user can only update their own data
    authorize_user_access(user_id, current_user_id)
    
    result = await user_controller.handle_update(
        user_id=user_id,
        fname=data.firstname,
        lname=data.lastname,
        email=data.email,
        resume_dir=data.resume_dir,
        phone_number=data.phone_number
    )
    return handle_result(result)





@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete user account by ID",
    description="Permanently delete a specific user's account (must be own account)"
)
async def delete_user_account(
    user_id: str,
    current_user_id: CurrentUserId,
    user_controller: UserControllerDep
):
    """
    Permanently delete a user account by ID.
    
    **Authorization**: Users can only delete their own account.
    
    Requires valid JWT token in Authorization header.
    
    **Warning**: This action cannot be undone.
    
    Raises:
        403: If attempting to delete another user's account
        404: If user not found
    """
    # ✅ AUTHORIZATION CHECK: Ensure user can only delete their own account
    authorize_user_access(user_id, current_user_id)
    
    result = await user_controller.handle_delete(user_id)
    handle_result(result)
    return None