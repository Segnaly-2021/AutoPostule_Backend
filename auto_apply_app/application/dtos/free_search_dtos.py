# auto_apply_app/application/dtos/free_search_dtos.py
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class FreeSearchRequest:
    user_id: str
    query: str
    target_count: int

    def __post_init__(self):
        if not self.user_id:
            raise ValueError("user_id is required")
        if not self.query or not self.query.strip():
            raise ValueError("query cannot be empty")
        if self.target_count not in (10, 20, 50):
            raise ValueError("target_count must be 10, 20, or 50")

    def to_execution_params(self):
        return {
            "user_id": UUID(self.user_id),
            "query": self.query.strip(),
            "target_count": self.target_count,
        }