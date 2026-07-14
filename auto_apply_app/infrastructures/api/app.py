from contextlib import asynccontextmanager
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from auto_apply_app.infrastructures.api.rate_limit import limiter

from auto_apply_app.infrastructures.config import Config
from auto_apply_app.infrastructures.configuration.container import create_application
from auto_apply_app.infrastructures.persistence.database.session import engine #,init_db
from auto_apply_app.infrastructures.config import RepositoryType

# 🚨 NEW: Global exception handlers to prevent raw errors from reaching the frontend
from auto_apply_app.infrastructures.api.exception_handlers import register_exception_handlers
from auto_apply_app.infrastructures.api.middleware.ip_blocklist import ip_blocklist_middleware
from auto_apply_app.infrastructures.api.middleware.security_headers import security_headers_middleware


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
  WebAgentStatePresenter,
  WebAdminPresenter,
  WebAnalyticsPresenter,
)

# Import routers
from auto_apply_app.infrastructures.api.routers import (
  user,
  subscription,
  agent,
  application,
  preferences,
  free_search,
  agent_state,
  admin,
  analytics,
)

from auto_apply_app.infrastructures.api.cors import ALLOWED_ORIGINS

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
        free_search_presenter=WebFreeSearchPresenter(),
        admin_presenter=WebAdminPresenter(),
        analytics_presenter=WebAnalyticsPresenter(),
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
    app_role = os.getenv("APP_ROLE", "api").lower()   # "api" | "free_search"

    app = FastAPI(
        title="Auto Apply API",
        description="Job application automation service",
        version="1.0.0",
        openapi_url=None if is_production else "/openapi.json",
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        lifespan=lifespan
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # 🚨 Register global exception handlers BEFORE anything else
    # This ensures no raw error (SQL, asyncpg, etc.) ever reaches the frontend
    register_exception_handlers(app)

    app.middleware("http")(ip_blocklist_middleware)
    app.middleware("http")(security_headers_middleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # ---- ROLE-SPECIFIC: which routers are mounted ----
    if app_role == "free_search":
        # Free-search Service (browser image, scale-to-zero). Serves ONLY the
        # free-search route. Still needs JWT auth + DB for the per-user quota.
        app.include_router(
            free_search.router,
            prefix="/api/v1/free-search",
            tags=["Free Search"]
        )
    else:  # "api" (default) — everything EXCEPT free-search
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
            agent_state.router,
            prefix="/api/v1/agent-state",
            tags=["Agent State"]
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

        # Mounted at /site, not /analytics: ad-blockers and privacy filter lists match
        # URL substrings like "analytics" and "track" even on first-party requests, and a
        # blocked tracking call fails silently — we would undercount every visitor running
        # a blocker and never know. The module keeps its honest name; only the URL is bland.
        app.include_router(
            analytics.router,
            prefix="/api/v1/site",
            tags=["Site"]
        )

        # Admin is mounted under a secret prefix and hidden from the OpenAPI schema.
        # Both are defense-in-depth only — the real control is the per-request DB check
        # in CurrentAdminId, which holds even if this prefix leaks. With no prefix
        # configured we mount nothing at all, so a misconfigured deploy exposes no
        # admin surface rather than a default one.
        admin_prefix = Config.get_admin_route_prefix()
        if admin_prefix:
            app.include_router(
                admin.router,
                prefix=admin_prefix,
                include_in_schema=False,
            )
            logger.info("Admin router mounted.")   # never log the prefix itself
        else:
            logger.warning("ADMIN_ROUTE_PREFIX is not set — admin router NOT mounted.")

        # free_search is intentionally NOT mounted in the api role.

    return app