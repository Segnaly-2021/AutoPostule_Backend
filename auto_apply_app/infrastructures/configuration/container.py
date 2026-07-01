# auto_apply_app/infrastructures/configuration/container.py

import os
from dataclasses import dataclass
from functools import cached_property
from typing import Callable, Optional

from redis.asyncio import Redis

# Repositories & UoW
from auto_apply_app.application.repositories.token_blacklist import TokenBlacklistRepository
from auto_apply_app.application.repositories.unit_of_work import UnitOfWork
from auto_apply_app.infrastructures.repository_factory import create_repositories
from auto_apply_app.infrastructures.agent.create_agent import create_agent

# Progress relay + dispatch (Phase A decoupling)
from auto_apply_app.application.service_ports.progress_broker_port import ProgressBrokerPort
from auto_apply_app.application.service_ports.dispatch_port import DispatchPort
from auto_apply_app.infrastructures.progress.redis_progress_broker import RedisProgressBroker
from auto_apply_app.infrastructures.progress.in_memory_progress_broker import InMemoryProgressBroker
from auto_apply_app.infrastructures.agent.dispatch.local_dispatcher import LocalDispatcher
from auto_apply_app.application.use_cases.run_context_use_cases import (
    LoadStartRunContextUseCase,
    LoadResumeRunContextUseCase,
)

# Service Ports
from auto_apply_app.application.service_ports.password_service_port import PasswordServicePort
from auto_apply_app.application.service_ports.file_storage_port import FileStoragePort
from auto_apply_app.application.service_ports.token_provider_port import TokenProviderPort
from auto_apply_app.application.service_ports.payment_port import PaymentPort
from auto_apply_app.application.service_ports.encryption_port import EncryptionServicePort
from auto_apply_app.application.service_ports.email_service_port import EmailServicePort
from auto_apply_app.application.service_ports.captcha_service_port import CaptchaServicePort
from auto_apply_app.application.service_ports.rate_limiter_port import RateLimiterPort  # NEW

# Rate limiter adapter
from auto_apply_app.infrastructures.redis_rate_limit.redis_rate_limiter import RedisRateLimiter  # NEW

# Proxy / fingerprint
from auto_apply_app.infrastructures.proxy.iproyal_proxy_adapter import IPRoyalProxyAdapter
from auto_apply_app.infrastructures.proxy.no_proxy_adapter import NoProxyAdapter
from auto_apply_app.infrastructures.agent.fingerprint_generator import FingerprintGenerator

# Presenters
from auto_apply_app.interfaces.presenters.base_presenter import (
    AgentPresenter,
    UserPresenter,
    JobPresenter,
    JobSearchPresenter,
    SubPresenter,
    PreferencesPresenter,
    FreeSearchPresenter,
    AgentStatePresenter,
)

# Use Cases
from auto_apply_app.application.use_cases.user_use_cases import (
    DeleteUserUseCase,
    UpdateUserUseCase,
    RegisterUserUseCase,
    LoginUserUseCase,
    LogoutUseCase,
    GetUserUseCase,
    ChangePasswordUseCase,
    UploadUserResumeUseCase,
    RequestPasswordResetUseCase,
    ConfirmPasswordResetUseCase,
    ResendVerificationEmailUseCase,
    VerifyCodeUseCase,  # CHANGED: was VerifyEmailUseCase
    RequestEmailChangeUseCase,
    ConfirmEmailChangeUseCase,
)
from auto_apply_app.application.use_cases.agent_use_cases import (
    ApproveJobUseCase,
    DiscardJobUseCase,
    GetIgnoredHashesUseCase,
    GetJobsForReviewUseCase,
    StartJobSearchAgentUseCase,
    ResumeJobApplicationUseCase,
    KillJobSearchUseCase,
    SaveJobApplicationsUseCase,
    UpdateCoverLetterUseCase,
    ConsumeAiCreditsUseCase,
    ListRecentSearchesUseCase,  # NEW
    SetSearchStatusUseCase,  # NEW
    GetSearchStatusUseCase,  # NEW
)
from auto_apply_app.application.use_cases.subscription_use_cases import (
    GetUserSubscriptionUseCase,
    CreateCheckoutSessionUseCase,
    HandlePaymentWebhookUseCase,
    GetManagementPortalUseCase,
)
from auto_apply_app.application.use_cases.job_offer_use_cases import (
    GetApplicationAnalyticsUseCase,
    GetUserApplicationsUseCase,
    ToggleInterviewStatusUseCase,
    ToggleResponseStatusUseCase,
    CleanupUnsubmittedJobsUseCase,
    GetDailyStatsUseCase,
)
from auto_apply_app.application.use_cases.preferences_use_cases import (
    GetUserPreferencesUseCase,
    UpdateUserPreferencesUseCase,
)

