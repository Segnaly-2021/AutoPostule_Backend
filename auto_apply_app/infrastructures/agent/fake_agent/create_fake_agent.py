# auto_apply_app/infrastructures/agent/create_fake_agent.py

from typing import Optional

from auto_apply_app.application.service_ports.proxy_service_port import ProxyServicePort
from auto_apply_app.infrastructures.agent.fake_agent.fake_master.fake_master_agent import FakeMasterAgent


def create_fake_agent(proxy_service: Optional[ProxyServicePort] = None) -> FakeMasterAgent:
    """
    Factory function to create the FakeMasterAgent singleton.

    No database, no encryption, no user context. The only optional dependency
    is the proxy service, used by the APEC worker alone.

    Returns:
        FakeMasterAgent: The orchestrator ready to search job boards.
    """
    print("[Fake Agent Factory] Initializing fake workers...")
    return FakeMasterAgent(proxy_service=proxy_service)
