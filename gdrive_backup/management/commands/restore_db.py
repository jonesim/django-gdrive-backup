from django.core.management.base import BaseCommand
from ...backup import Backup


class Command(BaseCommand):

    def add_arguments(self, parser):
        parser.add_argument('--local_file', type=str)
        parser.add_argument('--schema', type=str)

    def handle(self, *args, **options):
        db = Backup.get_backup_db(schema=options['schema'])
        if options['local_file'] is not None:
            db.postgres_backup.restore_db(options['local_file'])
        else:
            latest_db = db.get_latest_db_backup()
            print('Found: ' + latest_db['name'])
            confirm = input('Do you want to restore which will overwrite current database (yes/no)? ')
            if confirm.lower() == 'yes':
                print('Restoring..')
                db.restore_gdrive_db(file_id=latest_db['id'])
