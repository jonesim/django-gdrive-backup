"""Tests for the status check. Run from any host project that has cloud_backup
installed: ``python manage.py test cloud_backup``. The grading tests need no database;
the run-record tests do."""
import datetime
import json
from unittest.mock import patch

from celery.schedules import crontab
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from cloud_backup import status as st
from cloud_backup.config import DEFAULT_STATUS
from cloud_backup.models import BackupRun
from cloud_backup.runs import record_run
from cloud_backup.status import (FAILED, MISSING, OK, RUNNING, STALE, UNKNOWN, Schedule, collect, daily_check,
                                 dump_checks, latest_due_slot, monthly_check, run_check, schedule_slots)
from cloud_backup.views import BackupStatusView

SCHEDULE = {
    'backup_db': {'task': 'cloud_backup.tasks.backup', 'kwargs': {'config': 'database'},
                  'schedule': crontab(hour='7,9,11,13,15,17,19,21,23', minute=50)},
    'backup_files': {'task': 'cloud_backup.tasks.backup', 'kwargs': {'config': 'files'},
                     'schedule': crontab(minute=30, hour=1)},
    'promote': {'task': 'cloud_backup.tasks.promote_db_tiers', 'kwargs': {'config': 'database'},
                'schedule': crontab(minute=0, hour=6)},
}
DB_SLOTS = Schedule([(h, 50) for h in (7, 9, 11, 13, 15, 17, 19, 21, 23)])
OPTIONS = dict(DEFAULT_STATUS)
# a mid-morning, after the 09:50 run has had its grace period
NOW = datetime.datetime(2026, 8, 28, 10, 30)
GRACE = datetime.timedelta(minutes=30)

STORAGE = {'backend': 's3', 'bucket': 'bucket-db', 'b2': True, 'access_key_id': 'k', 'secret_key': 's'}
CONFIGS = {
    'database': {'storage': STORAGE, 'dirs': [], 'db_tiers': True, 'retention': []},
    'files': {'storage': dict(STORAGE, bucket='bucket-files', root='backup'), 'dirs': [('/media', 'media')],
              'db': False},
}
LOCAL_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


def dump(taken, size=1_000_000):
    return taken, size, f'django_backup/db/hourly/{taken:%Y/%m/%d}/db_{taken:%Y_%m_%d_%H_%M_%S}.dump'


