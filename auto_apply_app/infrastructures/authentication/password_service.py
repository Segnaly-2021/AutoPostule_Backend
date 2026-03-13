from passlib.context import CryptContext
from auto_apply_app.application.service_ports.password_service_port import PasswordServicePort

class PasswordService(PasswordServicePort):
    """
    Concrete implementation of PasswordServicePort using the Argon2id algorithm.
    This belongs in the Infrastructure Layer because it relies on external libraries (passlib).
    """

    def __init__(self):
        # We configure passlib to use "argon2".
        # passlib automatically defaults this to the secure "argon2id" variant.
        # deprecated="auto" ensures that if we change schemes in the future, 
        # old hashes can still be verified but will be marked for upgrade.
        self._pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

    def get_password_hash(self, password: str) -> str:
        """
        Hashes the password using Argon2id.
        Returns a string starting with $argon2id$...
        """
        return self._pwd_context.hash(password)

    def verify(self, plain_password: str, hashed_password: str) -> bool:
        """
        Verifies a plain password against the stored hash.
        """
        return self._pwd_context.verify(plain_password, hashed_password)