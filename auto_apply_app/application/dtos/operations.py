# ==============================================================================
# FILE: auto_apply_app/application/dtos/operations.py
#
# WHAT IT DOES:
#   Shared result type for delete operations. Gives Result.success a real value,
#   because Result does not allow a success of None.
#
# USED BY:
#   - auto_apply_app/application/use_cases/job_offer_use_cases.py: delete result
#   - auto_apply_app/application/use_cases/job_search_use_cases.py: delete result
#   - auto_apply_app/application/use_cases/user_use_cases.py: delete result
#   - auto_apply_app/interfaces/controllers/job_search_controllers.py: result type
#
# EXTERNAL DEPENDENCIES:
#   - None (standard library only)
# ==============================================================================
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class DeletionOutcome:
    """Represents the result of a deletion operation."""

    entity_id: UUID

    def __str__(self) -> str:
        return f"Successfully deleted entity with ID: {self.entity_id}"