from contextlib import asynccontextmanager
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from auto_apply_app.infrastructures.config import Config
from auto_apply_app.infrastructures.configuration.container import create_application
from auto_apply_app.infrastructures.persistence.database.session import engine #,init_db
from auto_apply_app.infrastructures.config import RepositoryType

# 🚨 NEW: Global exception handlers to prevent raw errors from reaching the frontend
from auto_apply_app.infrastructures.api.exception_handlers import register_exception_handlers

# Import your concrete implementations
from auto_apply_app.infrastructures.authentication.password_service import PasswordService
from auto_apply_app.infrastructures.resume_storage.gcs_storage_adapter import GCSFileStorageAdapter
from auto_apply_app.infrastructures.authentication.token_provider import JwtTokenProvider
from auto_apply_app.infrastructures.payment.stripe_payment import StripePaymentAdapter
from auto_apply_app.infrastructures.board_credentials_encryption.encryption import EncryptionService
from auto_apply_app.infrastructures.emailing_service.resend_email_service import ResendEmailService
from auto_apply_app.infrastructures.captcha.turnstile_adapter import TurnstileCaptchaAdapter


# Import presenters
from auto_apply_app.interfaces.presenters.web import (
  WebUserPresenter,
  WebJobPresenter,
  WebJobSearchPresenter,
  WebSubPresenter,
  WebAgentPresenter,
  WebPreferencesPresenter,
  WebFreeSearchPresenter,
  WebAgentStatePresenter
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
    
    config = Config()

    if config.get_repository_type() == RepositoryType.DATABASE:
        logger.info("PostgreSQL Database initialized and verified.")
    else:
        logger.info("Running in MEMORY mode. Skipping DB initialization.")
    
    container = create_application(
        user_presenter=WebUserPresenter(),
        job_presenter=WebJobPresenter(),
        search_presenter=WebJobSearchPresenter(),
        agent_presenter=WebAgentPresenter(),
        agent_state_presenter=WebAgentStatePresenter(),
        password_service=PasswordService(),
        token_provider=JwtTokenProvider(),
        preferences_presenter=WebPreferencesPresenter(),
        encryption_port=EncryptionService(Config.get_encryption_key()),
        file_storage_port=GCSFileStorageAdapter(),
        payment_port=StripePaymentAdapter(),
        sub_presenter=WebSubPresenter(),
        captcha_port=TurnstileCaptchaAdapter(),
        email_service_port=ResendEmailService(),
        free_search_presenter=WebFreeSearchPresenter()
    )
    
    app.state.container = container
    app.state.config = config
    
    logger.info("Application container initialized successfully")
    
    yield
    
    logger.info("Shutting down application...")
    
    if config.get_repository_type() == RepositoryType.DATABASE:
        await engine.dispose()
        logger.info("PostgreSQL connection pool closed successfully.")
        
    logger.info("Application shutdown complete")


def create_fastapi_app() -> FastAPI:
    """
    Factory function to create and configure the FastAPI application.
    """
    is_production = os.getenv("ENV", "development") == "production"
    
    app = FastAPI(
        title="Auto Apply API",
        description="Job application automation service",
        version="1.0.0",
        openapi_url=None if is_production else "/openapi.json",
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        lifespan=lifespan
    )

    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # 🚨 Register global exception handlers BEFORE anything else
    # This ensures no raw error (SQL, asyncpg, etc.) ever reaches the frontend
    register_exception_handlers(app)

    

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "https://autopostule.com",
            "https://www.autopostule.com",
            "https://autopostule.netlify.app", 
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
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