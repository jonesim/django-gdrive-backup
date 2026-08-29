import json

from django.core.management.base import BaseCommand

from cloud_backup.status import collect


class Command(BaseCommand):
    help = ('Report whether the backups are current: the newest dump at each destination, the '
            'promoted copies and the last recorded run of each task, graded against the beat '
            'schedule. Exits 1 when any check is not OK, so it can be run from cron or a monitor.')

    def add_arguments(self, parser):
        parser.add_argument('--config', action='append', help='only these BACKUP_CONFIGS entries (repeatable)')
        parser.add_argument('--json', action='store_true', help='print the full status as json')

    def handle(self, *args, **options):
        status = collect(options['config'])
        if options['json']:
            self.stdout.write(json.dumps(status, indent=2, default=str))
        else:
            width = max(len(c['metric']) for c in status['checks']) if status['checks'] else 10
            for c in status['checks']:
                line = f"{c['status']:8} {c['metric']:{width}}  {c['now']}  [{c['alert_at']}]"
                self.stdout.write(line if c['ok'] else self.style.ERROR(line))
            summary = (f"OK - all {len(status['checks'])} checks passed" if status['ok']
                       else f"PROBLEM - {', '.join(status['problems'])}")
            self.stdout.write(self.style.SUCCESS(summary) if status['ok'] else self.style.ERROR(summary))
        if not status['ok']:
            raise SystemExit(1)
