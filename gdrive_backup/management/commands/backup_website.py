from django.core.management.base import BaseCommand
from ...backup import Backup


class Command(BaseCommand):

    def add_arguments(self, parser):
        parser.add_argument('--schema',
                            nargs='?',
                            help='Specifies which schema to backup')

        parser.add_argument('--all_schemas',
                            action=BooleanOptionalAction,
                            default=False)

        parser.add_argument('--include_folders',
                            action=BooleanOptionalAction,
                            default=True)

        parser.add_argument('--include_s3_folders',
                            action=BooleanOptionalAction,
                            default=True)

    def handle(self, *args, **options):

        include_folders = options['include_folders']
        include_s3_folders = options['include_s3_folders']

        if options['all_schemas']:
            Backup().backup_db_all_schemas_and_folders(include_folders=include_folders,
                                                       include_s3_folders=include_s3_folders)

        else:
            schema = options['schema']
            Backup().backup_db_and_folders(include_folders=include_folders,
                                           include_s3_folders=include_s3_folders)
