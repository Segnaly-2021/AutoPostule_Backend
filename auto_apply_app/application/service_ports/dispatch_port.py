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