from auto_apply_app.application.use_cases.agent_state_use_cases import (
    GetAgentStateUseCase,
    CreateAgentStateForSearchUseCase,
    RequestAgentShutdownUseCase,
    IsAgentKilledForSearchUseCase,
    GetAgentLivenessForSearchUseCase,  # NEW
    HeartbeatAgentForSearchUseCase,  # NEW
)

from auto_apply_app.application.use_cases.agent_usage_use_cases import CompleteAgentRunUseCase
from auto_apply_app.application.use_cases.free_search_use_cases import FreeSearchUseCase

from auto_apply_app.application.use_cases.fingerprint_use_cases import (
    GetOrCreateUserFingerprintUseCase,
)

# Controllers
from auto_apply_app.interfaces.controllers.user_controllers import UserController
from auto_apply_app.interfaces.controllers.auth_controllers import AuthController
from auto_apply_app.interfaces.controllers.subscription_controllers import SubscriptionController
from auto_apply_app.interfaces.controllers.agent_controllers import AgentController
from auto_apply_app.interfaces.controllers.job_offer_controllers import JobOfferController
from auto_apply_app.interfaces.controllers.preference_controllers import PreferencesController
from auto_apply_app.interfaces.controllers.agent_state_controllers import AgentStateController
from auto_apply_app.interfaces.controllers.free_search_controller import FreeSearchController

# Free-search infra
from auto_apply_app.infrastructures.agent.fake_agent.create_fake_agent import create_fake_agent


def _resolve_proxy_service():
    """Resolves the proxy adapter based on the PROXY_PROVIDER env var."""
    provider = os.getenv("PROXY_PROVIDER", "none").lower()
    if provider == "iproyal":
        return IPRoyalProxyAdapter()
    return NoProxyAdapter()


def _build_rate_limiter(redis_client: Optional[Redis]) -> RateLimiterPort:
    """
    Build the rate limiter. In DATABASE mode we reuse the same Redis client
    that backs the token blacklist. In MEMORY mode we fall back to a no-op
    limiter so dev/test environments aren't forced to run Redis.
    """
    if redis_client is not None:
        return RedisRateLimiter(redis_client)

    # In-memory fallback: always allow. Acceptable in dev because rate-limiting
    # is a defense-in-depth measure — the use case still functions without it.
    class _NoopRateLimiter(RateLimiterPort):
        async def try_acquire(self, key: str, window_seconds: int) -> tuple[bool, int]:
            return True, 0

    return _NoopRateLimiter()


def create_application(
    user_presenter: UserPresenter,
    job_presenter: JobPresenter,
    sub_presenter: SubPresenter,
    agent_presenter: AgentPresenter,
    search_presenter: JobSearchPresenter,
    preferences_presenter: PreferencesPresenter,
    agent_state_presenter: AgentStatePresenter,
    password_service: PasswordServicePort,
    token_provider: TokenProviderPort,
    file_storage_port: FileStoragePort,
    payment_port: PaymentPort,
    encryption_port: EncryptionServicePort,
    email_service_port: EmailServicePort,
    captcha_port: CaptchaServicePort,
    free_search_presenter: FreeSearchPresenter,
) -> "Application":

    # create_repositories now returns the Redis client too — we reuse it for
    # the rate limiter so we don't open a second connection pool.
    token_repo, uow_factory, redis_client = create_repositories()
    rate_limiter = _build_rate_limiter(redis_client)

    return Application(
        token_repo=token_repo,
        uow_factory=uow_factory,
        user_presenter=user_presenter,
        preference_presenter=preferences_presenter,
        agent_state_presenter=agent_state_presenter,
        job_presenter=job_presenter,
        search_presenter=search_presenter,
        agent_presenter=agent_presenter,
        sub_presenter=sub_presenter,
        password_service=password_service,
        token_provider=token_provider,
        file_storage_port=file_storage_port,
        payment_port=payment_port,
        encryption_port=encryption_port,
        captcha_port=captcha_port,
        email_service_port=email_service_port,
        rate_limiter=rate_limiter,  # NEW
        redis_client=redis_client,   # NEW (None in MEMORY mode)
        free_search_presenter=free_search_presenter,
    )


