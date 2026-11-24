# ==============================================================================
# FILE: auto_apply_app/infrastructures/api/dependencies/container_dep.py
#
# WHAT IT DOES:
#   The bridge between HTTP requests and the dependency-injection container. It
#   returns the single Application object that lifespan() stored on app.state.
#   Routes use it through Depends(get_container) to reach controllers and services.
#
# USED BY:
#   - auto_apply_app/infrastructures/api/dependencies/auth_deps.py: token services
#   - auto_apply_app/infrastructures/api/dependencies/admin_deps.py: admin check
#   - auto_apply_app/infrastructures/api/dependencies/captcha_dep.py: captcha adapter
#   - auto_apply_app/infrastructures/api/routers/agent.py: controllers and progress broker
#   - auto_apply_app/infrastructures/api/routers/user.py: user and auth controllers
#   - auto_apply_app/infrastructures/api/routers/subscription.py: billing controller
#   - auto_apply_app/infrastructures/api/routers/admin.py: admin controllers
#   - auto_apply_app/infrastructures/api/routers/application.py: job offer controller
#   - ... and 6 more
#
# EXTERNAL DEPENDENCIES:
#   - fastapi: the Request object
# ==============================================================================
from fastapi import Request

from auto_apply_app.infrastructures.configuration.container import Application

def get_container(request: Request) -> Application:
    """Get the application container from app state."""
    return request.app.state.container