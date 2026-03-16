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