import json
import os
import asyncio
from google.cloud import storage

from auto_apply_app.application.service_ports.file_storage_port import FileStoragePort

class GCSFileStorageAdapter(FileStoragePort):
    def __init__(self):
        bucket_name = os.getenv("GCP_RESUME_BUCKET")
        if not bucket_name:
            raise ValueError("GCP_RESUME_BUCKET environment variable is missing.")

        creds_json = os.getenv("GCP_CREDENTIALS")        
        
        if creds_json:
            # 💻 LOCAL DEV MODE: Parse the JSON string from your local .env file
            try:
                creds_dict = json.loads(creds_json)                
                self.client = storage.Client.from_service_account_info(creds_dict)
                print("☁️ GCS Adapter initialized using local JSON credentials.")
            except json.JSONDecodeError as e:
                raise ValueError(f"CRITICAL: Failed to parse GCP_CREDENTIALS JSON. {e}")
        else:
            # 🚀 PRODUCTION MODE (Cloud Run): Automatically uses the native Service Account!
            self.client = storage.Client()
            print("☁️ GCS Adapter initialized using native Cloud Run Service Account.")

        self.bucket = self.client.bucket(bucket_name)

    def _upload_sync(self, blob_name: str, file_bytes: bytes, content_type: str) -> str:
        blob = self.bucket.blob(blob_name)
        blob.upload_from_string(file_bytes, content_type=content_type)
        return blob.name

    def _download_sync(self, blob_name: str) -> bytes:
        blob = self.bucket.blob(blob_name)
        return blob.download_as_bytes()

    def _delete_sync(self, blob_name: str) -> None:
        blob = self.bucket.blob(blob_name)
        if blob.exists():
            blob.delete()

    async def upload_file(self, user_id: str, file_bytes: bytes, content_type: str, extension: str) -> str:
        # Standardize path: resumes/user_id.pdf (automatically overwrites old ones)
        blob_name = f"resumes/{user_id}.{extension.replace('.', '')}"
        
        # Run synchronous GCP call in a thread
        path = await asyncio.to_thread(self._upload_sync, blob_name, file_bytes, content_type)
        return path

    async def download_file(self, storage_path: str) -> bytes:
        return await asyncio.to_thread(self._download_sync, storage_path)

    async def delete_file(self, storage_path: str) -> None:
        await asyncio.to_thread(self._delete_sync, storage_path)