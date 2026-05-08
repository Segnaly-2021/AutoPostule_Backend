# auto_apply_app/infrastructures/configuration/container.py

import os
from dataclasses import dataclass
from typing import Callable

# Repositories & UoW
from auto_apply_app.application.repositories.token_blacklist import TokenBlacklistRepository
from auto_apply_app.application.repositories.unit_of_work import UnitOfWork
from auto_apply_app.infrastructures.repository_factory import create_repositories
from auto_apply_app.infrastructures.agent.create_agent import create_agent

# Service Ports
from auto_apply_app.application.service_ports.password_service_port import PasswordServicePort
from auto_apply_app.application.service_ports.file_storage_port import FileStoragePort
from auto_apply_app.application.service_ports.token_provider_port import TokenProviderPort
from auto_apply_app.application.service_ports.payment_port import PaymentPort
from auto_apply_app.application.service_ports.encryption_port import EncryptionServicePort
from auto_apply_app.application.service_ports.email_service_port import EmailServicePort
from auto_apply_app.application.service_ports.captcha_service_port import CaptchaServicePort

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
    VerifyEmailUseCase,
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

# NEW: agent state use cases (replaced ShutdownAgentUseCase + ResetAgentUseCase)
from auto_apply_app.application.use_cases.agent_state_use_cases import (
    GetAgentStateUseCase,
    BindAgentToSearchUseCase,
    RequestAgentShutdownUseCase,
    IsAgentKilledForSearchUseCase,
)

# NEW: completion + free-search use cases
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

    token_repo, uow_factory = create_repositories()

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
        free_search_presenter=free_search_presenter,
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
    # Presenters
    user_presenter: UserPresenter
    job_presenter: JobPresenter
    search_presenter: JobSearchPresenter
    sub_presenter: SubPresenter
    agent_presenter: AgentPresenter
    preference_presenter: PreferencesPresenter
    agent_state_presenter: AgentStatePresenter
    free_search_presenter: FreeSearchPresenter

    # =========================================================================
    # CONTROLLER FACTORIES
    # =========================================================================

    @property
    def user_controller(self) -> UserController:
        uow = self.uow_factory()
        return UserController(
            get_user_use_case=GetUserUseCase(uow),
            update_user_use_case=UpdateUserUseCase(uow),
            delete_user_use_case=DeleteUserUseCase(uow),
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
                token_provider=self.token_provider,            # NEW
                email_service=self.email_service_port,         # NEW
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
            verify_email_use_case=VerifyEmailUseCase(            # NEW
                uow=uow,
                token_provider=self.token_provider,
            ),
            resend_verification_use_case=ResendVerificationEmailUseCase(  # NEW
                uow=uow,
                token_provider=self.token_provider,
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

        proxy_service = _resolve_proxy_service()
        get_or_create_fingerprint_uc = GetOrCreateUserFingerprintUseCase(
            uow=uow,
            generator=FingerprintGenerator(),
        )

        # NEW: build the use cases the master agent needs
        bind_agent_to_search_uc = BindAgentToSearchUseCase(uow)
        is_agent_killed_uc = IsAgentKilledForSearchUseCase(uow)
        complete_agent_run_uc = CompleteAgentRunUseCase(uow)

        agent_service = create_agent(
            results_saver=SaveJobApplicationsUseCase(uow),
            consume_credits_use_case=ConsumeAiCreditsUseCase(uow),
            get_ignored_hashes_use_case=GetIgnoredHashesUseCase(uow),
            file_storage=self.file_storage_port,
            encryption_service=self.encryption_port,
            get_agent_state_use_case=GetAgentStateUseCase(uow),
            bind_agent_to_search_use_case=bind_agent_to_search_uc,
            is_agent_killed_for_search_use_case=is_agent_killed_uc,
            complete_agent_run_use_case=complete_agent_run_uc,
            get_daily_stats_use_case=GetDailyStatsUseCase(uow),
            cleanup_unsubmitted_use_case=CleanupUnsubmittedJobsUseCase(uow),
            get_or_create_fingerprint_use_case=get_or_create_fingerprint_uc,
            proxy_service=proxy_service,
        )

        return AgentController(
            start_agent_use_case=StartJobSearchAgentUseCase(uow, agent_service),
            resume_agent_use_case=ResumeJobApplicationUseCase(uow, agent_service),
            kill_agent_use_case=KillJobSearchUseCase(uow, agent_service),
            get_jobs_for_review_use_case=GetJobsForReviewUseCase(uow),
            update_cover_letter_use_case=UpdateCoverLetterUseCase(uow),
            approve_job_use_case=ApproveJobUseCase(uow),
            discard_job_use_case=DiscardJobUseCase(uow),
            presenter=self.agent_presenter,
            job_presenter=self.job_presenter,
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