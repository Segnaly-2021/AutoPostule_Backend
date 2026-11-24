# ==============================================================================
# FILE: auto_apply_app/infrastructures/api/routers/preferences.py
#
# WHAT IT DOES:
#   User settings routes, mounted at /api/v1/preferences: automation mode, AI model
#   and creativity, active job boards, and saving job-board credentials.
#
# USED BY:
#   - auto_apply_app/infrastructures/api/app.py: mounts this router in the "api" role
#
# EXTERNAL DEPENDENCIES:
#   - fastapi: routing and dependency injection
# ==============================================================================
# auto_apply_app/infrastructures/api/routes/preferences_routes.py

from fastapi import APIRouter, Depends, status
from typing import Annotated

from auto_apply_app.interfaces.controllers.preference_controllers import PreferencesController
from auto_apply_app.infrastructures.api.dependencies.result import handle_result
from auto_apply_app.infrastructures.api.dependencies.auth_deps import CurrentUserId
from auto_apply_app.infrastructures.api.dependencies.container_dep import get_container
from auto_apply_app.infrastructures.configuration.container import Application
from auto_apply_app.infrastructures.api.schema.preferences_schema import (
    UpdatePreferencesSchema,
    PreferencesResponseSchema
)

router = APIRouter()

# Dependency to get controller
def get_preferences_controller(
    app_container: Annotated[Application, Depends(get_container)]
) -> PreferencesController:
    return app_container.prefrences_controller 

PreferencesControllerDep = Annotated[PreferencesController, Depends(get_preferences_controller)]

@router.get(
    "/me",
    response_model=PreferencesResponseSchema,
    summary="Get my preferences",
    description="Retrieve the current user's preferences and settings"
)
async def get_my_preferences(
    current_user_id: CurrentUserId,
    controller: PreferencesControllerDep
):
    """
    Fetch user preferences including:
    - Automation mode (semi vs full)
    - AI creativity level
    - AI Model selection (gemini, claude, chatgpt)
    - Active job boards
    - Credential configuration status (without exposing actual credentials)
    """
    result = await controller.handle_get(user_id=current_user_id)
    return handle_result(result)


@router.put(
    "/update/me",
    status_code=status.HTTP_200_OK,
    summary="Update my preferences",
    description="Update user preferences and optionally save job board credentials"
)
async def update_my_preferences(
    data: UpdatePreferencesSchema,
    current_user_id: CurrentUserId,
    controller: PreferencesControllerDep
):
    """
    Update user preferences.
    """
    
    # These are still plain-text job-board passwords: never log them. The update use
    # case receives the encryption port and is responsible for encrypting before saving.
    credentials_dict = None
    if data.credentials:
        credentials_dict = {
            board: {"login": cred.login, "password": cred.password}
            for board, cred in data.credentials.items()
        }
    
    result = await controller.handle_update(
        user_id=current_user_id,
        is_full_automation=data.isFullAutomation,
        creativity_level=data.creativity,
        ai_model=data.aiModel, # ✅ NEW: Pass down to controller
        active_boards=data.boards,
        credentials=credentials_dict
    )
    
    return handle_result(result)