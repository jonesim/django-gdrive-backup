import datetime

from django.core.management.base import BaseCommand, CommandError

from cloud_backup.backup import Backup, RestoreOnlyConfig


class Logger:
    @staticmethod
    def info(text):
        print(text)

    @staticmethod
    def warning(text):
        print('WARNING: ' + text)

    @staticmethod
    def error(text):
        print('ERROR: ' + text)


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
                            default=False,
                            help='Only the folder backups: BACKUP_DIRS and AZURE_BACKUP_DIRS')

        parser.add_argument('--backup_dir',
                            type=int,
                            help='Back up only this folder source (its index in BACKUP_DIRS followed by '
                                 'AZURE_BACKUP_DIRS, as used by the web file browser) rather than all of them')

        parser.add_argument('-sub_folder',
                            type=str)

        parser.add_argument('--extend_retention',
                            action='store_true',
                            default=False,
                            help='Extend object-lock retention on existing backups instead of backing up')

        parser.add_argument('--promote_tiers',
                            action='store_true',
                            default=False,
                            help='Promote database dumps between the db_tiers hourly/daily/monthly '
                                 'tiers instead of backing up')

        parser.add_argument('--as_of',
                            type=str,
                            help='Promote as if today were this date (YYYY-MM-DD) - for backfilling')

        parser.add_argument('--days',
                            type=int,
                            help='How many days back --promote_tiers looks for dumps to promote')

        parser.add_argument('--config',
                            type=str,
                            help='Which BACKUP_CONFIGS entry to use (default: the default config)')

    def handle(self, *args, **options):
        try:
            self.backup(**options)
        except RestoreOnlyConfig as e:
            # asking a restore_only config to back up is a mistake in the command line, not
            # a failed backup - say so without a traceback
            raise CommandError(str(e))

    @staticmethod
    def backup(**options):
        if options['extend_retention']:
            Backup(logger=Logger(), config=options['config']).extend_file_retention()
            return
        if options['promote_tiers']:
            as_of = datetime.datetime.strptime(options['as_of'], '%Y-%m-%d').date() if options['as_of'] else None
            Backup(logger=Logger(), config=options['config']).promote_db_tiers(as_of=as_of, days=options['days'])
            return
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
        Backup(logger=Logger(), config=options['config']).backup_db_and_folders(all_schemas=options['all_schemas'],
                                                      schema=options['schema'],
                                                      table=options['table'],
                                                      sub_folder=options['sub_folder'],
                                                      backup_dir=options['backup_dir'],
                                                      **folder_kwargs)
