from fastapi import APIRouter, UploadFile, File, Depends, status
from typing import Annotated

from auto_apply_app.infrastructures.api.dependencies.result import handle_result
from auto_apply_app.infrastructures.configuration.container import Application
from auto_apply_app.interfaces.controllers.auth_controllers import AuthController
from auto_apply_app.interfaces.controllers.user_controllers import UserController
from auto_apply_app.interfaces.viewmodels.user_vm import UserViewModel, LoginViewModel, UploadResumeViewModel 
from auto_apply_app.infrastructures.api.dependencies.container_dep import get_container
from auto_apply_app.infrastructures.api.dependencies.auth_deps import CurrentUserId, CurrentToken
from auto_apply_app.infrastructures.api.schema.user_schema import (
    ChangePasswordSchema,
    LoginSchema,
    RegisterSchema,
    UserUpdateSchema
)

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


# 2. Updated Routes
@router.post(
        "/register", 
        response_model=UserViewModel, 
        status_code=status.HTTP_201_CREATED
)
async def register(
    data: RegisterSchema,  # FastAPI automatically parses the JSON body
    auth_controller: AuthControllerDep
):
    result = await auth_controller.handle_register(
        email=data.email, 
        password=data.password, 
        firstname=data.firstname, 
        lastname=data.lastname
    )
    return handle_result(result)



@router.post(
    "/login",
    response_model=LoginViewModel, 
    status_code=status.HTTP_200_OK,
    summary="Login user",
    description="Authenticate user and receive JWT access token"
)
async def login(
    data: LoginSchema,
    auth_controller: AuthControllerDep
):
    """
    Authenticate user and return JWT access token.
    
    - **email**: User's email address
    - **password**: User's password
    
    Returns:
    - **access_token**: JWT token for authentication
    - **token_type**: Always "bearer"
    
    Use the access_token in subsequent requests:
    ```
    Authorization: Bearer <access_token>
    ```
    """
    result = await auth_controller.handle_login(
        email=data.email,
        password=data.password
    )
    return handle_result(result)




@router.post(
    "/change-password",
    status_code=status.HTTP_200_OK,
    summary="Change password",
    description="Change the authenticated user's password"
)
async def change_password(
    data: ChangePasswordSchema,
    current_user_id: CurrentUserId,  # ✅ Validates token
    auth_controller: AuthControllerDep
):
    """
    Change the authenticated user's password.
    
    Requires valid JWT token in Authorization header.
    
    - **old_password**: Current password for verification
    - **new_password**: New password (minimum 8 characters)
    
    **Security Note**: The user_id is extracted from the token, not the request body,
    preventing users from changing other users' passwords.
    """
    # ✅ SECURITY: Use user_id from token, ignore data.user_id
    result = await auth_controller.handle_change_password(
        user_id=current_user_id,  # From token, not request body
        old_password=data.old_password,
        new_password=data.new_password
    )
    return handle_result(result)



@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Logout user",
    description="Invalidate the current JWT token by adding it to blacklist"
)
async def logout(
    token: CurrentToken,  # ✅ Gets the raw token string
    auth_controller: AuthControllerDep
):
    """
    Logout the authenticated user.
    
    Requires valid JWT token in Authorization header.
    The token will be blacklisted and cannot be used again until expiration.
    
    **Security Note**: This extracts the token from the Authorization header
    automatically, so the user cannot logout someone else's session.
    """
    result = await auth_controller.handle_logout(token=token)
    handle_result(result)
    return None



@router.get(
    "/get/me",
    response_model=UserViewModel,
    summary="Get current user profile",
    description="Retrieve the authenticated user's profile information"
)
async def get_my_profile(
    current_user_id: CurrentUserId,
    user_controller: UserControllerDep
):
    """
    Get the authenticated user's own profile.
    
    Requires valid JWT token in Authorization header.
    The user_id is automatically extracted from the token.
    
    Returns complete user profile information.
    """
    result = await user_controller.handle_get(current_user_id)
    return handle_result(result)



@router.post(
    "/update/me/resume",
    status_code=status.HTTP_200_OK,
    response_model=UploadResumeViewModel, # Optional: strictly document the response
    summary="Upload user resume",
    description="Uploads a PDF resume to Cloud Storage and updates the user profile with the human-readable filename."
)
async def upload_my_resume(
    current_user_id: CurrentUserId,
    user_controller: UserControllerDep,
    resume_file: UploadFile = File(...)
):
    """
    Expects a multipart/form-data request with a 'resume_file' field containing a PDF.
    """
    # 1. Read the file completely into the server's RAM (No temp files on disk!)
    file_bytes = await resume_file.read()
    
    # 2. Pass the raw bytes and metadata to the controller
    result = await user_controller.handle_upload_resume(
        user_id=current_user_id,
        file_bytes=file_bytes,
        content_type=resume_file.content_type,
        filename=resume_file.filename
    )
    
    # 3. Use your standard result handler to return the ViewModel or Error
    return handle_result(result)


@router.patch(
    "/update/me",
    response_model=UserViewModel,
    summary="Update current user profile",
    description="Update the authenticated user's profile information"
)
async def update_my_profile(
    data: UserUpdateSchema,
    current_user_id: CurrentUserId,
    user_controller: UserControllerDep
):
    """
    Update the authenticated user's own profile.
    
    Requires valid JWT token in Authorization header.
    Only provided fields will be updated (partial update).
    
    Updatable fields:
    - **firstname**: First name (1-50 characters)
    - **lastname**: Last name (1-50 characters)
    - **email**: Email address (must be unique)
    - **resume_dir**: Resume directory path
    - **phone_number**: Contact phone number
    
    Returns the updated user profile.
    """
    result = await user_controller.handle_update(
        user_id=current_user_id,  
        fname=data.firstname,
        lname=data.lastname,
        email=data.email,
        address=data.address,
        resume_path=data.resume_path,
        current_position=data.current_position,
        current_company=data.current_company,
        phone_number=data.phone_number,
        school_type=data.school_type,
        graduation_year=data.graduation_year,
        major=data.major,
        study_level=data.study_level
    )
    return handle_result(result)


@router.delete(
    "/delete/me",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete current user account",
    description="Permanently delete the authenticated user's account"
)
async def delete_my_account(
    current_user_id: CurrentUserId,
    user_controller: UserControllerDep
):
    """
    Permanently delete the authenticated user's account.
    
    Requires valid JWT token in Authorization header.
    
    **Warning**: This action cannot be undone.
    All user data and associated resources will be permanently deleted.
    """
    result = await user_controller.handle_delete(current_user_id)
    return handle_result(result)
    

