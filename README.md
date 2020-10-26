
**django-gdrive-backup** 

Backs up django postgres databases, local folders and S3 folders to a Google Drive folder through a google service account.

**Create service account**

Requires a Google service account with the Google Drive API enabled

https://console.cloud.google.com/apis/credentials/serviceaccountkey

**Store service account key**

By default *encrypted-credentials* is used to store the key. Create a directory off the django projects BASE_DIR called credentials and save the json key. 

settings.py

    CREDENTIAL_FOLDER = os.path.join(BASE_DIR, 'credentials')
    CREDENTIAL_FILES = {
        'drive': 'service-account.json',
    }
  

**Create Google Drive folder and share with service account**

With a Google Drive account create a folder and share with the email address of the service account.


**Configure database backup**

settings.py

    BACKUP_GDRIVE_DIR = 'django_backup'
    BACKUP_GDRIVE_DB = BACKUP_GDRIVE_DIR + '/db'

**Management commands**

    python manage.py backup_website
    python manage.py restore_db

**Management page**

urls.py

    urlpatterns = [
                    path('backup/', include('gdrive_backup.urls')),
                    ....

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
            