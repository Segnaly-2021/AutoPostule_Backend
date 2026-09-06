# auto_apply_app/application/service_ports/fingerprint_generator_port.py
from abc import ABC, abstractmethod
from typing import Sequence
from uuid import UUID

from auto_apply_app.domain.entities.user_fingerprint import UserFingerprint


class FingerprintGeneratorPort(ABC):
    """
    Interface for generating realistic browser fingerprints.
    The Infrastructure layer must implement this.

    Two distinct jobs, and the split is the whole point:

    - `generate_device` mints a *persona*: a coherent machine that gets persisted
      and reused. Randomly drawn, NOT derived from the user id — a fingerprint
      keyed to an identity can never change, which is the bug this replaces.
    - `derive_session_variant` produces the per-run copy. The device identity is
      untouched; only the volatile surface moves.
    """

    @abstractmethod
    def generate_device(
        self, user_id: UUID, existing: Sequence[UserFingerprint] = ()
    ) -> UserFingerprint:
        """
        Mints a new, internally coherent device persona for a user.

        Args:
            user_id: The user's UUID.
            existing: The user's current personas, so a second device doesn't
                come out a near-duplicate of the first.

        Returns:
            A fully-populated UserFingerprint entity (not yet persisted).
        """
        pass

    @abstractmethod
    def derive_session_variant(
        self, device: UserFingerprint, run_token: str
    ) -> UserFingerprint:
        """
        Returns an UNPERSISTED copy of `device` with the volatile surface
        re-rolled for one run: window size within the same screen, fresh
        canvas/audio noise seeds, and a Chrome major that drifts upward when
        the device is due for an update.

        Platform, GPU, core count, memory, locale and timezone MUST come
        through unchanged — those are the device, and a real one doesn't
        change them between sessions.

        Args:
            device: The persisted persona.
            run_token: Per-run identifier, mixed into the seeds so a resume of
                the same run reproduces the same variant.
        """
        pass
