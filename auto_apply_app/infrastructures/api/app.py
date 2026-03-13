from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging

from auto_apply_app.infrastructures.config import Config
from auto_apply_app.infrastructures.configuration.container import create_application
#from auto_apply_app.infrastructures.persistence.database.session import init_db, engine

# Import your concrete implementations
from auto_apply_app.infrastructures.authentication.password_service import PasswordService
from auto_apply_app.infrastructures.authentication.token_provider import JwtTokenProvider
from auto_apply_app.infrastructures.payment.stripe_payment import StripePaymentAdapter
from auto_apply_app.infrastructures.board_credentials_encryption.encryption import EncryptionService

# Import presenters
from auto_apply_app.interfaces.presenters.web import (
  WebUserPresenter,
  WebJobPresenter,
  WebJobSearchPresenter,
  WebSubPresenter,
  WebAgentPresenter,
  WebPreferencesPresenter,
  WebFreeSearchPresenter
)

# Import routers
from auto_apply_app.infrastructures.api.routers import (
  user, 
  subscription, 
  agent, 
  application,
  preferences,
  free_search,
) 

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages the application lifecycle: startup and shutdown.
    """
    logger.info("Starting application...")
    
    # 1. Initialize database tables
    #await init_db()
    logger.info("Database initialized")
    
    # 2. Load configuration
    config = Config()

    
   
    
    # 3. Build the Application container
    container = create_application(
        user_presenter=WebUserPresenter(),
        job_presenter=WebJobPresenter(),
        search_presenter=WebJobSearchPresenter(),
        agent_presenter=WebAgentPresenter(),
        password_service=PasswordService(),
        token_provider=JwtTokenProvider(),
        preferences_presenter=WebPreferencesPresenter(),
        encryption_port=EncryptionService(Config.get_encryption_key()),
        payment_port=StripePaymentAdapter(),
        sub_presenter=WebSubPresenter(),
        free_search_presenter=WebFreeSearchPresenter()
    )
    
    # 6. Attach container to app state
    app.state.container = container
    app.state.config = config
    
    logger.info("Application container initialized successfully")
    
    yield
    
    # Cleanup on shutdown
    logger.info("Shutting down application...")
    
    # Close database connections
    #await engine.dispose()
    
    logger.info("Application shutdown complete")

def create_fastapi_app() -> FastAPI:
    """
    Factory function to create and configure the FastAPI application.
    """
    app = FastAPI(
        title="Auto Apply API",
        description="Job application automation service",
        version="1.0.0",
        lifespan=lifespan
    )
    
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",  # Vite default dev server
            "http://localhost:3000",  # Alternative port
            "http://127.0.0.1:5173",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],  # Allow all methods (GET, POST, PUT, DELETE, etc.)
        allow_headers=["*"],  # Allow all headers (Authorization, Content-Type, etc.)
    )
    
    
    app.include_router(
        user.router,
        prefix="/api/v1/user",
        tags=["users"]
    )

    app.include_router(
        subscription.router,
        prefix="/api/v1/subscription",
        tags=["subscriptions"]
    )  
    
    app.include_router(
        agent.router,
        prefix="/api/v1/agent",
        tags=["agent"]
    )

    app.include_router(
        application.router,
        prefix="/api/v1/applications",
        tags=["application"]
    )

    app.include_router(
        preferences.router,
        prefix="/api/v1/preferences",
        tags=["Preferences"]
    )

    app.include_router(
        free_search.router,
        prefix="/api/v1/free-search",
        tags=["Free Search"]
    )
    
    return app


