from dotenv import load_dotenv
from enum import Enum
import os

# --- LangGraph Imports ---
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres import PostgresSaver 
from psycopg_pool import ConnectionPool

# Load environment variables from .env file
load_dotenv()


# Repository types
class RepositoryType(Enum):
    MEMORY = "memory"
    DATABASE = "database"


class Config:
    """Application configuration."""

    # Default values
    DEFAULT_REPOSITORY_TYPE: RepositoryType = RepositoryType.MEMORY
    
    @classmethod
    def get_repository_type(cls) -> RepositoryType:
        """Get the configured repository type."""
        repo_type_str = os.getenv("REPOSITORY_TYPE", cls.DEFAULT_REPOSITORY_TYPE.value)
        try:
            return RepositoryType(repo_type_str.lower())
        except ValueError:
            raise ValueError(f"Invalid repository type: {repo_type_str}")
        
    @classmethod
    def get_database_url(cls) -> str:
        """Get the database connection string."""
        url = os.getenv("DATABASE_URL")
        if not url and cls.get_repository_type() == RepositoryType.DATABASE:
            raise ValueError("DATABASE_URL is required when RepositoryType is DATABASE")
        return url

    @classmethod
    def get_checkpointer(cls):
        """
        Factory method to return the appropriate LangGraph checkpointer.
        """
        repo_type = cls.get_repository_type()

        if repo_type == RepositoryType.MEMORY:
            # Ephemeral checkpointer (Lost on restart)
            return MemorySaver()

        elif repo_type == RepositoryType.DATABASE:
            # Persistent checkpointer (Survives restarts)
            # Create connection pool
            # In a real app, this pool should be managed (opened/closed) at the app lifecycle level
            db_url = cls.get_database_url()
            connection_kwargs = {
                "autocommit": True,
                "prepare_threshold": 0,
            }
            
            pool = ConnectionPool(
                conninfo=db_url,
                max_size=20,
                kwargs=connection_kwargs,
            )
            
            # The checkpointer automatically creates the necessary tables on first run
            return PostgresSaver(pool)

        raise ValueError(f"No checkpointer implementation for {repo_type}")

    @classmethod
    def get_gemini_key(cls) -> str:
        try:
            key = os.getenv("GEMINI_API_KEY")
            return key
        except ValueError as e:
            raise ValueError(f"Error GEMINI KEY: {e}")
        


    @classmethod
    def get_encryption_key(cls) -> str:
        """
        Get the encryption key for credential storage.
        
        SECURITY NOTE: This key MUST be:
        1. Set in environment variables (never hardcoded)
        2. The same across deployments (or old credentials can't be decrypted)
        3. Kept secret (if compromised, all credentials are exposed)
        """
        key = os.getenv("ENCRYPTION_KEY")
        if not key:
            raise ValueError(
                "ENCRYPTION_KEY not found in environment variables. "
                "Generate one with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )
        return key