@override_settings(USE_TZ=False, CELERY_BEAT_SCHEDULE=SCHEDULE, BACKUP_CONFIGS=CONFIGS)
class ScheduleTest(SimpleTestCase):

    def test_slots_per_config(self):
        self.assertEqual(schedule_slots(BackupRun.BACKUP, 'database').slots, DB_SLOTS.slots)
        self.assertEqual(schedule_slots(BackupRun.BACKUP, 'files').slots, [(1, 30)])
        self.assertEqual(schedule_slots(BackupRun.PROMOTE, 'database').slots, [(6, 0)])
        self.assertIsNone(schedule_slots(BackupRun.PROMOTE, 'files'))

    @override_settings(CELERY_BEAT_SCHEDULE={'b': {'task': 'cloud_backup.tasks.backup',
                                                   'schedule': crontab(hour=2, minute=0)}},
                       BACKUP_CONFIGS={'only': CONFIGS['database']})
    def test_entry_without_config_is_the_default(self):
        self.assertEqual(schedule_slots(BackupRun.BACKUP, 'only').slots, [(2, 0)])

    @override_settings(CELERY_BEAT_SCHEDULE={'b': {'task': 'cloud_backup.tasks.backup',
                                                   'schedule': crontab(hour='8-19', minute=10,
                                                                       day_of_week='mon-fri')}},
                       BACKUP_CONFIGS={'only': CONFIGS['database']})
    def test_day_constraints_captured(self):
        schedule = schedule_slots(BackupRun.BACKUP, 'only')
        self.assertEqual(schedule.days_of_week, {1, 2, 3, 4, 5})
        self.assertTrue(schedule.fires_on(datetime.date(2026, 8, 28)))    # a Friday
        self.assertFalse(schedule.fires_on(datetime.date(2026, 8, 30)))   # a Sunday

    @override_settings(CELERY_BEAT_SCHEDULE={'b': {'task': 'cloud_backup.tasks.backup', 'schedule': 3600}})
    def test_interval_schedule_has_no_slots(self):
        self.assertIsNone(schedule_slots(BackupRun.BACKUP, 'database'))

    def test_latest_due_slot(self):
        self.assertEqual(latest_due_slot(NOW, DB_SLOTS, GRACE), datetime.datetime(2026, 8, 28, 9, 50))
        # 09:50 is still within its grace at 10:10, so 07:50 is the one that must be there
        self.assertEqual(latest_due_slot(NOW.replace(hour=10, minute=10), DB_SLOTS, GRACE),
                         datetime.datetime(2026, 8, 28, 7, 50))
        # overnight the last due run is yesterday's
        self.assertEqual(latest_due_slot(NOW.replace(hour=3), DB_SLOTS, GRACE), datetime.datetime(2026, 8, 27, 23, 50))

    def test_weekday_schedule_over_the_weekend(self):
        weekdays = Schedule(DB_SLOTS.slots, days_of_week={1, 2, 3, 4, 5})   # celery: 0 is Sunday
        friday_last = datetime.datetime(2026, 8, 28, 23, 50)
        # all weekend the last due run stays Friday night's, so a Friday backup is not stale
        self.assertEqual(latest_due_slot(datetime.datetime(2026, 8, 30, 10, 30), weekdays, GRACE), friday_last)
        # and still on Monday before the first slot's grace has passed
        self.assertEqual(latest_due_slot(datetime.datetime(2026, 8, 31, 7, 30), weekdays, GRACE), friday_last)
        self.assertEqual(latest_due_slot(datetime.datetime(2026, 8, 31, 8, 30), weekdays, GRACE),
                         datetime.datetime(2026, 8, 31, 7, 50))
        checks = dump_checks(datetime.datetime(2026, 8, 30, 10, 30), [dump(friday_last)], weekdays, OPTIONS)
        self.assertEqual(checks[0]['status'], OK)
        self.assertEqual(checks[0]['alert_at'], 'older than the Fri 23:50 run')

    def test_monthly_schedule(self):
        monthly = Schedule([(0, 30)], days_of_month={1})
        self.assertEqual(latest_due_slot(NOW, monthly, GRACE), datetime.datetime(2026, 8, 1, 0, 30))


@override_settings(USE_TZ=False)
class DumpCheckTest(SimpleTestCase):

    def test_current_dump_ok(self):
        checks = dump_checks(NOW, [dump(NOW.replace(hour=9, minute=50)), dump(NOW.replace(hour=7, minute=50))],
                             DB_SLOTS, OPTIONS)
        self.assertEqual([c['status'] for c in checks], [OK, OK])
        self.assertIn('40 min ago', checks[0]['now'])
        self.assertEqual(checks[0]['alert_at'], 'older than the 09:50 run')

    def test_missed_run_is_stale(self):
        self.assertEqual(dump_checks(NOW, [dump(NOW.replace(hour=7, minute=50))], DB_SLOTS, OPTIONS)[0]['status'],
                         STALE)

    def test_overnight_gap_is_ok(self):
        checks = dump_checks(NOW.replace(hour=3), [dump(datetime.datetime(2026, 8, 27, 23, 50))], DB_SLOTS, OPTIONS)
        self.assertEqual(checks[0]['status'], OK)

    def test_no_dumps(self):
        self.assertEqual(dump_checks(NOW, [], DB_SLOTS, OPTIONS)[0]['status'], MISSING)

    def test_no_schedule_falls_back_to_age(self):
        recent = dump_checks(NOW, [dump(NOW - datetime.timedelta(hours=20))], None, OPTIONS)
        old = dump_checks(NOW, [dump(NOW - datetime.timedelta(hours=30))], None, OPTIONS)
        self.assertEqual(recent[0]['status'], OK)
        self.assertEqual(old[0]['status'], STALE)
        self.assertEqual(old[0]['alert_at'], 'older than 24 h (no beat schedule here)')

    def test_shrunken_dump_flagged(self):
        checks = dump_checks(NOW, [dump(NOW.replace(hour=9, minute=50), size=500_000),
                                   dump(NOW.replace(hour=7, minute=50), size=1_000_000)], DB_SLOTS, OPTIONS)
        self.assertEqual(checks[1]['status'], FAILED)
        self.assertIn('(50%)', checks[1]['now'])

    def test_options_override(self):
        lenient = dict(OPTIONS, size_drop=0.4, grace_minutes=120)
        checks = dump_checks(NOW, [dump(NOW.replace(hour=7, minute=50), size=500_000),
                                   dump(NOW.replace(hour=5, minute=50), size=1_000_000)], DB_SLOTS, lenient)
        # with two hours' grace the 09:50 run is not due yet at 10:30, so 07:50 is current
        self.assertEqual([c['status'] for c in checks], [OK, OK])


