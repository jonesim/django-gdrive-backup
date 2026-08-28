import logging
from tempfile import gettempdir

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from .backup_db import BackupDb
from .backup_local_files import BackupLocal
from .config import BackupConfig, CHANGED_PROTECT, FileSource, get_config
from .db_tiers import DEFAULT_CATCHUP_DAYS, DELETE_APP, DbTierPromoter, TIER_DIRS
from .sql_functions import get_schemas
from .storages import get_storage

try:
    from .backup_s3 import BackupS3
except ImportError:
    # Allow for not using S3 and not installing boto3
    BackupS3 = None

try:
    from .backup_azure import BackupAzure
except ImportError:
    # likewise azure-storage-blob, only needed for azure_dirs (or an azure destination)
    BackupAzure = None


class ChangedFilesError(Exception):
    """Source files no longer match their existing backups while
    BACKUP_CHANGED_FILES = 'protect' - possible ransomware or corruption. The rest of
    the backup completed before this was raised."""


class RestoreOnlyConfig(Exception):
    """A write was asked for against a config marked restore_only - the destination holds
    another machine's backups and this one only reads from it."""


class Backup:

    def __init__(self, logger=None, config=None):
        """config: a BACKUP_CONFIGS name, a BackupConfig instance, or None for the
        default config (the legacy global settings when BACKUP_CONFIGS is not used)"""
        self.logger = logger if logger else logging.getLogger(__name__)
        self.config = config if isinstance(config, BackupConfig) else get_config(config)
        self._storage = None

    @property
    def storage(self):
        if self._storage is None:
            self._storage = get_storage(self.config.storage_settings)
        return self._storage

    def check_writable(self):
        """Every path that changes the destination starts here. Restoring does not: it
        goes through get_backup_db(), which only reads."""
        if self.config.restore_only:
            raise RestoreOnlyConfig(f"Backup config {self.config.name!r} is restore_only - nothing is "
                                    f'written to this destination')

    def empty_trash(self):
        self.check_writable()
        self.storage.empty_trash()

    def get_backup_db(self, schema=None, table=None, sub_folder=None):
        backup_directory = self.config.db_dir
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
                        exclude_table_data=getattr(settings, 'BACKUP_EXCLUDE_TABLE_DATA', None),
                        config=self.config)

    def get_file_backup(self, kind=FileSource.LOCAL):
        """The folder backup for a FileSource kind - BackupLocal for a directory,
        BackupAzure for a prefix of the config's azure_source container. Both take
        (source, dest_name) in backup_folder / verify_folder, so callers loop over
        config.file_sources without caring which."""
        if kind == FileSource.AZURE:
            if BackupAzure is None:
                raise ImproperlyConfigured('azure_dirs needs the azure-storage-blob package: '
                                           'pip install django-cloud-backup[azure]')
            return BackupAzure(self.config.azure_source, self.storage, self.config.root, self.logger,
                               config=self.config)
        return BackupLocal(self.storage, self.config.root, self.logger, config=self.config)

    def backup_db_and_folders(self, schema=None, table=None, include_db=True, all_schemas=False,
                              include_folders=True, include_s3_folders=True, sub_folder=None,
                              backup_dir=None):
        """:param backup_dir: index into config.file_sources, to back up one configured
        folder (local directory or Azure prefix) rather than all of them - the same index
        the file browser urls use
        """
        self.check_writable()
        changed_files = []
        sources = self.config.file_sources
        if backup_dir is not None and not 0 <= backup_dir < len(sources):
            # checked up front so an index into a config with no dirs at all is an
            # error rather than a run that silently backs nothing up
            raise IndexError(f'No backup directory {backup_dir} in config {self.config.name!r}')
        if include_db and self.config.include_db:
            schemas = [s[0] for s in get_schemas()] if all_schemas else [schema]
            for s in schemas:
                db = self.get_backup_db(s, table, sub_folder)
                db.backup_db_to_storage()
                if not sub_folder and self.config.retention:
                    db.prune_old_backups(self.config.retention)
            if not sub_folder and self.config.db_tiers:
                # promote here as well as on a schedule: the hourly tier expires by
                # itself, so a promotion job that silently stops running loses every
                # backup once the lifecycle rule catches up. Whatever else is broken,
                # a run that just uploaded a dump can promote yesterday's
                self.promote_db_tiers(resume=True, warn_empty=False)

        if include_folders and sources:
            if backup_dir is not None:
                sources = [sources[backup_dir]]
            backups = {}
            for source in sources:
                if source.kind not in backups:
                    backups[source.kind] = self.get_file_backup(source.kind)
                backups[source.kind].backup_folder(source.source, source.dest_name)
            for b in backups.values():
                changed_files += b.changed_files

        # backup_dir names a folder source, so an S3 source can never be what was asked for
        if include_s3_folders and self.config.s3_dirs and backup_dir is None:
            s3_backup = BackupS3(settings.AWS_ACCESS_KEY_ID, settings.AWS_SECRET_ACCESS_KEY,
                                 self.storage,
                                 self.config.root,
                                 self.logger,
                                 config=self.config)
            for s3 in self.config.s3_dirs:
                s3_backup.backup(settings.AWS_PRIVATE_STORAGE_BUCKET_NAME, *s3)
            changed_files += s3_backup.changed_files

        if changed_files:
            summary = ', '.join(changed_files[:5]) + ('...' if len(changed_files) > 5 else '')
            self.logger.warning(f'{len(changed_files)} source files changed since being backed up: {summary}')
            if self.config.changed_files == CHANGED_PROTECT:
                # raised after everything else has completed so the db dump and all
                # unchanged files are safely backed up before the run is marked failed
                raise ChangedFilesError(f'{len(changed_files)} source files changed since being backed up '
                                        f'and were NOT backed up: {summary}')

    def promote_db_tiers(self, as_of=None, days=None, resume=False, warn_empty=True):
        """Promote database dumps from the hourly tier into daily and monthly with
        server-side copies, so the bucket's lifecycle rules can keep them for different
        lengths of time. Needs no database, and runs at the end of every db backup as
        well as from the scheduled task - see backup_db_and_folders.

        :param resume: cheap mode for the in-backup call - see DbTierPromoter.promote
        """
        self.check_writable()
        if not self.config.db_tiers:
            self.logger.info('db_tiers is not enabled for this config - nothing to promote')
            return
        try:
            db_folder = self.storage.ensure_folder(self.config.db_dir)
            # sub-folders come from the storage rather than get_schemas() so dropped
            # schemas and -sub_folder destinations are promoted too
            folders = [db_folder] + [f for f in self.storage.list_folders(db_folder)
                                     if f['name'] not in TIER_DIRS]
        except Exception as e:  # noqa: BLE001 - never fail a completed backup over this
            self.logger.warning(f'Could not promote backup tiers: {e}')
            return
        stats = {'daily': 0, 'monthly': 0, 'skipped': 0, 'deleted': 0, 'empty_days': [], 'errors': []}
        for folder in folders:
            promoter = DbTierPromoter(self.storage, folder, self.logger,
                                      lock_days=self.config.db_tier_lock_days,
                                      lock_mode=self.config.db_tier_lock_mode)
            promoter.promote(as_of=as_of, days=days or DEFAULT_CATCHUP_DAYS,
                             resume=resume, warn_empty=warn_empty)
            if self.config.db_tier_delete == DELETE_APP:
                # only after promotion: an hourly dump that has not been copied into the
                # daily tier yet must not be deleted for being old
                promoter.prune(as_of=as_of, expire_days=self.config.db_tier_expire_days)
            for key, value in promoter.stats.items():
                stats[key] = stats[key] + value
        if stats['daily'] or stats['monthly'] or stats['deleted'] or stats['errors'] or not resume:
            # the in-backup call runs every time and usually has nothing to say
            self.logger.info(f"Backup tiers: {stats['daily']} promoted to daily, "
                             f"{stats['monthly']} to monthly, {stats['skipped']} already promoted"
                             + (f", {stats['deleted']} deleted" if stats['deleted'] else ''))
        for error in stats['errors']:
            self.logger.warning(f'Not promoted: {error}')
        return stats

    def extend_file_retention(self, workers=8):
        """Ensure everything under the backup root keeps at least the configured
        min_days of object-lock retention. Costs 1-2 API calls per file - schedule
        daily rather than running with every backup."""
        self.check_writable()
        min_days = self.storage.lock.get('min_days')
        if not min_days:
            self.logger.info('No object-lock min_days configured - nothing to extend')
            return
        stats = self.storage.extend_retention(self.storage.ensure_folder(self.config.root), min_days,
                                              workers=workers)
        if stats is None:
            self.logger.info('This storage backend does not support object-lock retention')
            return
        self.logger.info(f"Object-lock retention: {stats['checked']} files checked, "
                         f"{stats['extended']} extended to {min_days} days")
        for error in stats['errors']:
            self.logger.warning(f'Retention not extended: {error}')
        return stats
