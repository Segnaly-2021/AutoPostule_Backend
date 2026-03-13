from fastapi import Request

from auto_apply_app.infrastructures.configuration.container import Application

def get_container(request: Request) -> Application:
    """Get the application container from app state."""
    return request.app.state.container