@override_settings(USE_TZ=False)
class PromotedTierTest(SimpleTestCase):

    def test_daily(self):
        yesterday = NOW.date() - datetime.timedelta(days=1)
        self.assertEqual(daily_check(NOW, (yesterday, 1, 'k'), OPTIONS)['status'], OK)
        self.assertEqual(daily_check(NOW, (yesterday - datetime.timedelta(days=1), 1, 'k'), OPTIONS)['status'], STALE)
        # before the promotion deadline the day before yesterday is still acceptable
        early = NOW.replace(hour=5)
        self.assertEqual(daily_check(early, (yesterday - datetime.timedelta(days=1), 1, 'k'), OPTIONS)['status'], OK)
        self.assertEqual(daily_check(NOW, None, OPTIONS)['status'], MISSING)

    def test_monthly(self):
        self.assertEqual(monthly_check(NOW, (datetime.date(2026, 7, 1), 1, 'k'), OPTIONS)['status'], OK)
        self.assertEqual(monthly_check(NOW, (datetime.date(2026, 6, 1), 1, 'k'), OPTIONS)['status'], STALE)
        first_early = datetime.datetime(2026, 9, 1, 5, 0)
        self.assertEqual(monthly_check(first_early, (datetime.date(2026, 7, 1), 1, 'k'), OPTIONS)['status'], OK)
        self.assertEqual(monthly_check(first_early.replace(hour=8), (datetime.date(2026, 7, 1), 1, 'k'),
                                       OPTIONS)['status'], STALE)
        self.assertEqual(monthly_check(NOW, None, OPTIONS)['status'], MISSING)

    def test_monthly_not_yet_due(self):
        # tiering started in August: nothing to promote until 1 September
        young = monthly_check(NOW, None, OPTIONS, oldest_daily=datetime.date(2026, 8, 7))
        self.assertEqual(young['status'], OK)
        self.assertIn('daily copies start 07 Aug 2026', young['now'])
        self.assertEqual(young['alert_at'], 'first due 1 Sep 2026')
        # but a daily tier that spans a month end with no monthly copy is a real gap
        self.assertEqual(monthly_check(NOW, None, OPTIONS, oldest_daily=datetime.date(2026, 7, 20))['status'],
                         MISSING)


class FakeStorage:
    """Enough of S3Storage for the collector: prefix folders and a delimited listing."""
    lock = {}

    def __init__(self, objects):
        self.objects = objects  # key -> size

    def ensure_folder(self, path, parent=None):
        return {'id': (f"{parent['id']}/{path}" if parent else path).strip('/'), 'name': path}

    def list_files(self, folder, metadata_filter=None, deleted=False, include_metadata=False):
        prefix = folder['id'] + '/'
        return [{'id': key, 'name': key.rsplit('/', 1)[-1], 'size': size, 'created': None, 'metadata': {}}
                for key, size in self.objects.items()
                if key.startswith(prefix) and '/' not in key[len(prefix):]]

    def destination_status(self, root=None):
        return {'state': 'ok', 'detail': 'bucket is accessible'}


