# ==============================================================================
# FILE: auto_apply_app/application/service_ports/dispatch_port.py
#
# WHAT IT DOES:
#   Interface for launching an agent run outside the HTTP request, either as a
#   local asyncio task or as a separate Cloud Run Job. Only ids are passed.
#
# USED BY:
#   - auto_apply_app/application/use_cases/agent_use_cases.py: dispatches start and resume
#   - auto_apply_app/infrastructures/agent/dispatch/cloud_run_jobs_dispatcher.py: Cloud Run implementation
#   - auto_apply_app/infrastructures/agent/dispatch/local_dispatcher.py: in-process implementation
#   - auto_apply_app/infrastructures/configuration/container.py: selects the dispatcher
#
# EXTERNAL DEPENDENCIES:
#   - Google Cloud Run Jobs: used by the Cloud Run dispatcher (no import here)
# ==============================================================================
from abc import ABC, abstractmethod
from uuid import UUID


class DispatchPort(ABC):
    """
    Triggers an agent run out-of-band from the request that asked for it.

    Phase A: LocalDispatcher (in-process asyncio task).
    Phase B: CloudRunJobsDispatcher (triggers a Cloud Run Job with these ids as env).
    The id-only signature is deliberate: it is the contract that survives the move
    to a separate process (entities cannot cross to a Job).
    """

    @abstractmethod
    async def dispatch_start(self, search_id: UUID, user_id: UUID) -> None:
        ...

    @abstractmethod
    async def dispatch_resume(self, search_id: UUID, user_id: UUID, apply_all: bool) -> None:
        ...
