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
(include the database, default True), db_dir, dirs, azure_dirs, azure_source, s3_dirs,
retention, changed_files, db_tiers, restore_only, status. A key absent from a named config
inherits the corresponding legacy global setting (BACKUP_STORAGE, BACKUP_ENCRYPTION,
BACKUP_DIRS, ...), which is also how installations without BACKUP_CONFIGS keep working
unchanged - their globals simply become the 'default' config.

dirs and azure_dirs are the folder backups - local directories and prefixes of an Azure
container (media that django-storages keeps in Azure has no local directory to back
up). Together they are BackupConfig.file_sources, which the file browser, the
Backup <folder> button and backup_website --backup_dir index into: local entries first,
so an installation with only BACKUP_DIRS keeps its indices. azure_source (container plus
connection_string, or account_url and credential - the azure destination backend's keys)
says which container; when it is not set the project's own django-storages Azure
settings are used, so media in Azure needs nothing more than AZURE_BACKUP_DIRS.

restore_only marks a destination this installation reads and never writes: another
machine's backups, e.g. a staging server restoring the live server's dumps out of the
bucket the live server backs up to. Every write refuses (Backup.check_writable), the web UI
offers no backup actions for it, and the setup page generates a read-only key. It is a
config setting rather than a credential because machines commonly share one encrypted
settings file, so the same credential is present on all of them and only the config can
tell the roles apart.

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

from .db_tiers import (DEFAULT_EXPIRE_DAYS, DEFAULT_PURGE_DAYS, DELETE_APP, DELETE_LIFECYCLE, DELETE_MODES,
                       LOCK_MODES)
from .encryption import resolve_key
from .storages import check_storage_settings

DEFAULT_CONFIG = 'default'

# Thresholds the status check (status.py) grades a destination against; a config's
# 'status' dict overrides any of them, or BACKUP_STATUS for the legacy globals.
DEFAULT_STATUS = {
    'grace_minutes': 30,        # how late a scheduled run may be before it counts as missed
    'max_age_hours': 24,        # newest dump / run allowed this old when there is no beat schedule
    'size_drop': 0.8,           # a dump smaller than this fraction of the previous one is suspect
    'promotion_deadline': '06:30',  # local time by which yesterday's daily copy must exist
    'stuck_hours': 6,           # a run still marked running after this long has died
}

CHANGED_OVERWRITE = 'overwrite'
CHANGED_PROTECT = 'protect'
CHANGED_HISTORY = 'history'


class FileSource:
    """One folder backup: where it comes from and the destination folder it is stored in.
    kind is LOCAL (source is a directory path) or AZURE (source is a blob prefix in the
    config's azure_source container, '' for the whole container)."""

    LOCAL = 'local'
    AZURE = 'azure'

    def __init__(self, kind, source, dest_name):
        self.kind = kind
        self.source = source
        self.dest_name = dest_name

    def __repr__(self):
        return f'FileSource({self.kind!r}, {self.source!r}, {self.dest_name!r})'


def default_azure_source():
    """The Azure container the project's default file storage lives in, read from the
    django-storages settings (STORAGES['default'] OPTIONS, falling back to the AZURE_*
    settings as django-storages itself does), or None when the default storage is not
    Azure. Lets media stored through django-storages be backed up with just
    AZURE_BACKUP_DIRS - the credentials are already in the settings."""
    default = (getattr(settings, 'STORAGES', None) or {}).get('default') or {}
    backend = default.get('BACKEND') or getattr(settings, 'DEFAULT_FILE_STORAGE', '') or ''
    if 'azure' not in backend.lower():
        return None
    options = default.get('OPTIONS') or {}

    def option(name):
        return options.get(name, getattr(settings, 'AZURE_' + name.upper(), None))

    container = option('azure_container') or getattr(settings, 'AZURE_CONTAINER', None)
    if not container:
        return None
    if option('connection_string'):
        return {'container': container, 'connection_string': option('connection_string')}
    account_name = option('account_name')
    if not account_name:
        return None
    suffix = option('endpoint_suffix') or 'core.windows.net'
    credential = option('sas_token') or option('account_key') or option('token_credential')
    return {'container': container, 'account_url': f'https://{account_name}.blob.{suffix}',
            'credential': credential}


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
        # read from, never written to - see the module docstring
        self.restore_only = config.get('restore_only', getattr(settings, 'BACKUP_RESTORE_ONLY', False))
        self.db_dir = config.get('db_dir', getattr(settings, 'BACKUP_DB_DIR', self.root + '/db'))
        self.dirs = config.get('dirs', getattr(settings, 'BACKUP_DIRS', []))
        # (blob prefix, destination folder) pairs in the azure_source container
        self.azure_dirs = config.get('azure_dirs', getattr(settings, 'AZURE_BACKUP_DIRS', []))
        self.azure_source = config.get('azure_source', getattr(settings, 'AZURE_BACKUP_SOURCE', None))
        if self.azure_dirs and not self.azure_source:
            self.azure_source = default_azure_source()
            if not self.azure_source:
                raise ImproperlyConfigured(
                    f"azure_dirs for backup config '{name}' but no azure_source: set AZURE_BACKUP_SOURCE "
                    f"(or the config's azure_source) to {{'container': ..., 'connection_string': ...}} "
                    f'- it is only implied when the default file storage is django-storages Azure')
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
        # how long the bucket keeps a hidden version before purging it - the undo window
        self.db_tier_purge_days = tier_options.get('purge_days', DEFAULT_PURGE_DAYS)
        if self.db_tiers:
            if (not isinstance(self.db_tier_purge_days, int) or isinstance(self.db_tier_purge_days, bool)
                    or self.db_tier_purge_days < 1):
                raise ImproperlyConfigured(f"db_tiers purge_days for backup config '{name}' must be a whole "
                                           f'number of days, at least 1')
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
            if self.retention and not self.restore_only:
                # a restore_only config mirrors the writing config's layout so it can find
                # the dumps, but inherits BACKUP_DB_RETENTION from the globals - and prunes
                # nothing either way, so the two cannot conflict
                raise ImproperlyConfigured(
                    f"db_tiers for backup config '{name}' replaces client-side pruning, but retention is set"
                    f"{'' if 'retention' in config else ' (inherited from BACKUP_DB_RETENTION)'} - add "
                    f"'retention': [] to the config to let the bucket lifecycle rules do the pruning")
        # fails fast on a bad key before anything is backed up
        status = dict(DEFAULT_STATUS, **(getattr(settings, 'BACKUP_STATUS', None) or {}))
        status.update(config.get('status') or {})
        unknown = set(status) - set(DEFAULT_STATUS)
        if unknown:
            raise ImproperlyConfigured(f"status for backup config '{name}' has unknown option(s) "
                                       f'{", ".join(sorted(unknown))} - expected {", ".join(DEFAULT_STATUS)}')
        self.status = status
        self.encryption_key = resolve_key(config.get('encryption',
                                                     getattr(settings, 'BACKUP_ENCRYPTION', None)))
        self.changed_files = config.get('changed_files',
                                        getattr(settings, 'BACKUP_CHANGED_FILES', CHANGED_OVERWRITE))
        if self.changed_files not in (CHANGED_OVERWRITE, CHANGED_PROTECT, CHANGED_HISTORY):
            raise ImproperlyConfigured(f"changed_files for backup config '{name}' must be one of "
                                       f'{CHANGED_OVERWRITE!r}, {CHANGED_PROTECT!r}, {CHANGED_HISTORY!r}')

    @property
    def file_sources(self):
        """Every folder backup as a FileSource, local directories first - the list that
        backup_dir indices refer to, so adding azure_dirs renumbers nothing."""
        return ([FileSource(FileSource.LOCAL, source, dest_name) for source, dest_name in self.dirs] +
                [FileSource(FileSource.AZURE, prefix, dest_name) for prefix, dest_name in self.azure_dirs])


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
