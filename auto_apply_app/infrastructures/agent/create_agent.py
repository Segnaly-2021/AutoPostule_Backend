# auto_apply_app/infrastructures/agent/create_agent.py
import os

from auto_apply_app.infrastructures.agent.master.master_agent import MasterAgent
from auto_apply_app.infrastructures.agent.workers.apec.apec_worker import ApecWorker
from auto_apply_app.infrastructures.agent.workers.hellowork.hw_worker import HelloWorkWorker
from auto_apply_app.infrastructures.agent.workers.wttj.wttj_worker import WelcomeToTheJungleWorker
from auto_apply_app.infrastructures.agent.workers.teaser.teaser_worker import JobTeaserWorker

from auto_apply_app.application.use_cases.agent_use_cases import (
    SaveJobApplicationsUseCase,
    ConsumeAiCreditsUseCase,
    GetIgnoredHashesUseCase,
)
from auto_apply_app.application.use_cases.job_offer_use_cases import (
    CleanupUnsubmittedJobsUseCase,
    GetDailyStatsUseCase,
)
from auto_apply_app.application.use_cases.agent_state_use_cases import (
    GetAgentStateUseCase,
    BindAgentToSearchUseCase,
    IsAgentKilledForSearchUseCase,
)
from auto_apply_app.application.use_cases.agent_usage_use_cases import CompleteAgentRunUseCase
from auto_apply_app.application.use_cases.fingerprint_use_cases import GetOrCreateUserFingerprintUseCase

from auto_apply_app.application.service_ports.encryption_port import EncryptionServicePort
from auto_apply_app.application.service_ports.file_storage_port import FileStoragePort
from auto_apply_app.application.service_ports.proxy_service_port import ProxyServicePort


def create_agent(
    results_saver: SaveJobApplicationsUseCase,
    consume_credits_use_case: ConsumeAiCreditsUseCase,
    encryption_service: EncryptionServicePort,
    file_storage: FileStoragePort,
    get_ignored_hashes_use_case: GetIgnoredHashesUseCase,
    get_agent_state_use_case: GetAgentStateUseCase,
    bind_agent_to_search_use_case: BindAgentToSearchUseCase,           # NEW
    is_agent_killed_for_search_use_case: IsAgentKilledForSearchUseCase, # NEW
    complete_agent_run_use_case: CompleteAgentRunUseCase,               # NEW
    cleanup_unsubmitted_use_case: CleanupUnsubmittedJobsUseCase,
    get_daily_stats_use_case: GetDailyStatsUseCase,
    get_or_create_fingerprint_use_case: GetOrCreateUserFingerprintUseCase,
    proxy_service: ProxyServicePort,
) -> MasterAgent:

    api_keys = {
        "gemini": os.getenv("GEMINI_API_KEY"),
        "openai": os.getenv("OPENAI_API_KEY"),
        "anthropic": os.getenv("ANTHROPIC_API_KEY"),
    }

    # Workers — pass the new IsAgentKilledForSearchUseCase instead of GetAgentStateUseCase
    apec_worker = ApecWorker(
       get_ignored_hashes=get_ignored_hashes_use_case,
       encryption_service=encryption_service,
       file_storage=file_storage,
       is_agent_killed_for_search=is_agent_killed_for_search_use_case,  # CHANGED
    )
    
    hw_worker = HelloWorkWorker(
        get_ignored_hashes=get_ignored_hashes_use_case,
        encryption_service=encryption_service,
        file_storage=file_storage,
        is_agent_killed_for_search=is_agent_killed_for_search_use_case,  # CHANGED
    )

    wttj_worker = WelcomeToTheJungleWorker(
        get_ignored_hashes=get_ignored_hashes_use_case,
        encryption_service=encryption_service,
        file_storage=file_storage,
        api_keys=api_keys,
        is_agent_killed_for_search=is_agent_killed_for_search_use_case,  # CHANGED
    )
    
    teaser_worker = JobTeaserWorker(
        get_ignored_hashes=get_ignored_hashes_use_case,
        encryption_service=encryption_service,
        file_storage=file_storage,
        is_agent_killed_for_search=is_agent_killed_for_search_use_case,  # CHANGED
    )
    
    return MasterAgent(
        wttj_worker=wttj_worker,
        hellowork_worker=hw_worker,
        apec_worker=apec_worker,
        jobteaser_worker=teaser_worker,
        api_keys=api_keys,
        file_storage=file_storage,
        consume_credits_use_case=consume_credits_use_case,
        save_applications_use_case=results_saver,
        cleanup_unsubmitted_use_case=cleanup_unsubmitted_use_case,
        get_agent_state=get_agent_state_use_case,
        bind_agent_to_search=bind_agent_to_search_use_case,
        is_agent_killed_for_search=is_agent_killed_for_search_use_case,
        complete_agent_run=complete_agent_run_use_case,
        get_daily_stats=get_daily_stats_use_case,
        get_or_create_fingerprint=get_or_create_fingerprint_use_case,
        proxy_service=proxy_service,
    )