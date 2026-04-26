# auto_apply_app/application/service_ports/fingerprint_generator_port.py
from abc import ABC, abstractmethod
from uuid import UUID

from auto_apply_app.domain.entities.user_fingerprint import UserFingerprint


class FingerprintGeneratorPort(ABC):
    """
    Interface for generating realistic browser fingerprints.
    The Infrastructure layer must implement this.
    """

    @abstractmethod
    def generate_for_user(self, user_id: UUID) -> UserFingerprint:
        """
        Generates a deterministic, realistic browser fingerprint for a user.
        Same user_id must always produce the same fingerprint.
        
        Args:
            user_id: The user's UUID.
            
        Returns:
            A fully-populated UserFingerprint entity (not yet persisted).
        """
        pass