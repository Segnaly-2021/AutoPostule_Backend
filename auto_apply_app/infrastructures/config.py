# ==============================================================================
# FILE: auto_apply_app/infrastructures/config.py
#
# WHAT IT DOES:
#   Central access to environment-based settings: storage mode (MEMORY or DATABASE),
#   database URL, secrets and the admin route prefix. It also builds the LangGraph
#   checkpointer, the component that saves agent state to Postgres so a paused run
#   can resume later. Importing this file loads a local .env file if there is one.
#
# USED BY:
#   - auto_apply_app/infrastructures/agent/master/master_agent.py: gets the checkpointer
#   - auto_apply_app/infrastructures/api/app.py: repository type, encryption key, admin prefix
#   - auto_apply_app/infrastructures/configuration/container.py: encryption key for workers
#   - auto_apply_app/infrastructures/repository_factory.py: chooses memory or database storage
#
# EXTERNAL DEPENDENCIES:
#   - python-dotenv: loads .env files
#   - langgraph (checkpoint-postgres): stores graph checkpoints
#   - psycopg_pool: connection pool for the checkpointer
#   - PostgreSQL (Supabase): database behind the checkpoint tables
# ==============================================================================
from dotenv import load_dotenv
from enum import Enum
from typing import Any, Optional
import pickle
import os

# --- LangGraph Imports ---
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

load_dotenv()


# ============================================================================
# CUSTOM SERIALIZER
# ============================================================================

# SECURITY: pickle.loads can execute code embedded in its input. This is only safe
# while the checkpoint tables can be written by this application alone.
# NOTE: possible issue: langgraph-checkpoint 3.x (pinned in requirements.txt) defines
# its serializer protocol with dumps_typed/loads_typed, which this class lacks. Confirm
# that checkpoint writes work against the installed version.
class PickleSerde:
    """
    A custom serializer that preserves rich Python objects
    (Dataclasses, Enums, UUIDs) so LangGraph doesn't flatten
    them into plain dictionaries when checkpointing state.
    """
    def dumps(self, obj: Any) -> bytes:
        return pickle.dumps(obj)

    def loads(self, data: bytes) -> Any:
        return pickle.loads(data)


# ============================================================================
# REPOSITORY TYPE
# ============================================================================

class RepositoryType(Enum):
    MEMORY = "memory"
    DATABASE = "database"


# ============================================================================
# CONFIG
# ============================================================================

class Config:
    """Application configuration."""

    DEFAULT_REPOSITORY_TYPE: RepositoryType = RepositoryType.MEMORY

    # Singleton pool — prevents creating thousands of connections
    _langgraph_db_pool = None

    @classmethod
    def get_repository_type(cls) -> RepositoryType:
        repo_type_str = os.getenv("REPOSITORY_TYPE", cls.DEFAULT_REPOSITORY_TYPE.value)
        try:
            return RepositoryType(repo_type_str.lower())
        except ValueError:
            raise ValueError(f"Invalid repository type: {repo_type_str}")

    @classmethod
    def get_database_url(cls) -> str:
        url = os.getenv("DATABASE_URL")
        if not url and cls.get_repository_type() == RepositoryType.DATABASE:
            raise ValueError("DATABASE_URL is required when RepositoryType is DATABASE")
        return url

    @classmethod
    async def get_checkpointer(cls):
        repo_type = cls.get_repository_type()

        if repo_type == RepositoryType.MEMORY:
            # NOTE: possible issue: LangGraph's in-memory saver is normally named
            # InMemorySaver/MemorySaver, so this import may fail in MEMORY mode.
            from langgraph.checkpoint.memory import AsyncMemorySaver
            return AsyncMemorySaver(serde=PickleSerde())

        elif repo_type == RepositoryType.DATABASE:
            # The checkpointer uses psycopg, not asyncpg, so it needs a plain
            # postgresql:// URL without SQLAlchemy's driver suffix.
            db_url = cls.get_database_url().replace("+asyncpg", "")

            if cls._langgraph_db_pool is None:
                # This pool is separate from the SQLAlchemy engine in session.py.
                # - open=False: psycopg pools should be opened with `await` (done below).
                # - check: tests a connection before lending it, replacing dead ones.
                # - autocommit: required by the Postgres checkpointer.
                # - prepare_threshold=None: disables prepared statements, which are not
                #   safe through a transaction-mode pooler such as Supavisor.
                # - keepalives*: TCP probes that help keep idle connections alive.
                cls._langgraph_db_pool = AsyncConnectionPool(
                    conninfo=db_url,
                    max_size=5,
                    num_workers=1,
                    open=False,
                    check=AsyncConnectionPool.check_connection,
                    kwargs={
                        "autocommit": True,
                        "prepare_threshold": None,
                        "keepalives": 1,
                        "keepalives_idle": 60,
                        "keepalives_interval": 10,
                        "keepalives_count": 5,
                    }
                )

            await cls._langgraph_db_pool.open()

            checkpointer = AsyncPostgresSaver(
                cls._langgraph_db_pool,
                serde=PickleSerde()  # ← preserves Enums, UUIDs, Dataclasses
            )

            print("🛠️ [Config] Ensuring LangGraph checkpoint tables exist in Supabase...")
            # setup() creates or migrates LangGraph's own tables. They are managed by
            # LangGraph rather than by this project's Alembic migrations.
            await checkpointer.setup()

            return checkpointer

        raise ValueError(f"No checkpointer implementation for {repo_type}")

    @classmethod
    def get_admin_route_prefix(cls) -> Optional[str]:
        """
        Mount prefix for the admin router, e.g. "/x7f2q9-internal-ops".

        Returns None when ADMIN_ROUTE_PREFIX is unset, in which case the admin router is
        not mounted at all — an unconfigured deployment exposes no admin surface.

        This is obscurity, not security. It raises the cost of stumbling onto the
        endpoint, but the authorization check behind it is what actually holds: the
        admin dependency re-reads is_admin from the database on every request and denies
        by default, so knowing this prefix buys an attacker nothing.
        """
        prefix = os.getenv("ADMIN_ROUTE_PREFIX")
        if not prefix:
            return None
        if not prefix.startswith("/"):
            prefix = "/" + prefix
        return prefix.rstrip("/")

    @classmethod
    def get_page_view_retention_days(cls) -> int:
        """How long raw page_views rows are kept before pruning."""
        return int(os.getenv("PAGE_VIEW_RETENTION_DAYS", "90"))

    @classmethod
    def get_gemini_key(cls) -> str:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise ValueError("GEMINI_API_KEY not found in environment variables.")
        return key

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
                "Generate one with: python -c "
                "'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )
        return key