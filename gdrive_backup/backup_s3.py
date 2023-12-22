import os

from gdrive_backup.base_backup import BaseBackup
from django.core.files.storage import get_storage_class


class BackupS3(BaseBackup):

    def __init__(self, access_key_id, access_key, backup_dir, logger):
        super().__init__(backup_dir, logger)
        self.s3_storage = get_storage_class('storages.backends.s3boto3.S3Boto3Storage')(
            aws_access_key_id=access_key_id,
            aws_secret_access_key=access_key
        )

    def backup(self, prefix, destination):
        _, files = self.s3_storage.listdir(prefix)
        for file_name in files:
            s3_file_path = os.path.join(prefix, file_name)
            google_drive_path = os.path.join(destination, file_name)

            # Download from S3 and upload to Google Drive
            with self.s3_storage.open(s3_file_path, 'rb') as s3_file:
                content = s3_file.read()
                storages = self.get_storages()
                with storages.open(google_drive_path, 'wb') as google_drive_file:
                    google_drive_file.write(content)