def create_worker_application() -> "Application":
    """
    Minimal composition root for the Cloud Run Job worker (worker_main.py).

    The worker ONLY ever uses Application.agent_runner, whose dependency graph is
    agent_service + progress_broker + uow_factory + Load*RunContextUseCase. None of
    those touch the web-only ports/presenters.

    We must NOT call the full create_application() here: JwtTokenProvider and
    TurnstileCaptchaAdapter RAISE at construction when JWT_SECRET / TURNSTILE_SECRET_KEY
    are absent, and the Job intentionally omits those env vars (B-1 §6). So we build
    only the agent-relevant adapters for real (GCS storage + encryption + repos +
    Redis) and leave the unused ports/presenters as None.

    The runner is still built by Application.agent_runner — the single source of
    truth — so there is no hand-rolled use-case wiring on this path.
    """
    from auto_apply_app.infrastructures.config import Config
    from auto_apply_app.infrastructures.resume_storage.gcs_storage_adapter import (
        GCSFileStorageAdapter,
    )
    from auto_apply_app.infrastructures.board_credentials_encryption.encryption import (
        EncryptionService,
    )

    token_repo, uow_factory, redis_client = create_repositories()

    return Application(
        token_repo=token_repo,
        uow_factory=uow_factory,
        # Agent-relevant ports (built for real):
        file_storage_port=GCSFileStorageAdapter(),
        encryption_port=EncryptionService(Config.get_encryption_key()),
        rate_limiter=_build_rate_limiter(redis_client),
        redis_client=redis_client,
        # Web-only ports/presenters — unused by the agent run path, left as None
        # so we never construct adapters that require absent web env vars.
        password_service=None,
        token_provider=None,
        payment_port=None,
        email_service_port=None,
        captcha_port=None,
        user_presenter=None,
        job_presenter=None,
        search_presenter=None,
        sub_presenter=None,
        agent_presenter=None,
        preference_presenter=None,
        agent_state_presenter=None,
        free_search_presenter=None,
    )


