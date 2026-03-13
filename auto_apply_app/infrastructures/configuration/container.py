from dataclasses import dataclass

# Repositories & UoW
# from auto_apply_app.application.repositories.user_repo import UserRepository
# from auto_apply_app.application.repositories.auth_repo import AuthRepository
# from auto_apply_app.application.repositories.job_offer_repo import JobOfferRepository
# from auto_apply_app.application.repositories.job_search_repo import JobSearchRepository
# from auto_apply_app.application.repositories.subscription_repo import SubscriptionRepository
from auto_apply_app.application.repositories.token_blacklist import TokenBlacklistRepository
from auto_apply_app.application.repositories.unit_of_work import UnitOfWork
from auto_apply_app.infrastructures.repository_factory import create_repositories
from auto_apply_app.infrastructures.agent.create_agent import create_agent

# Service Ports
from auto_apply_app.application.service_ports.password_service_port import PasswordServicePort
from auto_apply_app.application.service_ports.token_provider_port import TokenProviderPort
from auto_apply_app.application.service_ports.payment_port import PaymentPort
from auto_apply_app.application.service_ports.encryption_port import EncryptionServicePort
#from auto_apply_app.application.service_ports.agent_port import AgentServicePort
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
)
from auto_apply_app.application.use_cases.agent_use_cases import (
    ApproveJobUseCase,
    DiscardJobUseCase,
    GetIgnoredHashesUseCase,
    GetJobsForReviewUseCase,
    StartJobSearchAgentUseCase,
    ResumeJobApplicationUseCase,
    ProcessAgentResultsUseCase,
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
# from auto_apply_app.interfaces.controllers.job_search_controllers import JobSearchController


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
    payment_port: PaymentPort,
    encryption_port: EncryptionServicePort,
    free_search_presenter: FreeSearchPresenter
    
) -> "Application":
    
    # Assembly line starts here
    token_repo, uow = create_repositories()    
    
    return Application(
        token_repo=token_repo,
        uow=uow,
        user_presenter=user_presenter,
        preference_presenter=preferences_presenter,
        job_presenter=job_presenter,
        search_presenter=search_presenter,
        agent_presenter=agent_presenter,
        sub_presenter=sub_presenter,
        password_service=password_service,
        token_provider=token_provider,
        payment_port=payment_port,
        encryption_port=encryption_port,
        free_search_presenter=free_search_presenter
        
        
    )