HEALTHY_BUCKET = {
    'django_backup/db/hourly/2026/08/27/db_2026_08_27_23_50_03.dump': 1_000_000,
    'django_backup/db/hourly/2026/08/28/db_2026_08_28_07_50_02.dump': 1_010_000,
    'django_backup/db/hourly/2026/08/28/db_2026_08_28_09_50_04.dump': 1_020_000,
    'django_backup/db/daily/db_2026-08-27.dump': 1_000_000,
    'django_backup/db/monthly/db_2026-07.dump': 900_000,
}


def fake_backups(objects):
    """backup_for() replacement: real Backup objects over a fake storage."""
    from cloud_backup.backup import Backup

    def make(name):
        backup = Backup(config=name)
        backup._storage = FakeStorage(objects if name == 'database' else {})
        return backup
    return make


def run(config, kind, status=BackupRun.SUCCESS, finished=None, started=None, error=''):
    finished = finished or NOW.replace(hour=9, minute=52)
    return BackupRun(pk=1, config=config, kind=kind, status=status, started=started or finished,
                     finished=None if status == BackupRun.RUNNING else finished, error=error, detail={})


@override_settings(USE_TZ=False, CELERY_BEAT_SCHEDULE=SCHEDULE, BACKUP_CONFIGS=CONFIGS, CACHES=LOCAL_CACHE)
class RunCheckTest(SimpleTestCase):

    def test_success_within_slot(self):
        self.assertEqual(run_check(NOW, BackupRun.BACKUP, run('database', 'backup'), DB_SLOTS, OPTIONS)['status'], OK)

    def test_success_but_missed_latest_run(self):
        stale = run('database', 'backup', finished=NOW.replace(hour=7, minute=52))
        self.assertEqual(run_check(NOW, BackupRun.BACKUP, stale, DB_SLOTS, OPTIONS)['status'], STALE)

    def test_failure(self):
        failed = run('database', 'backup', status=BackupRun.FAILURE, error='DatabaseUploadError: boom')
        row = run_check(NOW, BackupRun.BACKUP, failed, DB_SLOTS, OPTIONS)
        self.assertEqual(row['status'], FAILED)
        self.assertIn('boom', row['now'])

    def test_running_and_stuck(self):
        fresh = run('database', 'backup', status=BackupRun.RUNNING, started=NOW - datetime.timedelta(minutes=5))
        stuck = run('database', 'backup', status=BackupRun.RUNNING, started=NOW - datetime.timedelta(hours=7))
        self.assertEqual(run_check(NOW, BackupRun.BACKUP, fresh, DB_SLOTS, OPTIONS)['status'], RUNNING)
        self.assertEqual(run_check(NOW, BackupRun.BACKUP, stuck, DB_SLOTS, OPTIONS)['status'], FAILED)

    def test_no_run(self):
        row = run_check(NOW, BackupRun.BACKUP, None, DB_SLOTS, OPTIONS)
        self.assertEqual(row['status'], MISSING)
        self.assertEqual(row['now'], 'no run recorded in the last 90 days')


