# auto_apply_app/infrastructures/agent/create_agent.py

import os

from auto_apply_app.infrastructures.agent.master.master_agent import MasterAgent
from auto_apply_app.infrastructures.agent.workers.apec.apec_worker import ApecWorker
from auto_apply_app.infrastructures.agent.workers.hellowork.hw_worker import HelloWorkWorker
from auto_apply_app.infrastructures.agent.workers.wttj.wttj_worker import WelcomeToTheJungleWorker

from auto_apply_app.application.use_cases.agent_use_cases import (
    SaveJobApplicationsUseCase,
    ConsumeAiCreditsUseCase,
)
from auto_apply_app.application.service_ports.encryption_port import EncryptionServicePort
from auto_apply_app.application.service_ports.file_storage_port import FileStoragePort  
from auto_apply_app.application.use_cases.agent_use_cases import GetIgnoredHashesUseCase

api_keys = {
    "gemini": os.getenv("GEMINI_API_KEY"),
    "openai": os.getenv("OPENAI_API_KEY"),
    "anthropic": os.getenv("ANTHROPIC_API_KEY"),
}


def create_agent(
    results_saver: SaveJobApplicationsUseCase,
    consume_credits_use_case: ConsumeAiCreditsUseCase,
    encryption_service: EncryptionServicePort,
    file_storage: FileStoragePort,
    get_ignored_hashes_use_case: GetIgnoredHashesUseCase
) -> MasterAgent:
    """
    Factory function to create the MasterAgent Singleton.
    
    This builds the infrastructure layer. User-specific settings (like 
    headless mode, temperature, and active boards) are NOT injected here.
    They are passed at runtime via the JobApplicationState.
    
    Args:
        results_processor: Use case for processing scraped jobs
        results_saver: Use case for saving applications
        encryption_service: Service for decrypting board credentials
    
    Returns:
        MasterAgent: The orchestrator ready to handle requests.
    """
    
    print("[Agent Factory] Initializing Worker Infrastructure...")

    # 1. Instantiate Apec Worker (Stateless)
    apec_worker = ApecWorker(
       get_ignored_hashes=get_ignored_hashes_use_case,
       encryption_service=encryption_service,
       file_storage=file_storage
    )
    
    # 2. Instantiate HelloWork Worker (Stateless)
    hw_worker = HelloWorkWorker(
        get_ignored_hashes=get_ignored_hashes_use_case,
        encryption_service=encryption_service,
        file_storage=file_storage
    )
    
    # 3. Instantiate WTTJ Worker (Stateless)
    wttj_worker = WelcomeToTheJungleWorker(
        get_ignored_hashes=get_ignored_hashes_use_case,
        encryption_service=encryption_service,
        file_storage=file_storage
    )
    
    # 4. Return Master Agent
    # The MasterAgent will use its routing logic to decide which worker 
    # to use based on the JobSearch entity passed at runtime.
    return MasterAgent(
        wttj_worker=wttj_worker,
        hellowork_worker=hw_worker,
        apec_worker=apec_worker,
        api_keys=api_keys,
        file_storage=file_storage,
        consume_credits_use_case=consume_credits_use_case,
        save_applications_use_case=results_saver

    )