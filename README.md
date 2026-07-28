[![PyPI version](https://badge.fury.io/py/django-gdrive-backup.svg)](https://badge.fury.io/py/django-gdrive-backup)


**django-gdrive-backup** 

Backs up django postgres databases, local folders and S3 folders to a Google Drive folder through a google service account.

**encrypted-credentials**

This package uses encrypted-credentials and the instructions there could be useful. Adding the following lines to **settings.py** will initialise the package

    from encrypted_credentials.django_credentials import add_encrypted_settings
    
    add_encrypted_settings(globals())


**Create service account**

Requires a Google service account with the Google Drive API enabled

https://console.cloud.google.com/apis/credentials/serviceaccountkey

**Add to gdrive_backup installed apps**

settings.py

    INSTALLED_APPS = [ ..
            'gdrive_backup',
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

    BACKUP_GDRIVE_DIR = 'django_backup'

**Choosing a backup destination**

Google Drive is the default destination and needs no extra settings beyond those above.
Backups can instead be stored on any S3-compatible service or Azure Blob Storage by adding
a `BACKUP_STORAGE` dict to settings.py. The optional `root` key replaces `BACKUP_GDRIVE_DIR`
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

S3 backends require `pip install django-gdrive-backup[s3]` and Azure
`pip install django-gdrive-backup[azure]`.

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
            'task': 'gdrive_backup.tasks.extend_retention',
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


**Management commands**

    python manage.py backup_website
    python manage.py restore_db

**Management page**

urls.py

    urlpatterns = [
                    path('backup/', include('gdrive_backup.urls')),
                    ....


An enhanced version of the management page will be shown if the following django apps are installed

    'django_modals', 'django_datatables', 'django_menus', 'ajax_helpers'

from the following PyPi packages

    django-nested-modals, django-filtered-datatables, django-tab-menus, django-ajax-helpers

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
            'task': 'gdrive_backup.tasks.backup',
            'schedule': crontab(hour='8-19', minute=10, day_of_week='mon-fri')
        }
    }
            
