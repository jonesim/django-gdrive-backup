"""Promotion between the lifecycle tiers - per server, so installations sharing one db
folder each keep their own daily and monthly copies. Pure python: db_tiers imports no
Django, so this runs with `python -m unittest cloud_backup.tests.test_db_tiers` as well
as under the Django test runner."""
import datetime
import logging
import unittest

from cloud_backup.db_tiers import DAILY, DbTierPromoter, MONTHLY, list_tier, server_of

PROD, STAGING = '20_117_165_6', '51_142_93_33'
DB = 'django_backup/db'


class FakeStorage:
    """An s3-shaped bucket: key -> (size, metadata). Folders are prefixes."""

    def __init__(self, objects):
        self.objects = dict(objects)
        self.copies = []

    def ensure_folder(self, path, parent=None):
        return {'id': (f"{parent['id']}/{path}" if parent else path).strip('/'), 'name': path}

    def _file(self, key):
        size, metadata = self.objects[key]
        return {'id': key, 'name': key.rsplit('/', 1)[-1], 'size': size, 'created': None,
                'metadata': dict(metadata)}

    def list_files(self, folder, metadata_filter=None, deleted=False, include_metadata=False):
        prefix = folder['id'] + '/'
        return [self._file(key) for key in self.objects
                if key.startswith(prefix) and '/' not in key[len(prefix):]]

    def walk(self, folder, include_metadata=False):
        prefix = folder['id'] + '/'
        for key in list(self.objects):
            if key.startswith(prefix):
                relative = key[len(prefix):]
                yield relative.rsplit('/', 1)[0] if '/' in relative else '', self._file(key)

    def copy_stored_file(self, stored_file, folder, name, extra_metadata=None, lock_days=None, lock_mode=None):
        key = f"{folder['id']}/{name}"
        size, metadata = self.objects[stored_file['id']]
        self.objects[key] = (size, dict(metadata, **(extra_metadata or {})))
        self.copies.append((stored_file['id'], key))
        return self._file(key)

    def delete(self, file_id):
        del self.objects[file_id]


def hourly(day, hour, minute, server, size=1000, second=0):
    key = f'{DB}/hourly/{day:%Y/%m/%d}/db_{day:%Y_%m_%d}_{hour:02}_{minute:02}_{second:02}.dump'
    return key, (size, {'ip_address': server})


def promoter(storage, **kwargs):
    logger = logging.getLogger('test')
    logger.addHandler(logging.NullHandler())  # keep the expected warnings out of the test output
    return DbTierPromoter(storage, storage.ensure_folder(DB), logger, **kwargs)


AUG_28 = datetime.date(2026, 8, 28)
AUG_29 = datetime.date(2026, 8, 29)


class PromoteDailyTest(unittest.TestCase):

    def test_one_copy_per_server(self):
        storage = FakeStorage([hourly(AUG_28, 7, 50, PROD), hourly(AUG_28, 23, 50, PROD, size=1200),
                               hourly(AUG_28, 14, 13, STAGING, size=800)])
        stats = promoter(storage).promote(as_of=AUG_29, days=1)
        self.assertEqual(stats['daily'], 2)
        self.assertEqual(storage.objects[f'{DB}/daily/{PROD}/db_2026-08-28.dump'][0], 1200)  # the last one
        self.assertEqual(storage.objects[f'{DB}/daily/{STAGING}/db_2026-08-28.dump'][0], 800)
        self.assertEqual(sorted(s for s, _f in list_tier(storage, storage.ensure_folder(DB), DAILY)),
                         [PROD, STAGING])

    def test_copy_keeps_server_and_records_source(self):
        storage = FakeStorage([hourly(AUG_28, 23, 50, PROD)])
        promoter(storage).promote(as_of=AUG_29, days=1)
        metadata = storage.objects[f'{DB}/daily/{PROD}/db_2026-08-28.dump'][1]
        self.assertEqual(metadata['ip_address'], PROD)
        self.assertEqual(metadata['tier'], DAILY)
        self.assertEqual(metadata['backup_time'], '2026-08-28T23:50:00')
        self.assertEqual(metadata['promoted_from'], f'{DB}/hourly/2026/08/28/db_2026_08_28_23_50_00.dump')

    def test_rerun_is_a_noop(self):
        storage = FakeStorage([hourly(AUG_28, 23, 50, PROD), hourly(AUG_28, 14, 13, STAGING)])
        promoter(storage).promote(as_of=AUG_29, days=1)
        stats = promoter(storage).promote(as_of=AUG_29, days=1)
        self.assertEqual((stats['daily'], stats['skipped']), (0, 2))
        self.assertEqual(len(storage.copies), 2)

    def test_root_copy_from_before_per_server_counts_for_the_day(self):
        storage = FakeStorage([hourly(AUG_28, 23, 50, PROD), hourly(AUG_28, 14, 13, STAGING),
                               (f'{DB}/daily/db_2026-08-28.dump', (1000, {'ip_address': PROD}))])
        stats = promoter(storage).promote(as_of=AUG_29, days=1)
        self.assertEqual((stats['daily'], stats['skipped']), (0, 2))
        self.assertFalse([k for k in storage.objects if f'/daily/{PROD}/' in k or f'/daily/{STAGING}/' in k])

    def test_resume_starts_after_the_newest_copy_from_any_server(self):
        aug_27 = datetime.date(2026, 8, 27)
        storage = FakeStorage([hourly(aug_27, 23, 50, STAGING), hourly(AUG_28, 23, 50, PROD),
                               (f'{DB}/daily/{PROD}/db_2026-08-27.dump', (1000, {'ip_address': PROD}))])
        stats = promoter(storage).promote(as_of=AUG_29, days=14, resume=True)
        # the 27th is behind prod's newest copy, so staging's dump for it is not looked at
        self.assertEqual(stats['daily'], 1)
        self.assertNotIn(f'{DB}/daily/{STAGING}/db_2026-08-27.dump', storage.objects)
        # a full scan does pick it up
        stats = promoter(storage).promote(as_of=AUG_29, days=14)
        self.assertEqual(stats['daily'], 1)
        self.assertIn(f'{DB}/daily/{STAGING}/db_2026-08-27.dump', storage.objects)

    def test_unknown_server(self):
        storage = FakeStorage([(f'{DB}/hourly/2026/08/28/db_2026_08_28_10_00_00.dump', (1000, {}))])
        promoter(storage).promote(as_of=AUG_29, days=1)
        self.assertIn(f'{DB}/daily/unknown/db_2026-08-28.dump', storage.objects)

    def test_server_of_is_key_safe(self):
        self.assertEqual(server_of({'metadata': {'ip_address': '2001:db8::1/64'}}), '2001_db8__1_64')
        self.assertEqual(server_of({'metadata': {'ip_address': ' '}}), 'unknown')
        self.assertEqual(server_of({}), 'unknown')


