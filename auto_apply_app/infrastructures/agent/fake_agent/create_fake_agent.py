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