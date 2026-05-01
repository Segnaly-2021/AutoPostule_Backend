from fastapi import APIRouter, UploadFile, File, Depends, status
from typing import Annotated

from auto_apply_app.infrastructures.api.dependencies.result import handle_result
from auto_apply_app.infrastructures.configuration.container import Application
from auto_apply_app.interfaces.controllers.auth_controllers import AuthController
from auto_apply_app.interfaces.controllers.user_controllers import UserController
from auto_apply_app.interfaces.viewmodels.user_vm import UserViewModel, LoginViewModel, UploadResumeViewModel 
from auto_apply_app.infrastructures.api.dependencies.container_dep import get_container
from auto_apply_app.infrastructures.api.dependencies.auth_deps import CurrentUserId, CurrentToken

# 🚨 NEW: Added ForgotPasswordRequestSchema and ResetPasswordConfirmSchema to imports
from auto_apply_app.infrastructures.api.schema.user_schema import (
    ChangePasswordSchema,
    LoginSchema,
    RegisterSchema,
    UserUpdateSchema,
    ForgotPasswordRequestSchema,  
    ResetPasswordConfirmSchema    
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


# ============================================================================
# AUTHENTICATION ROUTES
# ============================================================================

@router.post(
        "/register", 
        response_model=UserViewModel, 
        status_code=status.HTTP_201_CREATED
)
async def register(
    data: RegisterSchema,  
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
    """
    result = await auth_controller.handle_login(
        email=data.email,
        password=data.password
    )
    return handle_result(result)


# 🚨 NEW: Forgot Password Route
@router.post(
    "/forgot-password",
    status_code=status.HTTP_200_OK,
    summary="Request password reset",
    description="Sends a password reset email if the account exists"
)
async def forgot_password(
    data: ForgotPasswordRequestSchema,
    auth_controller: AuthControllerDep
):
    """
    Initiates the password reset flow. 
    Sends an email with a short-lived token to the user.
    """
    result = await auth_controller.handle_forgot_password(email=data.email)
    return handle_result(result)


# 🚨 NEW: Reset Password Route
@router.post(
    "/reset-password",
    status_code=status.HTTP_200_OK,
    summary="Reset password",
    description="Sets a new password using the token received via email"
)
async def reset_password(
    data: ResetPasswordConfirmSchema,
    auth_controller: AuthControllerDep
):
    """
    Completes the password reset flow.
    Requires the valid token extracted from the email link and a new secure password.
    """
    result = await auth_controller.handle_reset_password(
        token=data.token,
        new_password=data.new_password
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
    current_user_id: CurrentUserId,  
    auth_controller: AuthControllerDep
):
    """
    Change the authenticated user's password.
    """
    result = await auth_controller.handle_change_password(
        user_id=current_user_id,  
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
    token: CurrentToken,  
    auth_controller: AuthControllerDep
):
    """
    Logout the authenticated user.
    """
    result = await auth_controller.handle_logout(token=token)
    handle_result(result)
    return None


# ============================================================================
# USER PROFILE ROUTES
# ============================================================================

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
    result = await user_controller.handle_get(current_user_id)
    return handle_result(result)


@router.post(
    "/update/me/resume",
    status_code=status.HTTP_200_OK,
    response_model=UploadResumeViewModel, 
    summary="Upload user resume",
    description="Uploads a PDF resume to Cloud Storage and updates the user profile with the human-readable filename."
)
async def upload_my_resume(
    current_user_id: CurrentUserId,
    user_controller: UserControllerDep,
    resume_file: UploadFile = File(...)
):
    file_bytes = await resume_file.read()
    
    result = await user_controller.handle_upload_resume(
        user_id=current_user_id,
        file_bytes=file_bytes,
        content_type=resume_file.content_type,
        filename=resume_file.filename
    )
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
        study_level=data.study_level,
        linkedin_url=data.linkedin_url
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
    result = await user_controller.handle_delete(current_user_id)
    return handle_result(result)