import logging
from tempfile import gettempdir

from django.conf import settings
from .backup_db import BackupDb
from .backup_local_files import BackupLocal
from .sql_functions import get_schemas
from .storages import get_storage, backup_root

try:
    from .backup_s3 import BackupS3
except ImportError:
    # Allow for not using S3 and not installing boto3
    BackupS3 = None


class Backup:

    def __init__(self, logger=None):
        self.logger = logger if logger else logging.getLogger(__name__)
        self._storage = None

    @property
    def storage(self):
        if self._storage is None:
            self._storage = get_storage()
        return self._storage

    def get_backup_db(self, schema=None, table=None, sub_folder=None):
        backup_directory = getattr(settings, 'BACKUP_GDRIVE_DB', backup_root() + '/db')
        if sub_folder:
            backup_directory += '/' + sub_folder
        elif schema:
            backup_directory += '/' + schema
        return BackupDb(self.storage,
                        backup_directory,
                        settings.DATABASES['default'],
                        getattr(settings, 'BACKUP_LOCAL_DB_DIR', gettempdir()),
                        self.logger,
                        schema=schema,
                        table=table,
                        exclude_tables=getattr(settings, 'BACKUP_EXCLUDE_TABLES', None),
                        exclude_table_data=getattr(settings, 'BACKUP_EXCLUDE_TABLE_DATA', None))

    def backup_db_and_folders(self, schema=None, table=None, include_db=True, all_schemas=False,
                              include_folders=True, include_s3_folders=True, sub_folder=None):
        if include_db:
            schemas = [s[0] for s in get_schemas()] if all_schemas else [schema]
            for s in schemas:
                db = self.get_backup_db(s, table, sub_folder)
                db.backup_db_to_storage()
                if not sub_folder:
                    db.prune_old_backups(settings.BACKUP_DB_RETENTION)

        if include_folders and hasattr(settings, 'BACKUP_DIRS'):
            b = BackupLocal(self.storage, backup_root(), self.logger)
            for backup in settings.BACKUP_DIRS:
                b.backup_folder(*backup)

        if include_s3_folders and hasattr(settings, 'S3_BACKUP_DIRS'):
            s3_backup = BackupS3(settings.AWS_ACCESS_KEY_ID, settings.AWS_SECRET_ACCESS_KEY,
                                 self.storage,
                                 backup_root(),
                                 self.logger)
            for s3 in settings.S3_BACKUP_DIRS:
                s3_backup.backup(settings.AWS_PRIVATE_STORAGE_BUCKET_NAME, *s3)