@override_settings(USE_TZ=False, CELERY_BEAT_SCHEDULE=SCHEDULE, BACKUP_CONFIGS=CONFIGS, CACHES=LOCAL_CACHE)
class CollectTest(SimpleTestCase):

    def collect(self, objects=HEALTHY_BUCKET, runs=None):
        if runs is None:
            runs = {('database', 'backup'): run('database', 'backup'),
                    ('database', 'promote'): run('database', 'promote', finished=NOW.replace(hour=6, minute=1)),
                    ('files', 'backup'): run('files', 'backup', finished=NOW.replace(hour=1, minute=45))}
        with patch.object(st, 'backup_for', side_effect=fake_backups(objects)), \
                patch.object(st, 'latest_run', side_effect=lambda c, k: runs.get((c, k))):
            return collect(now=NOW)

    def test_healthy(self):
        status = self.collect()
        self.assertTrue(status['ok'], status['problems'])
        self.assertEqual([c['metric'] for c in status['checks']],
                         ['database: Latest database dump', 'database: Dump size vs previous',
                          'database: Latest daily copy', 'database: Latest monthly copy',
                          'database: Backup run', 'database: Tier promotion run',
                          'files: Destination reachable', 'files: Backup run'])
        self.assertEqual(status['buckets'], {'database': 'bucket-db', 'files': 'bucket-files'})
        self.assertEqual(status['configs']['database']['checks'][0]['key'],
                         'django_backup/db/hourly/2026/08/28/db_2026_08_28_09_50_04.dump')
        self.assertTrue(status['configs']['files']['ok'])

    def test_single_config_has_no_prefix(self):
        with patch.object(st, 'backup_for', side_effect=fake_backups(HEALTHY_BUCKET)), \
                patch.object(st, 'latest_run', return_value=None):
            status = collect(['database'], now=NOW)
        self.assertEqual(status['checks'][0]['metric'], 'Latest database dump')
        self.assertEqual(status['problems'], ['Backup run', 'Tier promotion run'])

    def test_stopped_beat(self):
        # nothing since yesterday evening and no runs recorded
        objects = {k: v for k, v in HEALTHY_BUCKET.items() if '2026/08/28' not in k}
        status = self.collect(objects, runs={})
        self.assertFalse(status['ok'])
        self.assertEqual(status['problems'], ['database: Latest database dump', 'database: Backup run',
                                              'database: Tier promotion run', 'files: Backup run'])

    def test_unreachable_destination_is_a_row_not_an_error(self):
        def broken(name):
            raise ConnectionError('no route to b2')
        with patch.object(st, 'backup_for', side_effect=broken), self.assertLogs('cloud_backup.status', level='ERROR'):
            status = collect(now=NOW)
        self.assertFalse(status['ok'])
        self.assertEqual(status['checks'][0]['status'], UNKNOWN)
        self.assertIn('no route to b2', status['checks'][0]['now'])

    def test_json_serialisable(self):
        json.dumps(self.collect())

    def test_view_permission_hook(self):
        status = self.collect()
        factory = RequestFactory()

        class Superuser:
            is_authenticated = is_superuser = True

            def has_perm(self, perm):
                return False

        class Nobody:
            is_authenticated = True
            is_superuser = False

            def has_perm(self, perm):
                return False

        with patch.object(st, 'collect', return_value=status):
            for user, expected in ((AnonymousUser(), 403), (Nobody(), 403), (Superuser(), 200)):
                request = factory.get('/backup/status/?refresh=1')
                request.user = user
                self.assertEqual(BackupStatusView.as_view()(request).status_code, expected)
            request = factory.get('/backup/status/?refresh=1')
            request.user = Superuser()
            self.assertTrue(json.loads(BackupStatusView.as_view()(request).content)['ok'])


@override_settings(USE_TZ=False)
class RecordRunTest(TestCase):

    def test_success_and_failure_recorded(self):
        with record_run('database', BackupRun.BACKUP) as detail:
            detail['db'] = 1
        with self.assertRaises(ValueError):
            with record_run('database', BackupRun.PROMOTE):
                raise ValueError('bang')
        ok, bad = BackupRun.objects.get(kind='backup'), BackupRun.objects.get(kind='promote')
        self.assertEqual((ok.status, ok.detail, ok.error), ('success', {'db': 1}, ''))
        self.assertEqual((bad.status, bad.error), ('failure', 'ValueError: bang'))
        self.assertIsNotNone(bad.finished)
        self.assertGreaterEqual(bad.duration, 0)

    def test_latest_run_and_pruning(self):
        from cloud_backup.runs import latest_run, prune
        old = BackupRun.objects.create(config='database', kind='backup', status='success',
                                       started=datetime.datetime(2020, 1, 1), finished=datetime.datetime(2020, 1, 1))
        stuck = BackupRun.objects.create(config='database', kind='backup', started=datetime.datetime(2020, 1, 2))
        new = BackupRun.objects.create(config='database', kind='backup', status='success')
        self.assertEqual(latest_run('database', 'backup'), new)
        self.assertIsNone(latest_run('files', 'backup'))
        prune()
        self.assertFalse(BackupRun.objects.filter(pk=old.pk).exists())
        # a running row is never pruned - it is the only evidence of a run that died
        self.assertTrue(BackupRun.objects.filter(pk=stuck.pk).exists())
