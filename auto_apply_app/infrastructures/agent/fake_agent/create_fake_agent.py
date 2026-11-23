# ==============================================================================
# FILE: auto_apply_app/infrastructures/agent/fake_agent/create_fake_agent.py
#
# WHAT IT DOES:
#   STATUS: LIVE. Used by the free-search service (APP_ROLE=free_search).
#   Builds the FakeMasterAgent. Despite the word "singleton" in the docstring,
#   container.py calls this each time free_search_controller is read, so each
#   access gets a new agent with its own workers.
#
# USED BY:
#   - auto_apply_app/infrastructures/configuration/container.py: passes the agent to FreeSearchUseCase
#
# EXTERNAL DEPENDENCIES:
#   - None (standard library only); its workers use Playwright
# ==============================================================================
# auto_apply_app/infrastructures/agent/create_fake_agent.py

from auto_apply_app.infrastructures.agent.fake_agent.fake_master.fake_master_agent import FakeMasterAgent


def create_fake_agent() -> FakeMasterAgent:
    """
    Factory function to create the FakeMasterAgent singleton.
    
    No dependencies needed - the fake agent is completely self-contained.
    No database, no encryption, no user context.
    
    Returns:
        FakeMasterAgent: The orchestrator ready to search job boards.
    """
    print("[Fake Agent Factory] Initializing fake workers...")
    return FakeMasterAgent()