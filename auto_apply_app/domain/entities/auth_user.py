from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID
from typing import Optional


from auto_apply_app.domain.entities.entity import Entity

@dataclass
class AuthUser(Entity):
    
    email: str
    password_hash: str
    user_id: UUID  # The link to the Domain User Profile
    
    
    is_active: bool = True
    is_verified: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_login: Optional[datetime] = None

    

    def change_password(self, new_password_hash: str):
        self.password_hash = new_password_hash
        self.updated_at = datetime.now(timezone.utc)

    def record_login(self):
        self.last_login = datetime.now(timezone.utc)