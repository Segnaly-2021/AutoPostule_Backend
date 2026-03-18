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
from auto_apply_app.interfaces.controllers.preference_controllers import PreferencesController

# Presenters
from auto_apply_app.interfaces.presenters.base_presenter import (
    AgentPresenter,
    UserPresenter,
    JobPresenter,
    JobSearchPresenter, 
    SubPresenter,
    PreferencesPresenter,
    FreeSearchPresenter
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
    UploadUserResumeUseCase
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
    ToggleResponseStatusUseCase
)

from auto_apply_app.application.use_cases.preferences_use_cases import (
    GetUserPreferencesUseCase,
    UpdateUserPreferencesUseCase
)

# Controllers
from auto_apply_app.interfaces.controllers.user_controllers import UserController
from auto_apply_app.interfaces.controllers.auth_controllers import AuthController
from auto_apply_app.interfaces.controllers.subscription_controllers import SubscriptionController
from auto_apply_app.interfaces.controllers.agent_controllers import AgentController
from auto_apply_app.interfaces.controllers.job_offer_controllers import JobOfferController

from auto_apply_app.infrastructures.agent.fake_agent.create_fake_agent import create_fake_agent
from auto_apply_app.interfaces.controllers.free_search_controller import FreeSearchController


def create_application(
    user_presenter: UserPresenter, 
    job_presenter: JobPresenter,
    sub_presenter: SubPresenter,
    agent_presenter: AgentPresenter,
    search_presenter: JobSearchPresenter,
    preferences_presenter: PreferencesPresenter,
    password_service: PasswordServicePort,
    token_provider: TokenProviderPort,
    file_storage_port: FileStoragePort,
    payment_port: PaymentPort,
    encryption_port: EncryptionServicePort,
    free_search_presenter: FreeSearchPresenter
) -> "Application":
    
    # Assembly line starts here - we now get a factory function for the UoW
    token_repo, uow_factory = create_repositories()    
    
    return Application(
        token_repo=token_repo,
        uow_factory=uow_factory,  # 🚨 Pass the factory here
        user_presenter=user_presenter,
        preference_presenter=preferences_presenter,
        job_presenter=job_presenter,
        search_presenter=search_presenter,
        agent_presenter=agent_presenter,
        sub_presenter=sub_presenter,
        password_service=password_service,
        token_provider=token_provider,
        file_storage_port=file_storage_port,
        payment_port=payment_port,
        encryption_port=encryption_port,
        free_search_presenter=free_search_presenter
    )

@dataclass
class Application:
    # Repositories & UoW Factory
    token_repo: TokenBlacklistRepository
    uow_factory: Callable[[], UnitOfWork]  # 🚨 Store the factory, not the instantiated UoW
    
    # Infrastructure Services (Ports)
    password_service: PasswordServicePort
    token_provider: TokenProviderPort
    file_storage_port: FileStoragePort
    payment_port: PaymentPort
    encryption_port: EncryptionServicePort
    
    # Presenters
    user_presenter: UserPresenter
    job_presenter: JobPresenter
    search_presenter: JobSearchPresenter
    sub_presenter: SubPresenter
    agent_presenter: AgentPresenter
    preference_presenter: PreferencesPresenter
    free_search_presenter: FreeSearchPresenter

    # =========================================================================
    # 🚀 CONTROLLER FACTORIES (Generated fresh per-request for thread safety)
    # =========================================================================

    @property
    def user_controller(self) -> UserController:
        uow = self.uow_factory()
        return UserController(
            get_user_use_case=GetUserUseCase(uow),
            update_user_use_case=UpdateUserUseCase(uow),
            delete_user_use_case=DeleteUserUseCase(uow),
            upload_resume_use_case=UploadUserResumeUseCase(uow, self.file_storage_port),
            presenter=self.user_presenter 
        )

    @property
    def auth_controller(self) -> AuthController:
        uow = self.uow_factory()
        return AuthController(
            register_use_case=RegisterUserUseCase(uow, self.password_service),
            login_use_case=LoginUserUseCase(self.password_service, self.token_provider, uow),
            logout_use_case=LogoutUseCase(self.token_provider, self.token_repo),
            change_password_use_case=ChangePasswordUseCase(self.password_service, uow),
            presenter=self.user_presenter
        )

    @property
    def subscription_controller(self) -> SubscriptionController:
        uow = self.uow_factory()
        return SubscriptionController(
            get_subscription_use_case=GetUserSubscriptionUseCase(uow),
            create_checkout_use_case=CreateCheckoutSessionUseCase(uow, self.payment_port),
            handle_webhook_use_case=HandlePaymentWebhookUseCase(uow, self.payment_port),
            get_portal_use_case=GetManagementPortalUseCase(uow, self.payment_port),
            presenter=self.sub_presenter 
        )

    @property
    def agent_controller(self) -> AgentController:
        uow = self.uow_factory()
        
        agent_service = create_agent(
            results_saver=SaveJobApplicationsUseCase(uow),
            consume_credits_use_case=ConsumeAiCreditsUseCase(uow),
            get_ignored_hashes_use_case=GetIgnoredHashesUseCase(uow),
            file_storage=self.file_storage_port,
            encryption_service=self.encryption_port,
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
            job_presenter=self.job_presenter
        )

    @property
    def job_offer_controller(self) -> JobOfferController:
        uow = self.uow_factory()
        return JobOfferController(
            get_analytics_use_case=GetApplicationAnalyticsUseCase(uow),
            get_user_applications_use_case=GetUserApplicationsUseCase(uow),
            toggle_interview_status_use_case=ToggleInterviewStatusUseCase(uow),
            toggle_response_status_use_case=ToggleResponseStatusUseCase(uow),
            job_offer_presenter=self.job_presenter            
        )

    @property
    def prefrences_controller(self) -> PreferencesController:
        uow = self.uow_factory()
        return PreferencesController(
            get_prefs_use_case=GetUserPreferencesUseCase(uow),
            update_prefs_use_case=UpdateUserPreferencesUseCase(uow, self.encryption_port),
            presenter=self.preference_presenter
        )

    @property
    def free_search_controller(self) -> FreeSearchController:
        return FreeSearchController(
            fake_agent=create_fake_agent(),
            presenter=self.free_search_presenter
        )