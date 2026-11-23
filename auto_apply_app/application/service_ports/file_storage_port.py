# ==============================================================================
# FILE: auto_apply_app/application/service_ports/file_storage_port.py
#
# WHAT IT DOES:
#   Interface for storing files, mainly resumes: upload, download and delete.
#
# USED BY:
#   - auto_apply_app/application/use_cases/user_use_cases.py: resume upload and deletion
#   - auto_apply_app/infrastructures/resume_storage/gcs_storage_adapter.py: implementation
#   - auto_apply_app/infrastructures/agent/create_agent.py: passes it to the agent
#   - auto_apply_app/infrastructures/agent/master/master_agent.py: reads resumes
#   - auto_apply_app/infrastructures/agent/workers/apec/apec_worker.py: storage access
#   - auto_apply_app/infrastructures/agent/workers/hellowork/hw_worker.py: storage access
#   - auto_apply_app/infrastructures/agent/workers/wttj/wttj_worker.py: storage access
#   - auto_apply_app/infrastructures/configuration/container.py: wires the adapter
#   - ... and 1 more
#
# EXTERNAL DEPENDENCIES:
#   - Google Cloud Storage: backing store for the adapter (no import here)
# ==============================================================================
from abc import ABC, abstractmethod

class FileStoragePort(ABC):
    """
    Interface for handling file storage operations (like resumes).
    """

    @abstractmethod
    async def upload_file(self, user_id: str, file_bytes: bytes, content_type: str, extension: str) -> str:
        """
        Uploads a file and returns its storage path/identifier.
        """
        pass

    @abstractmethod
    async def download_file(self, storage_path: str) -> bytes:
        """
        Downloads a file from storage and returns its raw bytes.
        """
        pass

    @abstractmethod
    async def delete_file(self, storage_path: str) -> None:
        """
        Deletes a file from storage.
        """
        pass