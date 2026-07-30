[![PyPI version](https://badge.fury.io/py/django-cloud-backup.svg)](https://badge.fury.io/py/django-cloud-backup)


**django-cloud-backup** 

Backs up django postgres databases, local folders and S3 folders to Google Drive, S3-compatible storage (AWS, Backblaze B2, Cloudflare R2) or Azure Blob Storage.

**Migrating from django-gdrive-backup**

This package was previously published as `django-gdrive-backup`. The rename is a breaking
release; upgrading requires the following changes in your project:

- Install `django-cloud-backup` (and its extras, e.g. `django-cloud-backup[s3]`) instead of
  `django-gdrive-backup`
- `INSTALLED_APPS`: `'gdrive_backup'` → `'cloud_backup'`
- urls.py: `include('gdrive_backup.urls')` → `include('cloud_backup.urls')`, and any
  reverses/`{% url %}` tags use the `cloud_backup:` namespace instead of `gdrive_backup:`
- Celery beat schedules: task names are now `cloud_backup.tasks.*`
  (e.g. `cloud_backup.tasks.backup`)
- Settings renamed: `BACKUP_GDRIVE_DIR` → `BACKUP_ROOT`, `BACKUP_GDRIVE_DB` → `BACKUP_DB_DIR`
  (they apply to every destination backend, not just Google Drive). `BACKUP_TEAM_DRIVE` is
  unchanged.
- Removed legacy method aliases `backup_db_gdrive`, `restore_gdrive_db` and
  `restore_gdrive_folder` - use `backup_db_to_storage`, `restore_db_from_storage` and
  `restore_folder`
- Client-side encryption: the file format constants changed with the rename, so files
  encrypted by pre-release versions of the encryption feature cannot be read. No published
  release included encryption, so this affects no production backups.

Backups already in your storage destination are unaffected - folder layout and metadata
are unchanged, and existing backups restore as before.

**encrypted-credentials**

This package uses encrypted-credentials and the instructions there could be useful. Adding the following lines to **settings.py** will initialise the package

    from encrypted_credentials.django_credentials import add_encrypted_settings
    
    add_encrypted_settings(globals())


**Create service account**

Requires a Google service account with the Google Drive API enabled

https://console.cloud.google.com/apis/credentials/serviceaccountkey

**Add to cloud_backup installed apps**

settings.py

    INSTALLED_APPS = [ ..
            'cloud_backup',
        ]

**Store service account key**

By default *encrypted-credentials* is used to store the key. Create a directory off the django projects BASE_DIR called credentials and save the json key. 

settings.py

    CREDENTIAL_FOLDER = os.path.join(BASE_DIR, 'credentials')
    CREDENTIAL_FILES = {
        'drive': 'service-account.json',
    }
  

**Create Google Drive folder and share with service account**

With a Google Drive account create a folder and share with the email address of the service account.


**Ensure psql is available to python subprocess**

For docker containers you may need to something similar to the following line in the Dockerfile dependent on the version of Postgres.

    RUN apt-get -y install postgresql-client-11

**Configure database backup**

settings.py

    BACKUP_ROOT = 'django_backup'

**Choosing a backup destination**

Google Drive is the default destination and needs no extra settings beyond those above.
Backups can instead be stored on any S3-compatible service or Azure Blob Storage by adding
a `BACKUP_STORAGE` dict to settings.py. The optional `root` key replaces `BACKUP_ROOT`
as the top-level folder/prefix.

AWS S3:

    BACKUP_STORAGE = {
        'backend': 's3',
        'bucket': 'my-backups',
        'access_key_id': '...',
        'secret_key': '...',
        'root': 'django_backup',
    }

Backblaze B2 (the S3 endpoint is discovered from the key automatically):

    BACKUP_STORAGE = {
        'backend': 's3',
        'b2': True,
        'bucket': 'my-backups',
        'access_key_id': '...',       # B2 keyID
        'secret_key': '...',          # B2 applicationKey
    }

Cloudflare R2:

    BACKUP_STORAGE = {
        'backend': 's3',
        'bucket': 'my-backups',
        'endpoint_url': 'https://<account-id>.r2.cloudflarestorage.com',
        'region': 'auto',
        'access_key_id': '...',
        'secret_key': '...',
    }

Azure Blob Storage:

    BACKUP_STORAGE = {
        'backend': 'azure',
        'container': 'backups',
        'connection_string': '...',   # or account_url + credential
    }

S3 backends require `pip install django-cloud-backup[s3]` and Azure
`pip install django-cloud-backup[azure]`.

Note that unlike Google Drive, S3 and Azure destinations have no trash - pruned
database backups are deleted permanently, so consider enabling bucket versioning
(S3/B2/R2 lifecycle rules) or soft delete (Azure) if you want a safety net. The
backup page queries the destination and shows whether versioning, soft delete and
Object Lock/immutability (WORM) are actually enabled, so a missing safety net is
visible at a glance.

**Ransomware protection**

If backups re-sync whenever a source file changes, an attacker encrypting your files
would overwrite the good backups on the next scheduled run. Protection is layered:

*Changed-file handling* - settings.py:

    BACKUP_CHANGED_FILES = 'overwrite'   # default: re-upload changed files
    BACKUP_CHANGED_FILES = 'protect'     # never touch the existing backup: skip the
                                         # file, log a warning and fail the backup run
                                         # (raises ChangedFilesError after everything
                                         # else has completed, so monitoring alerts)
    BACKUP_CHANGED_FILES = 'history'     # keep the previous version (S3 server-side
                                         # copy named <file>.<timestamp>, Azure
                                         # snapshot, Google Drive rename), then upload
                                         # the new version; warns but succeeds

Use `'protect'` when your file store is immutable (e.g. UUID-named uploads that are
never edited) - any change is corruption or an attacker. Use `'history'` when changes
can be legitimate but you still want every previous version recoverable.

*Object Lock retention (S3 and B2)* - create the bucket with Object Lock enabled and
add a `lock` section to `BACKUP_STORAGE`:

    BACKUP_STORAGE = {
        'backend': 's3', ...,
        'lock': {
            'mode': 'COMPLIANCE',   # not even the bucket owner can shorten or delete
            'db_days': 35,          # each database dump is locked for 35 days at upload
            'file_days': 7,         # each file backup is locked for 7 days at upload
            'min_days': 7,          # extend_retention keeps every file locked >= 7 days ahead
        },
    }

Locking at upload costs nothing (extra headers on the existing request). To keep
long-lived file backups permanently locked, schedule the top-up task daily - it
extends any object whose remaining lock is below `min_days`:

    CELERY_BEAT_SCHEDULE = {
        'extend_retention': {
            'task': 'cloud_backup.tasks.extend_retention',
            'schedule': crontab(hour=3, minute=0),
        },
    }

or run `python manage.py backup_website --extend_retention`. The top-up costs 1-2 API
calls per file (roughly a minute per 10,000 files), which is why it is a scheduled
task rather than part of every backup. Backups only become deletable `min_days` after
the top-up task stops running.

Pruning still works on a locked bucket: Object Lock buckets are versioned, so deleting
an old dump just writes a delete marker (allowed even while versions are locked) and
the locked versions physically remain. Add a bucket lifecycle rule such as "expire
noncurrent versions after 40 days" to clean them up once their lock has passed. The
same versioning means even `'overwrite'` mode cannot physically destroy data on a
locked bucket - the prior locked version survives underneath.

*Credential and bucket hardening* (outside this package):

- **AWS S3**: give the backup IAM user no `s3:DeleteObject`/`s3:PutBucketLifecycle`;
  prune via lifecycle rules instead of `BACKUP_DB_RETENTION`
- **Backblaze B2**: use an application key without the `deleteFiles` capability
- **Azure**: enable blob soft delete or a container immutability policy
- **Google Drive**: deletes go to trash and are recoverable, but the service account
  can empty the trash - treat the credential file accordingly

With delete-less credentials, pruning logs a warning instead of failing the backup;
leave `BACKUP_DB_RETENTION` unset and let bucket lifecycle rules do the pruning.

**Client-side encryption**

By default backups are stored as the provider receives them - anyone with access to
the Drive folder or bucket can read a full database dump. Setting `BACKUP_ENCRYPTION`
encrypts every backup (database dumps, local folder files and S3-source files) on the
client before upload, using chunked AES-256-GCM, so the provider only ever holds
ciphertext:

    BACKUP_ENCRYPTION = True     # reuse the encrypted-credentials SETTINGS_KEY
    BACKUP_ENCRYPTION = '...'    # or a dedicated urlsafe-base64 32-byte key

`True` derives backup keys from the same `SETTINGS_KEY` that already protects the
encrypted credentials - nothing new to manage, and the key never sits in the
repository. The trade-off is coupling: today `SETTINGS_KEY` can be rotated cheaply by
re-encrypting the `.enc` settings files, but once backups are encrypted with it, old
backups need the old key forever. A dedicated key avoids that; generate one with:

    python -c "from encrypted_credentials.encrypted_file import random_key; print(random_key())"

and keep it in the encrypted private settings, not plain settings.py.

Notes:

- Restores are transparent - encrypted and older unencrypted backups are detected by
  content and both restore normally, including `restore_db --local_file`. A wrong or
  missing key fails cleanly before anything reaches `pg_restore`.
- **Losing the key means losing every backup encrypted with it.** Keep a copy of the
  key somewhere that does not depend on the server or the backups themselves.
- File deduplication keeps working: the plaintext md5 is recorded in each file's
  metadata at upload and compared on later runs. If you turn encryption off again,
  already-encrypted file backups re-upload once (their metadata is no longer fetched).
- Database backups briefly need twice the dump size in `BACKUP_LOCAL_DB_DIR` while the
  ciphertext copy is written; folder and S3-source backups encrypt in-stream with no
  extra disk.
- Requires the `cryptography` package (`pip install django-cloud-backup[encryption]`) -
  already present in practice, as encrypted-credentials depends on it.
- `BackupAzureToS3` is a separate rclone-compatible mirror and is not encrypted.
- With multiple backup configurations (below), encryption is set per config rather
  than globally.

**Multiple backup configurations**

`BACKUP_CONFIGS` lets one project back up to several destinations with different
behaviour per destination - the classic case being a hardened offsite backup plus an
unencrypted database copy that a staging server restores from:

    BACKUP_CONFIGS = {
        'default': {
            'storage': {'backend': 's3', 'bucket': 'offsite-backups', ..., 'lock': {...}},
            'encryption': True,
            'changed_files': 'protect',
        },
        'staging': {
            'storage': {'backend': 's3', 'bucket': 'staging-transfer', ...},
            'encryption': False,
            'dirs': [],                              # database only, no folder backups
            'retention': [{'days': 1, 'number': 2}], # keep just the latest couple of dumps
            'changed_files': 'overwrite',
        },
    }

Config keys: `storage` (a `BACKUP_STORAGE`-style dict), `encryption`, `db` (include
the database, default True), `db_dir`, `dirs` (as `BACKUP_DIRS`), `s3_dirs` (as
`S3_BACKUP_DIRS`), `retention`, `changed_files`. **A key absent from a config
inherits the corresponding legacy global setting** (`BACKUP_STORAGE`,
`BACKUP_ENCRYPTION`, `BACKUP_DIRS`, ...), so shared values can stay in the globals -
but note that means a config without `'dirs': []` backs up the global `BACKUP_DIRS`.
Without `BACKUP_CONFIGS` the globals simply are the default config, so existing
installations are unaffected.

Running a config:

    python manage.py backup_website --config staging
    python manage.py restore_db --config staging       # e.g. on the staging server

    CELERY_BEAT_SCHEDULE = {
        'backup': {'task': 'cloud_backup.tasks.backup',
                   'schedule': crontab(hour='8-19', minute=10)},
        'backup_staging': {'task': 'cloud_backup.tasks.backup',
                           'schedule': crontab(hour=6, minute=0),
                           'kwargs': {'config': 'staging'}},
    }

The web UI and un-parameterised tasks use the config named `'default'` (or the only
entry, if there is exactly one). For the staging pattern, the staging server's own
settings point a config at the same transfer bucket and `restore_db` pulls from it -
production never shares its offsite credentials or encryption key with staging. If
the transfer bucket holds an unencrypted production dump, treat the bucket itself as
production-sensitive, or give that config a dedicated key the staging server also
has.

Backups made by one config restore with that config's key: restoring an encrypted
backup through a config with a different key (or none) fails cleanly.


**Management commands**

    python manage.py backup_website
    python manage.py restore_db

**Management page**

urls.py

    urlpatterns = [
                    path('backup/', include('cloud_backup.urls')),
                    ....


All URL names live under the `cloud_backup` namespace (e.g.
`reverse('cloud_backup:backup-info')`). Previously the basic management page
used un-namespaced names such as `backup-info`; add the `cloud_backup:` prefix
if you reverse them yourself.

An enhanced version of the management page will be shown if the following django apps are installed

    'django_modals', 'django_datatables', 'django_menus', 'ajax_helpers'

from the following PyPi packages

    django-nested-modals, django-filtered-datatables, django-tab-menus, django-ajax-helpers

**Branding the management page**

The enhanced page views build the whole UI (menus, storage info and tables) into a
single HTML string, `{{ backup_content }}`, so it can be dropped into your own
template. Subclass the base views and set `template_name`:

    from cloud_backup.enhanced_views import BackupBaseView, BackupFilesBaseView, SchemaTableBaseView

    class MyBackupView(BackupBaseView):
        template_name = 'myapp/backup.html'

    class MySchemaTableView(SchemaTableBaseView):
        template_name = 'myapp/backup.html'

    class MyBackupFilesView(BackupFilesBaseView):
        template_name = 'myapp/backup.html'

The template must include the ajax_helpers/datatables/modals libraries and the
page script, then place the content wherever it fits your layout:

    {% load ajax_helpers %}
    {% lib_include 'ajax_helpers' 'Bootstrap' 'FontAwesome' module='ajax_helpers.includes' %}
    {% lib_include 'datatable' module='django_datatables.includes' %}
    {% lib_include 'Modals' module='django_modals.includes' %}
    {{ ajax_helpers_script }}
    ...
    {{ backup_content }}

Register the subclasses with `backup_urlpatterns` so the menu links and modals
(which reverse the standard `cloud_backup:` URL names) point at your views:

    from cloud_backup.urls import backup_urlpatterns

    urlpatterns = [
        path('backup/', include((backup_urlpatterns(
            backup_view=MyBackupView, schema_table_view=MySchemaTableView,
            files_view=MyBackupFilesView), 'cloud_backup'))),
    ]

The unbranded standard page remains the default when using
`include('cloud_backup.urls')`.

**Restoring from the management page**

Restore and drop-schema actions on the enhanced management page require

    BACKUP_ALLOW_RESTORE = True

which defaults to the value of `DEBUG`. This is enforced server-side on every
restore endpoint (not just by hiding the buttons), so a production server with
`DEBUG = False` and no `BACKUP_ALLOW_RESTORE` setting cannot be restored from
the web UI even by a superuser. Set `BACKUP_ALLOW_RESTORE = True` on staging and
development machines where restoring is wanted. The `manage.py restore_db`
command is not affected by this setting, so disaster recovery on a live server
remains possible from the command line.

**Browsing and verifying folder backups**

When `BACKUP_DIRS` (or `S3_BACKUP_DIRS`) is configured, the enhanced management
page shows a `Backup Files` button that backs up all configured folders without
touching the database, and a single `Files` button opening a file browser. Its
root level lists each configured backup directory as a folder; clicking through
navigates the backed-up tree one level at a time (with breadcrumbs back up), and
files show their size, backup date and checksum - the plaintext md5 recorded
with the file at upload time (so it stays comparable when client-side encryption
is enabled).

The `Verify` button on each row re-hashes the file on the server's local disk
and compares it with the stored checksum, reporting:

- `Match` - the local file is identical to its backup
- `Changed` - the local file no longer matches its backup
- `Missing locally` - the local file has been deleted since it was backed up
- `No stored checksum` - the backup has no comparable checksum (e.g. a large
  multipart S3 upload made without md5 metadata)

`Verify All Files` runs the same comparison over the whole directory as a
celery task (the worker must be running) and reports a summary.

The browser and verification are read-only, require the same `access_admin`
permission as the rest of the management page, and always use the default
backup config. Note that on an S3-compatible destination with client-side
encryption enabled, listing the checksums costs one metadata request per file,
so the page can be slow to load for very large trees.

**Configure S3 folder backups**

settings.py

            AWS_ACCESS_KEY_ID = id
            AWS_SECRET_ACCESS_KEY = key
            AWS_PRIVATE_STORAGE_BUCKET_NAME = bucket
            
            S3_BACKUP_DIRS = [('S3-source-folder1', 'google-drive-folder1'),
                              ('S3-source-folder2', 'google-drive-folder2')
            ]
            
**Configure cleaning of old datatabase backups**

settings.py

    BACKUP_DB_RETENTION = [{'hours': 1, 'number': 4}, 
                           {'hours': 2, 'number': 10},
                           {'days': 1, 'number': 10},
                           {'months': 1, 'number': 36},
                           ]

             
**Schedule backup with celery beat**

    CELERY_BEAT_SCHEDULE = {
        'backup': {
            'task': 'cloud_backup.tasks.backup',
            'schedule': crontab(hour='8-19', minute=10, day_of_week='mon-fri')
        }
    }
            
