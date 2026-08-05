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

The enhanced web UI shows a tab per config and every action works on the selected one -
it opens on the first config that includes the database, since a files-only destination
has no database page. The basic UI and un-parameterised Celery tasks still use the
default config; a named config is also reached with backup_website --config /
restore_db --config or by scheduling the tasks with kwargs={'config': 'staging'}.

The UI helpers at the end of this module never raise (a broken config has to be
describable, not fatal). Where the selection cannot travel as a name - modal slugs, which
django-modals splits on '-' - it travels as the config's index in config_names().
"""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .db_tiers import DEFAULT_EXPIRE_DAYS, DELETE_APP, DELETE_LIFECYCLE, DELETE_MODES, LOCK_MODES
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
        # who deletes the aged-out dumps, and which tiers get an object-lock retention
        # that nothing holding the backup credential can shorten
        self.db_tier_delete = tier_options.get('delete', DELETE_LIFECYCLE)
        self.db_tier_lock_days = tier_options.get('lock_days') or {}
        self.db_tier_lock_mode = tier_options.get('lock_mode', 'COMPLIANCE')
        if self.db_tiers:
            for option, values in (('expire_days', self.db_tier_expire_days),
                                   ('lock_days', self.db_tier_lock_days)):
                unknown = set(values) - set(DEFAULT_EXPIRE_DAYS)
                if unknown:
                    raise ImproperlyConfigured(f"db_tiers {option} for backup config '{name}' has unknown "
                                               f'tier(s) {", ".join(sorted(unknown))} - expected '
                                               f'{", ".join(DEFAULT_EXPIRE_DAYS)}')
            if self.db_tier_delete not in DELETE_MODES:
                raise ImproperlyConfigured(f"db_tiers delete for backup config '{name}' must be one of "
                                           f'{", ".join(repr(mode) for mode in DELETE_MODES)}')
            if self.db_tier_lock_mode not in LOCK_MODES:
                raise ImproperlyConfigured(f"db_tiers lock_mode for backup config '{name}' must be one of "
                                           f'{", ".join(repr(mode) for mode in LOCK_MODES)}')
            for tier, lock_days in self.db_tier_lock_days.items():
                expire_days = self.db_tier_expire_days.get(tier)
                if lock_days and expire_days and expire_days < lock_days:
                    # nothing could carry out that deletion: an object-lock retention
                    # cannot be shortened, and a lifecycle rule cannot delete through it
                    raise ImproperlyConfigured(
                        f"db_tiers for backup config '{name}' locks {tier} dumps for {lock_days} days but "
                        f'expires them after {expire_days} - they cannot be deleted before the lock ends')
            if self.storage_settings.get('backend', 'gdrive') != 's3':
                raise ImproperlyConfigured(f"db_tiers for backup config '{name}' needs the 's3' storage "
                                           f'backend')
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


def config_names():
    """Every configured destination, which is just the implicit default when
    BACKUP_CONFIGS is not used."""
    return list(getattr(settings, 'BACKUP_CONFIGS', None) or [DEFAULT_CONFIG])


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


def safe_config(name):
    """get_config() without the exception - None when the config does not exist or its
    settings are broken. The web UI lists every destination and must not fail on the one
    that needs fixing; storage_setup.check_config does the same for the setup page."""
    try:
        return get_config(name)
    except Exception:  # noqa: BLE001 - a broken config is exactly what is being described
        return None


def selected_config_name(name=None):
    """Which destination the web UI is working on: the requested one when it exists, else
    the first that includes the database - the UI opens on the database page, which has
    nothing to show for a files-only destination."""
    names = config_names()
    if name in names:
        return name
    for candidate in names:
        config = safe_config(candidate)
        if config is not None and config.include_db:
            return candidate
    return names[0]


def config_index(name):
    """Position of a config in config_names(). State that cannot carry the name safely
    uses this: django-modals splits a modal slug on '-', which a config name may contain."""
    names = config_names()
    return names.index(name) if name in names else 0


def config_at(index):
    """The config name for a config_index() value, or None (the default config) when there
    is no such position. Accepts the string a modal slug delivers."""
    if index is None or index == '':
        return None
    try:
        return config_names()[int(index)]
    except (TypeError, ValueError, IndexError):
        return None
