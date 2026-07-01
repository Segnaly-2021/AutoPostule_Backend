# auto_apply_app/infrastructures/agent/session/browser_session_store.py
"""
Best-effort durable cache of Playwright ``storage_state`` per (user, board) in GCS
(Phase C-2).

Lets a submit run reuse a logged-in browser session instead of re-authenticating
every time. It is NEVER a requirement: every method swallows its own errors and
degrades to "no session" / "skip save", so a storage problem can only ever cost a
re-login — never fail, block, or crash a run.

Talks to GCS directly (not through ``FileStoragePort``): the resume adapter binds
a single hard-coded bucket + ``resumes/`` prefix, so it can't address the separate
private session bucket. Mirrors that adapter's credential handling.
"""
import asyncio
import json
import logging
import os
from typing import Optional

from google.cloud import storage
from google.cloud.exceptions import NotFound

logger = logging.getLogger(__name__)


def _key(user_id, board: str) -> str:
    # One object per (user, board). Mirrors the workers' tmp/sessions naming.
    return f"sessions/{user_id}_{board}_session.json"


class BrowserSessionStore:
    """GCS-backed, per-(user, board) cache of Playwright ``storage_state``."""

    def __init__(self, bucket: str, encryptor=None):
        # bucket: the private session bucket (GCP_SESSION_BUCKET), separate from resumes.
        # encryptor: optional EncryptionService. Its encrypt/decrypt are ASYNC and operate
        #            on str (verified against the real EncryptionService), so we round-trip
        #            the storage_state JSON through text.
        self._encryptor = encryptor

        creds_json = os.getenv("GCP_CREDENTIALS")
        if creds_json:
            # Local dev: explicit SA JSON in the env, same as the resume adapter.
            self._client = storage.Client.from_service_account_info(json.loads(creds_json))
        else:
            # Cloud Run: native runtime service account.
            self._client = storage.Client()
        self._bucket = self._client.bucket(bucket)

    def _download_sync(self, blob_name: str) -> Optional[bytes]:
        try:
            return self._bucket.blob(blob_name).download_as_bytes()
        except NotFound:
            return None  # cold cache — expected, not an error

    def _upload_sync(self, blob_name: str, data: bytes) -> None:
        self._bucket.blob(blob_name).upload_from_string(data, content_type="application/json")

    async def load_to_local(self, user_id, board: str, dest_path: str) -> Optional[str]:
        """Download the stored session into ``dest_path``. Returns the path on success,
        or None (no session / any error) so the caller logs in fresh. Never raises."""
        try:
            data = await asyncio.to_thread(self._download_sync, _key(user_id, board))
            if not data:
                return None
            if self._encryptor is not None:
                data = (await self._encryptor.decrypt(data.decode("utf-8"))).encode("utf-8")
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with open(dest_path, "wb") as f:
                f.write(data)
            return dest_path
        except Exception:
            logger.warning("session load failed for %s/%s; logging in fresh", user_id, board, exc_info=True)
            return None

    async def save_from_local(self, user_id, board: str, local_path: str) -> None:
        """Upload the refreshed session. Best-effort — a save miss must not fail the run."""
        try:
            with open(local_path, "rb") as f:
                data = f.read()
            if self._encryptor is not None:
                data = (await self._encryptor.encrypt(data.decode("utf-8"))).encode("utf-8")
            await asyncio.to_thread(self._upload_sync, _key(user_id, board), data)
        except Exception:
            logger.warning("session save failed for %s/%s (non-fatal)", user_id, board, exc_info=True)

    @staticmethod
    def cleanup_local(local_path: Optional[str]) -> None:
        """Delete the local session file (holds auth cookies). Never raises."""
        if not local_path:
            return
        try:
            os.remove(local_path)
        except OSError:
            pass