@dataclass
class Application:
    # Repositories & UoW Factory
    token_repo: TokenBlacklistRepository
    uow_factory: Callable[[], UnitOfWork]

    # Infrastructure Services (Ports)
    password_service: PasswordServicePort
    token_provider: TokenProviderPort
    file_storage_port: FileStoragePort
    payment_port: PaymentPort
    encryption_port: EncryptionServicePort
    email_service_port: EmailServicePort
    captcha_port: CaptchaServicePort
    rate_limiter: RateLimiterPort  # NEW

    # Presenters
    user_presenter: UserPresenter
    job_presenter: JobPresenter
    search_presenter: JobSearchPresenter
    sub_presenter: SubPresenter
    agent_presenter: AgentPresenter
    preference_presenter: PreferencesPresenter
    agent_state_presenter: AgentStatePresenter
    free_search_presenter: FreeSearchPresenter

    # Shared Redis client (None in MEMORY mode). Surfaced so the progress
    # broker can reuse the same connection pool as the rate limiter.
    redis_client: Optional[Redis] = None   # NEW

    # =========================================================================
    # SINGLETONS (Phase A decoupling)
    # =========================================================================
    # The broker MUST be a singleton so the in-memory impl's queues are shared
    # between publisher (agent task) and subscriber (SSE generator). The agent
    # service and dispatcher MUST be singletons so background tasks aren't tied
    # to a request-scoped object, and so kill finds live workers across requests.

    @cached_property
    def progress_broker(self) -> ProgressBrokerPort:
        if self.redis_client is not None:
            return RedisProgressBroker(self.redis_client)
        return InMemoryProgressBroker()

    @cached_property
    def _agent_service(self):
        uow = self.uow_factory()
        proxy_service = _resolve_proxy_service()
        get_or_create_fingerprint_uc = GetOrCreateUserFingerprintUseCase(
            uow=uow, generator=FingerprintGenerator(),
        )
        return create_agent(
            results_saver=SaveJobApplicationsUseCase(uow),
            consume_credits_use_case=ConsumeAiCreditsUseCase(uow),
            get_ignored_hashes_use_case=GetIgnoredHashesUseCase(uow),
            file_storage=self.file_storage_port,
            encryption_service=self.encryption_port,
            get_agent_state_use_case=GetAgentStateUseCase(uow),
            create_agent_state_use_case=CreateAgentStateForSearchUseCase(uow),
            is_agent_killed_for_search_use_case=IsAgentKilledForSearchUseCase(uow),
            complete_agent_run_use_case=CompleteAgentRunUseCase(uow),
            heartbeat_use_case=HeartbeatAgentForSearchUseCase(uow),
            set_search_status_use_case=SetSearchStatusUseCase(uow),
            get_daily_stats_use_case=GetDailyStatsUseCase(uow),
            cleanup_unsubmitted_use_case=CleanupUnsubmittedJobsUseCase(uow),
            get_or_create_fingerprint_use_case=get_or_create_fingerprint_uc,
            proxy_service=proxy_service,
        )

    @cached_property
    def agent_runner(self):
        """
        Single construction path for the run logic, shared by BOTH dispatchers
        (in-process LocalDispatcher) and the Cloud Run Job worker (worker_main).
        Keeping this here means the use-case wiring lives in ONE place — neither
        the dispatcher nor the worker hand-rolls it.
        """
        from auto_apply_app.infrastructures.agent.runner import AgentRunner
        uow = self.uow_factory()
        return AgentRunner(
            agent_service=self._agent_service,
            broker=self.progress_broker,
            load_start_ctx=LoadStartRunContextUseCase(uow),
            load_resume_ctx=LoadResumeRunContextUseCase(uow),
        )

    @cached_property
    def _dispatcher(self) -> DispatchPort:
        """
        Picks the dispatcher by config:
          - AGENT_DISPATCH_MODE=cloud_run_job → CloudRunJobsDispatcher (prod):
            triggers a separate Cloud Run Job; progress returns over Redis.
          - anything else (default "local")   → LocalDispatcher: runs the agent
            in-process via AgentRunner (dev / MEMORY mode).
        """
        mode = os.getenv("AGENT_DISPATCH_MODE", "local").lower()
        if mode == "cloud_run_job":
            from auto_apply_app.infrastructures.agent.dispatch.cloud_run_jobs_dispatcher import (
                CloudRunJobsDispatcher,
            )
            return CloudRunJobsDispatcher(
                project=os.environ["GCP_PROJECT_ID"],   # already set on the API
                region=os.environ["AGENT_JOB_REGION"],  # new env (e.g. europe-west9)
                job_name=os.environ["AGENT_JOB_NAME"],  # new env (e.g. autopostule-agent)
            )
        return LocalDispatcher(self.agent_runner)

    # =========================================================================
    # CONTROLLER FACTORIES
    # =========================================================================

    @property
    def user_controller(self) -> UserController:
        uow = self.uow_factory()
        return UserController(
            get_user_use_case=GetUserUseCase(uow),
            update_user_use_case=UpdateUserUseCase(uow),
            delete_user_use_case=DeleteUserUseCase(uow, self.file_storage_port),
            upload_resume_use_case=UploadUserResumeUseCase(uow, self.file_storage_port),
            presenter=self.user_presenter,
        )

    @property
    def auth_controller(self) -> AuthController:
        uow = self.uow_factory()
        return AuthController(
            register_use_case=RegisterUserUseCase(
                uow=uow,
                password_service=self.password_service,
                email_service=self.email_service_port,
                # token_provider removed — registration no longer issues a JWT.
            ),
            login_use_case=LoginUserUseCase(self.password_service, self.token_provider, uow),
            logout_use_case=LogoutUseCase(self.token_provider, self.token_repo),
            change_password_use_case=ChangePasswordUseCase(self.password_service, uow),
            request_password_reset_use_case=RequestPasswordResetUseCase(
                uow=uow,
                token_provider=self.token_provider,
                email_service=self.email_service_port,
            ),
            confirm_password_reset_use_case=ConfirmPasswordResetUseCase(
                uow=uow,
                token_provider=self.token_provider,
                password_service=self.password_service,
            ),
            verify_code_use_case=VerifyCodeUseCase(  # CHANGED: was verify_email_use_case
                uow=uow,
                password_service=self.password_service,
                token_provider=self.token_provider,
            ),
            resend_verification_use_case=ResendVerificationEmailUseCase(
                uow=uow,
                password_service=self.password_service,  # NEW: needed to hash codes
                email_service=self.email_service_port,
                rate_limiter=self.rate_limiter,          # NEW: Redis-backed cooldown
                # token_provider removed — codes don't use JWTs.
            ),
            request_email_change_use_case=RequestEmailChangeUseCase(
                uow=uow,
                password_service=self.password_service,
                email_service=self.email_service_port,
                rate_limiter=self.rate_limiter,
            ),
            confirm_email_change_use_case=ConfirmEmailChangeUseCase(
                uow=uow,
                password_service=self.password_service,
                payment_port=self.payment_port,
                email_service=self.email_service_port,
            ),
            presenter=self.user_presenter,
        )

    @property
    def subscription_controller(self) -> SubscriptionController:
        uow = self.uow_factory()
        return SubscriptionController(
            get_subscription_use_case=GetUserSubscriptionUseCase(uow),
            create_checkout_use_case=CreateCheckoutSessionUseCase(uow, self.payment_port),
            handle_webhook_use_case=HandlePaymentWebhookUseCase(uow, self.payment_port),
            get_portal_use_case=GetManagementPortalUseCase(uow, self.payment_port),
            presenter=self.sub_presenter,
        )

    @property
    def agent_controller(self) -> AgentController:
        uow = self.uow_factory()

        return AgentController(
            start_agent_use_case=StartJobSearchAgentUseCase(uow, self._dispatcher),
            resume_agent_use_case=ResumeJobApplicationUseCase(uow, self._dispatcher),
            kill_agent_use_case=KillJobSearchUseCase(uow, self._agent_service),
            get_jobs_for_review_use_case=GetJobsForReviewUseCase(uow),
            update_cover_letter_use_case=UpdateCoverLetterUseCase(uow),
            approve_job_use_case=ApproveJobUseCase(uow),
            discard_job_use_case=DiscardJobUseCase(uow),
            list_recent_searches_use_case=ListRecentSearchesUseCase(uow),  # NEW
            get_search_status_use_case=GetSearchStatusUseCase(uow),  # NEW
            presenter=self.agent_presenter,
            job_presenter=self.job_presenter,
            search_presenter=self.search_presenter,  # NEW
        )

    @property
    def job_offer_controller(self) -> JobOfferController:
        uow = self.uow_factory()
        return JobOfferController(
            get_analytics_use_case=GetApplicationAnalyticsUseCase(uow),
            get_user_applications_use_case=GetUserApplicationsUseCase(uow),
            toggle_interview_status_use_case=ToggleInterviewStatusUseCase(uow),
            toggle_response_status_use_case=ToggleResponseStatusUseCase(uow),
            get_daily_stats_use_case=GetDailyStatsUseCase(uow),
            job_offer_presenter=self.job_presenter,
        )

    @property
    def prefrences_controller(self) -> PreferencesController:
        uow = self.uow_factory()
        return PreferencesController(
            get_prefs_use_case=GetUserPreferencesUseCase(uow),
            update_prefs_use_case=UpdateUserPreferencesUseCase(uow, self.encryption_port),
            presenter=self.preference_presenter,
        )

    @property
    def agent_state_controller(self) -> AgentStateController:
        uow = self.uow_factory()
        return AgentStateController(
            get_agent_state_use_case=GetAgentStateUseCase(uow),
            request_shutdown_use_case=RequestAgentShutdownUseCase(uow),
            get_liveness_use_case=GetAgentLivenessForSearchUseCase(uow),  # NEW
            presenter=self.agent_state_presenter,
        )

    @property
    def free_search_controller(self) -> FreeSearchController:
        uow = self.uow_factory()
        return FreeSearchController(
            free_search_use_case=FreeSearchUseCase(
                uow=uow,
                fake_agent=create_fake_agent(),
            ),
            presenter=self.free_search_presenter,
        )