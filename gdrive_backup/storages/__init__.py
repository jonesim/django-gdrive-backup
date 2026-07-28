from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import BackupStorage, StorageFileNotFound  # noqa: F401


def storage_settings():
    """The BACKUP_STORAGE dict, defaulting to the original Google Drive behaviour so
    existing installations keep working without any settings changes."""
    return getattr(settings, 'BACKUP_STORAGE', None) or {'backend': 'gdrive'}


def backup_root():
    """Root folder/prefix at the destination that all backups go under."""
    return storage_settings().get('root', getattr(settings, 'BACKUP_GDRIVE_DIR', 'django_backup'))


def get_storage(config=None):
    """
    Build the configured BackupStorage. Config keys other than 'backend' and 'root' are
    passed to the backend constructor:

    gdrive: credentials (CREDENTIAL_FILES key, default 'drive'),
            shared_drive (defaults to settings.BACKUP_TEAM_DRIVE)
    s3:     bucket, access_key_id, secret_key, endpoint_url, region, b2
            (endpoint_url unset = AWS; b2=True discovers the Backblaze endpoint;
             R2 uses the account endpoint_url with region='auto')
    azure:  container, connection_string or account_url + credential
    """
    config = dict(config if config is not None else storage_settings())
    backend = config.pop('backend', 'gdrive')
    config.pop('root', None)
    if backend == 'gdrive':
        from encrypted_credentials import django_credentials
        from .gdrive import GDriveStorage
        credentials = django_credentials.get_credentials(config.pop('credentials', 'drive'))
        config.setdefault('shared_drive', getattr(settings, 'BACKUP_TEAM_DRIVE', None))
        return GDriveStorage(credentials, **config)
    if backend == 's3':
        from .s3 import S3Storage
        return S3Storage(**config)
    if backend == 'azure':
        from .azure import AzureStorage
        return AzureStorage(**config)
    raise ImproperlyConfigured(f'Unknown BACKUP_STORAGE backend {backend!r}')
