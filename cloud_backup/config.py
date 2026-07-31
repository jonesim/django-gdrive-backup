"""Named backup configurations.

settings.BACKUP_CONFIGS maps config names to dicts so one project can back up to
several destinations with different behaviour - e.g. an encrypted, object-locked
offsite store plus an unencrypted database copy for a staging server:

    BACKUP_CONFIGS = {
        'default': {
            'storage': {'backend': 's3', ..., 'lock': {...}},
            'encryption': True,
            'changed_files': 'protect',
        },
        'staging': {
            'storage': {'backend': 's3', 'bucket': 'staging-transfer', ...},
            'encryption': False,
            'dirs': [],          # database only
            'retention': [{'days': 1, 'number': 2}],
        },
    }

Config keys: storage (BACKUP_STORAGE-style dict), encryption (True = derive from the
encrypted-credentials SETTINGS_KEY, or a urlsafe-base64 32-byte key string), db
(include the database, default True), db_dir, dirs, s3_dirs, retention,
changed_files, db_tiers. A key absent from a named config inherits the corresponding legacy
global setting (BACKUP_STORAGE, BACKUP_ENCRYPTION, BACKUP_DIRS, ...), which is also
how installations without BACKUP_CONFIGS keep working unchanged - their globals
simply become the 'default' config.

The web UI and un-parameterised Celery tasks always use the default config; other
configs are reached with backup_website --config / restore_db --config or by
scheduling the tasks with kwargs={'config': 'staging'}.
"""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .db_tiers import DEFAULT_EXPIRE_DAYS
from .encryption import resolve_key
from .storages import check_storage_settings

DEFAULT_CONFIG = 'default'

CHANGED_OVERWRITE = 'overwrite'
CHANGED_PROTECT = 'protect'
CHANGED_HISTORY = 'history'


class BackupConfig:
    """Resolved settings for one backup destination. Every value falls back to the
    legacy global setting so BackupConfig() alone reproduces pre-BACKUP_CONFIGS
    behaviour exactly."""

    def __init__(self, name=DEFAULT_CONFIG, config=None):
        self.name = name
        config = config or {}
        self.storage_settings = config.get('storage',
                                           getattr(settings, 'BACKUP_STORAGE', None) or {'backend': 'gdrive'})
        check_storage_settings(self.storage_settings)
        self.root = self.storage_settings.get('root', getattr(settings, 'BACKUP_ROOT', 'django_backup'))
        self.include_db = config.get('db', True)
        self.db_dir = config.get('db_dir', getattr(settings, 'BACKUP_DB_DIR', self.root + '/db'))
        self.dirs = config.get('dirs', getattr(settings, 'BACKUP_DIRS', []))
        self.s3_dirs = config.get('s3_dirs', getattr(settings, 'S3_BACKUP_DIRS', []))
        self.retention = config.get('retention', getattr(settings, 'BACKUP_DB_RETENTION', None))
        db_tiers = config.get('db_tiers', getattr(settings, 'BACKUP_DB_TIERS', False))
        # True or a dict of options - only False/None turn it off
        self.db_tiers = db_tiers is not False and db_tiers is not None
        # how far back the web UI lists hourly dumps: a listing window, NOT a retention
        # setting - the bucket lifecycle rule is what deletes them
        tier_options = db_tiers if isinstance(db_tiers, dict) else {}
        self.db_tier_hourly_days = tier_options.get('hourly_days')
        # how long each tier should be kept, for the setup page to build lifecycle rules
        # from and check the real ones against - None keeps a tier indefinitely
        self.db_tier_expire_days = dict(DEFAULT_EXPIRE_DAYS, **(tier_options.get('expire_days') or {}))
        if self.db_tiers:
            unknown = set(self.db_tier_expire_days) - set(DEFAULT_EXPIRE_DAYS)
            if unknown:
                raise ImproperlyConfigured(f"db_tiers expire_days for backup config '{name}' has unknown "
                                           f'tier(s) {", ".join(sorted(unknown))} - expected '
                                           f'{", ".join(DEFAULT_EXPIRE_DAYS)}')
            if self.storage_settings.get('backend', 'gdrive') != 's3':
                raise ImproperlyConfigured(f"db_tiers for backup config '{name}' needs the 's3' storage backend - "
                                           f'the tiers are managed by bucket lifecycle rules')
            if self.retention:
                raise ImproperlyConfigured(
                    f"db_tiers for backup config '{name}' replaces client-side pruning, but retention is set"
                    f"{'' if 'retention' in config else ' (inherited from BACKUP_DB_RETENTION)'} - add "
                    f"'retention': [] to the config to let the bucket lifecycle rules do the pruning")
        # fails fast on a bad key before anything is backed up
        self.encryption_key = resolve_key(config.get('encryption',
                                                     getattr(settings, 'BACKUP_ENCRYPTION', None)))
        self.changed_files = config.get('changed_files',
                                        getattr(settings, 'BACKUP_CHANGED_FILES', CHANGED_OVERWRITE))
        if self.changed_files not in (CHANGED_OVERWRITE, CHANGED_PROTECT, CHANGED_HISTORY):
            raise ImproperlyConfigured(f"changed_files for backup config '{name}' must be one of "
                                       f'{CHANGED_OVERWRITE!r}, {CHANGED_PROTECT!r}, {CHANGED_HISTORY!r}')


def get_config(name=None):
    configs = getattr(settings, 'BACKUP_CONFIGS', None)
    if not configs:
        if name not in (None, DEFAULT_CONFIG):
            raise ImproperlyConfigured(f'BACKUP_CONFIGS is not defined so backup config {name!r} does not exist')
        return BackupConfig()
    if name is None:
        if DEFAULT_CONFIG in configs:
            name = DEFAULT_CONFIG
        elif len(configs) == 1:
            name = next(iter(configs))
        else:
            raise ImproperlyConfigured(f"Several BACKUP_CONFIGS and none named '{DEFAULT_CONFIG}' - "
                                       f'specify one of: {", ".join(configs)}')
    if name not in configs:
        raise ImproperlyConfigured(f'Unknown backup config {name!r} - available: {", ".join(configs)}')
    return BackupConfig(name, configs[name])
