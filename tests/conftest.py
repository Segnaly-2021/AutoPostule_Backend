"""Shared fixtures.

REPOSITORY_TYPE is forced to memory so nothing here can reach the real database.
"""
import os

os.environ.setdefault("REPOSITORY_TYPE", "memory")
# Keep pacing arithmetic predictable: a stray value in the developer's .env
# would otherwise change what the tier assertions are measuring.
os.environ["AGENT_HUMAN_PACE"] = "1.0"
os.environ["AGENT_HUMAN_BUDGET_S"] = "6300"

import pytest


@pytest.fixture
def generator():
    from auto_apply_app.infrastructures.agent.fingerprint_generator import FingerprintGenerator
    return FingerprintGenerator()