@dataclass
class Application:
    # Repositories & UoW
    token_repo: TokenBlacklistRepository
    uow: UnitOfWork
    
    # Infrastructure Services (Ports)
    password_service: PasswordServicePort
    token_provider: TokenProviderPort
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

    def __post_init__(self) -> None:
        # --- 1. User Use Cases ---
        self.get_user_use_case = GetUserUseCase(self.uow)
        self.update_user_use_case = UpdateUserUseCase(self.uow)
        self.delete_user_use_case = DeleteUserUseCase(self.uow)

        # --- 2. Auth Use Cases ---
        self.register_user_use_case = RegisterUserUseCase(
            uow=self.uow, 
            password_service=self.password_service
        )
        
        self.login_user_use_case = LoginUserUseCase(
            password_service=self.password_service, 
            token_provider=self.token_provider,
            uow=self.uow
        )
        self.logout_use_case = LogoutUseCase(
            token_provider=self.token_provider, 
            token_blacklist_repo=self.token_repo
        )
        self.change_password_use_case = ChangePasswordUseCase(
            password_service=self.password_service,
            uow=self.uow
        )

        # --- 3. JobOffer Use Cases ---
        self.get_user_applications_use_case = GetUserApplicationsUseCase(uow=self.uow)
        self.toggle_response_status_use_case = ToggleResponseStatusUseCase(uow=self.uow)
        self.toggle_interview_status_use_case = ToggleInterviewStatusUseCase(uow=self.uow)
        self.get_analytics_use_case = GetApplicationAnalyticsUseCase(uow=self.uow)


        # --- 4. JobSearch / Agent Use Cases ---
        self.process_agent_results_use_case = ProcessAgentResultsUseCase(uow=self.uow)
        self.save_job_applications_use_case = SaveJobApplicationsUseCase(uow=self.uow)
        self.get_jobs_for_review_use_case = GetJobsForReviewUseCase(uow=self.uow)
        self.update_cover_letter_use_case = UpdateCoverLetterUseCase(uow=self.uow)    
        self.discard_job_use_case = DiscardJobUseCase(uow=self.uow)
        self.approve_job_use_case = ApproveJobUseCase(uow=self.uow)
        self.get_hashed_ignored_use_case = GetIgnoredHashesUseCase(uow=self.uow)
        self.consume_ai_credits_use_case = ConsumeAiCreditsUseCase(uow=self.uow)

        self.agent_service = create_agent(
            results_saver=self.save_job_applications_use_case,
            consume_credits_use_case=self.consume_ai_credits_use_case,
            get_ignored_hashes_use_case=self.get_hashed_ignored_use_case,
            encryption_service=self.encryption_port,
        )

        self.start_agent_use_case = StartJobSearchAgentUseCase(
            uow=self.uow,
            agent_service=self.agent_service
        )
        self.resume_agent_use_case = ResumeJobApplicationUseCase(
            uow=self.uow,
            agent_service=self.agent_service
        )
        self.kill_agent_use_case = KillJobSearchUseCase(
            uow=self.uow,
            agent_service=self.agent_service
        )
        
       

        # --- 5. Subscription Use Cases ---
        self.get_sub_use_case = GetUserSubscriptionUseCase(uow=self.uow)
        self.create_checkout_use_case = CreateCheckoutSessionUseCase(
            uow=self.uow, 
            payment_port=self.payment_port
        )
        self.handle_webhook_use_case = HandlePaymentWebhookUseCase(
            uow=self.uow, 
            payment_port=self.payment_port
        )

        self.get_mgmt_portal_use_case = GetManagementPortalUseCase(
            uow=self.uow,
            payment_port=self.payment_port
        )

        # 6 user preferences
        self.get_user_preferences_use_case = GetUserPreferencesUseCase(uow=self.uow)
        self.update_user_preferences_use_case = UpdateUserPreferencesUseCase(uow=self.uow, encryption_service=self.encryption_port)

        # --- Wiring Controllers ---
        self.user_controller = UserController(
            get_user_use_case=self.get_user_use_case,
            update_user_use_case=self.update_user_use_case,
            delete_user_use_case=self.delete_user_use_case,
            presenter=self.user_presenter 
        )

        self.auth_controller = AuthController(
            register_use_case=self.register_user_use_case,
            login_use_case=self.login_user_use_case,
            logout_use_case=self.logout_use_case,
            change_password_use_case=self.change_password_use_case,
            presenter=self.user_presenter
        )
        self.subscription_controller = SubscriptionController(
            get_subscription_use_case=self.get_sub_use_case,
            create_checkout_use_case=self.create_checkout_use_case,
            handle_webhook_use_case=self.handle_webhook_use_case,
            get_portal_use_case=self.get_mgmt_portal_use_case,
            presenter=self.sub_presenter  # Create this presenter
        )

        self.agent_controller = AgentController(
            start_agent_use_case=self.start_agent_use_case,
            resume_agent_use_case=self.resume_agent_use_case,
            kill_agent_use_case=self.kill_agent_use_case,
            get_jobs_for_review_use_case=self.get_jobs_for_review_use_case,
            update_cover_letter_use_case=self.update_cover_letter_use_case,
            approve_job_use_case=self.approve_job_use_case,
            discard_job_use_case=self.discard_job_use_case,
            presenter=self.agent_presenter,
            job_presenter=self.job_presenter
        )


        self.job_offer_controller = JobOfferController(
            get_analytics_use_case=self.get_analytics_use_case,
            get_user_applications_use_case=self.get_user_applications_use_case,
            toggle_interview_status_use_case=self.toggle_interview_status_use_case,
            toggle_response_status_use_case=self.toggle_response_status_use_case,
            job_offer_presenter=self.job_presenter            
        )

        self.prefrences_controller = PreferencesController(
            get_prefs_use_case=self.get_user_preferences_use_case,
            update_prefs_use_case=self.update_user_preferences_use_case,
            presenter=self.preference_presenter
        )

        self.fake_agent = create_fake_agent()
        self.free_search_controller = FreeSearchController(
            fake_agent=self.fake_agent,
            presenter=self.free_search_presenter
        )