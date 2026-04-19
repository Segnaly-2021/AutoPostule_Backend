# auto_apply_app/infrastructures/agent/create_agent.py

import os

from auto_apply_app.infrastructures.agent.master.master_agent import MasterAgent
from auto_apply_app.infrastructures.agent.workers.apec.apec_worker import ApecWorker
from auto_apply_app.infrastructures.agent.workers.hellowork.hw_worker import HelloWorkWorker
from auto_apply_app.infrastructures.agent.workers.wttj.wttj_worker import WelcomeToTheJungleWorker

# Use Cases
from auto_apply_app.application.use_cases.agent_use_cases import (
    SaveJobApplicationsUseCase,
    ConsumeAiCreditsUseCase,
    GetIgnoredHashesUseCase
)
from auto_apply_app.application.use_cases.job_offer_use_cases import CleanupUnsubmittedJobsUseCase, GetDailyStatsUseCase
from auto_apply_app.application.use_cases.agent_state_use_cases import GetAgentStateUseCase, ResetAgentUseCase

# Ports
from auto_apply_app.application.service_ports.encryption_port import EncryptionServicePort
from auto_apply_app.application.service_ports.file_storage_port import FileStoragePort  




def create_agent(
    results_saver: SaveJobApplicationsUseCase,
    consume_credits_use_case: ConsumeAiCreditsUseCase,
    encryption_service: EncryptionServicePort,
    file_storage: FileStoragePort,
    get_ignored_hashes_use_case: GetIgnoredHashesUseCase,
    get_agent_state_use_case: GetAgentStateUseCase,                # 🚨 NEW
    reset_agent_state_use_case: ResetAgentUseCase,                 # 🚨 NEW
    cleanup_unsubmitted_use_case: CleanupUnsubmittedJobsUseCase,    # 🚨 NEW
    get_daily_stats_use_case: GetDailyStatsUseCase                 # 🚨 NEW
) -> MasterAgent:
    """
    Factory function to create the MasterAgent Singleton.
    
    This builds the infrastructure layer. User-specific settings (like 
    headless mode, temperature, and active boards) are NOT injected here.
    They are passed at runtime via the JobApplicationState.
    """
    
    print("[Agent Factory] Initializing Worker Infrastructure...")

    api_keys = {
        "gemini": os.getenv("GEMINI_API_KEY"),
        "openai": os.getenv("OPENAI_API_KEY"),
        "anthropic": os.getenv("ANTHROPIC_API_KEY"),
    }

    # 1. Instantiate Apec Worker
    apec_worker = ApecWorker(
       get_ignored_hashes=get_ignored_hashes_use_case,
       encryption_service=encryption_service,
       file_storage=file_storage,
       get_agent_state=get_agent_state_use_case # 🚨 Wired!
    )
    
    # 2. Instantiate HelloWork Worker
    hw_worker = HelloWorkWorker(
        get_ignored_hashes=get_ignored_hashes_use_case,
        encryption_service=encryption_service,
        file_storage=file_storage,
        get_agent_state=get_agent_state_use_case # 🚨 Wired!
    )
    
    # 3. Instantiate WTTJ Worker
    wttj_worker = WelcomeToTheJungleWorker(
        get_ignored_hashes=get_ignored_hashes_use_case,
        encryption_service=encryption_service,
        file_storage=file_storage,
        api_keys=api_keys,
        get_agent_state=get_agent_state_use_case # 🚨 Wired!
    )
    
    # 4. Return Master Agent
    return MasterAgent(
        wttj_worker=wttj_worker,
        hellowork_worker=hw_worker,
        apec_worker=apec_worker,
        api_keys=api_keys,
        file_storage=file_storage,
        consume_credits_use_case=consume_credits_use_case,
        save_applications_use_case=results_saver,
        cleanup_unsubmitted_use_case=cleanup_unsubmitted_use_case, # 🚨 Wired!
        get_agent_state=get_agent_state_use_case,                  # 🚨 Wired!
        reset_agent_state=reset_agent_state_use_case,               # 🚨 Wired!
        get_daily_stats=get_daily_stats_use_case                   # 🚨 Wired!
    )