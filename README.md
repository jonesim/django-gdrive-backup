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
            
