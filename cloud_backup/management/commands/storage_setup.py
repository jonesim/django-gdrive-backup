from django.core.management.base import BaseCommand

from cloud_backup.config import config_names
from cloud_backup.storage_setup import SHELL_LABELS, check_config, default_shell


class Command(BaseCommand):
    help = 'Check each backup destination and print the commands to set it up. Changes nothing.'

    def add_arguments(self, parser):
        parser.add_argument('--config',
                            type=str,
                            help='Only check this BACKUP_CONFIGS entry (default: all of them)')

        parser.add_argument('--shell',
                            choices=list(SHELL_LABELS),
                            default=default_shell(),
                            help='Which shell the commands are quoted for - a JSON lifecycle rule needs '
                                 'different quoting in each')

    def handle(self, *args, **options):
        names = [options['config']] if options['config'] else config_names()
        for name in names:
            check = check_config(name, shell=options.get('shell'))
            print(f'\n=== {name} ' + '=' * max(0, 60 - len(name)))
            if check['error']:
                print('Config error: ' + check['error'])
                continue
            lines = [(label, value) for label, value in check['facts']]
            if check['storage_error']:
                lines.append(('Storage error', check['storage_error']))
            for row in check['rows']:
                detail = f" - {row['detail']}" if row.get('detail') else ''
                # the folder is a column of its own on the web page; here it goes with the
                # label, which is why the column is measured rather than fixed
                label = row['label'] + (f" {row['folder']}" if row.get('folder') else '')
                lines.append((label, f"{row['status']}{detail}"))
            width = max((len(label) for label, _ in lines), default=0) + 2
            for label, value in lines:
                print(f'  {label + ":":{width}}{value}')
            for line in check['guidance']:
                print('  ' + line)
            if check['commands']:
                print(f"\n  Setup commands (b2 command-line tool v4, quoted for "
                      f"{SHELL_LABELS[check['shell']]}):")
                for command in check['commands']:
                    print(f"\n  # {command['note']}\n  {command['command']}")
        print()
