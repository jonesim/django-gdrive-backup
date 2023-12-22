import logging
from tempfile import gettempdir

from django.conf import settings
from encrypted_credentials import django_credentials
from .backup_db import BackupDb
from .backup_local_files import BackupLocal
from .backup_s3 import BackupS3
from .sql_functions import get_schemas

class Backup:

    def __init__(self, logger=None):
        self.logger = logger if logger else logging.getLogger(__name__)

    def get_backup_db(self, schema=None, table=None, sub_folder=None):
        cloud_storage_directory = getattr(settings, 'BACKUP_GDRIVE_DB', settings.BACKUP_STORAGE_DIR + '/db')
        if sub_folder:
            cloud_storage_directory += '/' + sub_folder
        elif schema:
            cloud_storage_directory += '/' + schema
        return BackupDb(cloud_storage_directory,
                        settings.DATABASES['default'],
                        getattr(settings, 'BACKUP_LOCAL_DB_DIR', gettempdir()),
                        self.logger,
                        schema=schema,
                        table=table)

    def backup_db_and_folders(self, schema=None, table=None, include_db=True, all_schemas=False,
                              include_folders=True, include_s3_folders=True, sub_folder=None):
        if include_db:
            schemas = [s[0] for s in get_schemas()] if all_schemas else [schema]
            for s in schemas:
                db = self.get_backup_db(s, table, sub_folder)
                db.backup_db_and_upload()
                if not sub_folder:
                    db.prune_old_backups(settings.BACKUP_DB_RETENTION)

        if include_folders and hasattr(settings, 'BACKUP_DIRS'):
            b = BackupLocal(settings.BACKUP_STORAGE_DIR, self.logger)
            for backup in settings.BACKUP_DIRS:
                b.backup_to_storages(*backup)

        if include_s3_folders and hasattr(settings, 'S3_BACKUP_DIRS'):
            s3_backup = BackupS3(settings.AWS_ACCESS_KEY_ID, settings.AWS_SECRET_ACCESS_KEY,
                                 settings.BACKUP_STORAGE_DIR,
                                 self.logger)
            for s3 in settings.S3_BACKUP_DIRS:
                s3_backup.backup(settings.AWS_PRIVATE_STORAGE_BUCKET_NAME, *s3)
