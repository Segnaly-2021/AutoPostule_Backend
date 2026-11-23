# ==============================================================================
# FILE: auto_apply_app/domain/entities/entity.py
#
# WHAT IT DOES:
#   Base class for all domain entities. Gives each one a random UUID `id` and
#   defines identity-based equality and hashing (same id = same entity).
#   The id is excluded from __init__ (init=False), so it is always generated;
#   code that reloads an entity from the database has to set `id` afterwards.
#
# USED BY:
#   - auto_apply_app/domain/entities/user.py: base class
#   - auto_apply_app/domain/entities/job_search.py: base class
#   - auto_apply_app/domain/entities/job_offer.py: base class
#   - auto_apply_app/domain/entities/auth_user.py: base class
#   - auto_apply_app/domain/entities/agent_state.py: base class
#   - auto_apply_app/domain/entities/agent_usage.py: base class
#   - auto_apply_app/domain/entities/credit_transaction.py: base class
#   - auto_apply_app/domain/entities/user_fingerprint.py: base class
#   - ... and 6 more
#
# EXTERNAL DEPENDENCIES:
#   - None (standard library only)
# ==============================================================================
from dataclasses import dataclass, field
from uuid import UUID, uuid4

# NOTE: possible issue: every subclass uses plain @dataclass (eq=True), which
# replaces the __eq__ below with a compare-all-fields version and sets __hash__
# to None. So subclass instances are NOT compared by id and are unhashable.
@dataclass
class Entity:

	id: UUID = field(default_factory=uuid4, init=False)

	def __eq__(self, other: object) -> bool:
		if not isinstance(other, type(self)):
			return NotImplemented
		return self.id == other.id		
	
	def __hash__(self) -> int:
		return hash(self.id)
	
