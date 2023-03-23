from django.core.management.base import BaseCommand

from gdrive_backup.backup import Backup


class Logger:
    @staticmethod
    def info(text):
        print(text)


class Command(BaseCommand):

    def add_arguments(self, parser):
        parser.add_argument('--schema',
                            nargs='?',
                            help='Specifies which schema to backup')

        parser.add_argument('--table',
                            nargs='?',
                            help='Specifies which table to backup. Must also include schema.')

        parser.add_argument('--all_schemas',
                            action='store_true',
                            default=False)

        parser.add_argument('--db_only',
                            action='store_true',
                            default=False)

        parser.add_argument('--s3_folders_only',
                            action='store_true',
                            default=False)

        parser.add_argument('--folders_only',
                            action='store_true',
                            default=False)

        parser.add_argument('-sub_folder',
                            type=str)

    def handle(self, *args, **options):
        folder_kwargs = {}
        if options['db_only']:
            folder_kwargs['include_folders'] = False
            folder_kwargs['include_s3_folders'] = False
        elif options['s3_folders_only']:
            folder_kwargs['include_db'] = False
            folder_kwargs['include_folders'] = False
        elif options['folders_only']:
            folder_kwargs['include_db'] = False
            folder_kwargs['include_s3_folders'] = False
        Backup(logger=Logger()).backup_db_and_folders(all_schemas=options['all_schemas'],
                                                      schema=options['schema'],
                                                      table=options['table'],
                                                      sub_folder=options['sub_folder'],
                                                      **folder_kwargs)