def daily(day, server, size=1000):
    folder = f'{DB}/daily/{server}' if server else f'{DB}/daily'
    return f'{folder}/db_{day:%Y-%m-%d}.dump', (size, {'ip_address': server or PROD, 'tier': DAILY})


SEP_1 = datetime.date(2026, 9, 1)


class PromoteMonthlyTest(unittest.TestCase):

    def test_one_copy_per_server_from_its_last_daily(self):
        storage = FakeStorage([daily(datetime.date(2026, 8, 30), PROD), daily(datetime.date(2026, 8, 31), PROD, 1300),
                               daily(datetime.date(2026, 8, 20), STAGING, 700)])
        stats = promoter(storage).promote(as_of=SEP_1, days=1)
        self.assertEqual(stats['monthly'], 2)
        self.assertEqual(storage.objects[f'{DB}/monthly/{PROD}/db_2026-08.dump'][0], 1300)
        self.assertEqual(storage.objects[f'{DB}/monthly/{STAGING}/db_2026-08.dump'][0], 700)

    def test_current_month_waits(self):
        storage = FakeStorage([daily(datetime.date(2026, 8, 30), PROD)])
        stats = promoter(storage).promote(as_of=datetime.date(2026, 8, 31), days=1)
        self.assertEqual(stats['monthly'], 0)

    def test_switch_over_month_is_archived_per_server_only(self):
        # root copies from before the change plus per-server ones for the rest of the month
        storage = FakeStorage([daily(datetime.date(2026, 8, 25), ''), daily(datetime.date(2026, 8, 28), ''),
                               daily(datetime.date(2026, 8, 31), PROD)])
        stats = promoter(storage).promote(as_of=SEP_1, days=1)
        self.assertEqual(stats['monthly'], 1)
        self.assertIn(f'{DB}/monthly/{PROD}/db_2026-08.dump', storage.objects)
        self.assertNotIn(f'{DB}/monthly/db_2026-08.dump', storage.objects)

    def test_month_with_only_root_copies_gets_a_root_monthly(self):
        storage = FakeStorage([daily(datetime.date(2026, 7, 31), '')])
        promoter(storage).promote(as_of=SEP_1, days=1)
        self.assertIn(f'{DB}/monthly/db_2026-07.dump', storage.objects)

    def test_root_monthly_means_the_month_is_done(self):
        storage = FakeStorage([daily(datetime.date(2026, 7, 31), PROD),
                               (f'{DB}/monthly/db_2026-07.dump', (1000, {'ip_address': PROD}))])
        stats = promoter(storage).promote(as_of=SEP_1, days=1)
        self.assertEqual((stats['monthly'], stats['skipped']), (0, 1))


class PruneTest(unittest.TestCase):

    def test_prunes_every_server(self):
        storage = FakeStorage([daily(datetime.date(2026, 5, 1), PROD), daily(datetime.date(2026, 5, 1), STAGING),
                               daily(datetime.date(2026, 5, 1), ''), daily(datetime.date(2026, 8, 28), PROD),
                               hourly(datetime.date(2026, 8, 1), 10, 0, PROD), hourly(AUG_28, 10, 0, STAGING)])
        deleted = promoter(storage).prune(as_of=AUG_29, expire_days={'hourly': 15, 'daily': 91, 'monthly': None})
        self.assertEqual(deleted, 4)
        self.assertEqual(sorted(storage.objects), sorted([daily(datetime.date(2026, 8, 28), PROD)[0],
                                                          hourly(AUG_28, 10, 0, STAGING)[0]]))
        self.assertEqual(sorted(s for s, _f in list_tier(storage, storage.ensure_folder(DB), MONTHLY)), [])


if __name__ == '__main__':
    unittest.main()
