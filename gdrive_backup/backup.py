import logging
from tempfile import gettempdir

from django.conf import settings
from encrypted_credentials import django_credentials
from .backup_db import BackupDb
from .backup_local_files import BackupLocal
from django.db import connection
try:
    from .backup_s3 import BackupS3
except ImportError:
    # Allow for not using S3 and not installing boto3
    BackupS3 = None


class Backup:

    def __init__(self, logger=None):
        self.logger = logger if logger else logging.getLogger(__name__)

    def get_backup_db(self, schema=None):
        google_directory = getattr(settings, 'BACKUP_GDRIVE_DB', settings.BACKUP_GDRIVE_DIR + '/db')
        if schema:
            google_directory += '/' + schema
        return BackupDb(django_credentials.get_credentials('drive'),
                        google_directory,
                        settings.DATABASES['default'],
                        getattr(settings, 'BACKUP_LOCAL_DB_DIR', gettempdir()),
                        self.logger,
                        schema=schema)

    def backup_db_and_folders(self, schema=None, include_folders=True, include_s3_folders=True):
        if schema:
            logging.info(f'Schema {schema}')

        db = self.get_backup_db(schema=schema)
        db.backup_db_gdrive()

        db.prune_old_backups(settings.BACKUP_DB_RETENTION)
        if include_folders:
            if hasattr(settings, 'BACKUP_DIRS'):
                b = BackupLocal(django_credentials.get_credentials('drive'), settings.BACKUP_GDRIVE_DIR, self.logger)
                for backup in settings.BACKUP_DIRS:
                    b.backup_to_drive(*backup)

        if include_s3_folders:
            if hasattr(settings, 'S3_BACKUP_DIRS'):
                s3_backup = BackupS3(settings.AWS_ACCESS_KEY_ID, settings.AWS_SECRET_ACCESS_KEY,
                                     django_credentials.get_credentials('drive'),
                                     settings.BACKUP_GDRIVE_DIR,
                                     self.logger)
                for s3 in settings.S3_BACKUP_DIRS:
                    s3_backup.backup(settings.AWS_PRIVATE_STORAGE_BUCKET_NAME, *s3)

    def backup_db_all_schemas_and_folders(self, include_folders=True, include_s3_folders=True):
        with connection.cursor() as cursor:
            cursor.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name not like 'pg_%'")

            for row in cursor.fetchall():
                schema = row[0]
                self.backup_db_and_folders(schema=schema,
                                           include_folders=include_folders,
                                           include_s3_folders=include_s3_folders)
