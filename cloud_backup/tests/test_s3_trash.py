"""The S3 backend's trash: a versioned bucket's hidden versions, restored by copying
forward. Skipped where boto3 is not installed."""
import datetime
import unittest
from unittest.mock import MagicMock

from django.test import SimpleTestCase

try:
    from cloud_backup.storages.s3 import S3Storage
except ImportError:  # pragma: no cover - a Google Drive-only host project
    S3Storage = None

UTC = datetime.timezone.utc
KEY = 'django_backup/db/hourly/2026/08/28/db_2026_08_28_07_50_02.dump'
MONTHLY = 'django_backup/db/monthly/db_2026-07.dump'


def version(key, version_id, when, latest=False, size=100):
    return {'Key': key, 'VersionId': version_id, 'LastModified': when, 'Size': size, 'IsLatest': latest}


def marker(key, version_id, when, latest=True):
    return {'Key': key, 'VersionId': version_id, 'LastModified': when, 'IsLatest': latest}


@unittest.skipIf(S3Storage is None, 'boto3 is not installed')
class S3TrashTest(SimpleTestCase):

    def storage(self, versions, markers):
        storage = S3Storage.__new__(S3Storage)
        storage.bucket = 'b'
        storage.s3 = MagicMock()
        storage.transfer_config = None
        storage.s3.get_paginator.return_value.paginate.return_value = [
            {'Versions': versions, 'DeleteMarkers': markers}]
        return storage

    def test_hidden_versions_are_the_trash(self):
        # KEY was overwritten: the old version is hidden by the new one. MONTHLY was
        # deleted: hidden by a marker. The current versions are not trash
        storage = self.storage(
            [version(KEY, 'old', datetime.datetime(2026, 8, 28, 6, 50, tzinfo=UTC)),
             version(KEY, 'new', datetime.datetime(2026, 8, 28, 12, 0, tzinfo=UTC), latest=True),
             version(MONTHLY, 'm1', datetime.datetime(2026, 8, 1, 6, 0, tzinfo=UTC))],
            [marker(MONTHLY, 'mk', datetime.datetime(2026, 8, 28, 12, 1, tzinfo=UTC))])
        self.assertTrue(storage.supports_trash)
        self.assertFalse(storage.supports_empty_trash)
        trash = storage.list_files({'id': 'django_backup/db'}, deleted=True)
        self.assertEqual([(f['name'], f['id']) for f in trash],
                         [('db_2026_08_28_07_50_02.dump', f'{KEY}?versionId=old'),
                          ('db_2026-07.dump', f'{MONTHLY}?versionId=m1')])
        # hidden when its successor arrived - the start of the purge clock
        self.assertEqual(trash[0]['hidden'], S3Storage.to_local_naive(datetime.datetime(2026, 8, 28, 12, 0, tzinfo=UTC)))
        self.assertEqual(trash[1]['hidden'], S3Storage.to_local_naive(datetime.datetime(2026, 8, 28, 12, 1, tzinfo=UTC)))

    def test_nothing_hidden_on_an_unversioned_bucket(self):
        storage = self.storage([version(KEY, 'null', datetime.datetime(2026, 8, 28, 6, 50, tzinfo=UTC), latest=True)], [])
        self.assertEqual(storage.list_files({'id': 'django_backup/db'}, deleted=True), [])

    def test_restore_copies_the_version_forward(self):
        storage = self.storage([], [])
        storage.s3.head_object.side_effect = [
            {'ContentLength': 100, 'Metadata': {'md5': 'abc', 'ip_address': '1_2_3_4'}, 'ContentType': 'application/octet-stream'},
            # get_file after the copy
            {'ContentLength': 100, 'ETag': '"abc"', 'LastModified': datetime.datetime(2026, 8, 29, 9, 0, tzinfo=UTC),
             'Metadata': {'md5': 'abc'}}]
        restored = storage.restore_deleted(f'{KEY}?versionId=old')
        storage.s3.head_object.assert_any_call(Bucket='b', Key=KEY, VersionId='old')
        storage.s3.copy_object.assert_called_once_with(
            Bucket='b', Key=KEY, CopySource={'Bucket': 'b', 'Key': KEY, 'VersionId': 'old'},
            MetadataDirective='REPLACE', Metadata={'md5': 'abc', 'ip_address': '1_2_3_4'},
            ContentType='application/octet-stream')
        storage.s3.delete_object.assert_not_called()
        self.assertEqual(restored['id'], KEY)

    def test_restore_checks_the_size(self):
        storage = self.storage([], [])
        storage.s3.head_object.side_effect = [
            {'ContentLength': 100, 'Metadata': {}},
            {'ContentLength': 5, 'ETag': '"x"', 'LastModified': datetime.datetime(2026, 8, 29, tzinfo=UTC), 'Metadata': {}}]
        with self.assertRaises(IOError):
            storage.restore_deleted(f'{KEY}?versionId=old')
