import concurrent.futures
import os
import threading
from datetime import datetime, timedelta, timezone
from io import BytesIO

import boto3
import requests
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError

from .base import BackupStorage, StorageFileNotFound


class B2AuthError(Exception):
    pass


def b2_s3_endpoint(key_id, application_key):
    """Backblaze B2 keys work with B2's S3-compatible API; the account's S3 endpoint
    is returned by b2_authorize_account so it does not need to be configured."""
    response = requests.get('https://api.backblazeb2.com/b2api/v2/b2_authorize_account',
                            auth=(key_id, application_key), timeout=30)
    if response.status_code != 200:
        raise B2AuthError(response.text)
    return response.json()['s3ApiUrl']


class S3Storage(BackupStorage):
    """
    Any S3-compatible destination: AWS S3, Backblaze B2 (pass b2=True to discover the
    endpoint from the key) or Cloudflare R2 (pass the account's endpoint_url and
    region='auto'). Folders are just key prefixes so they always "exist"; deletes are
    permanent unless the bucket itself has versioning or lifecycle rules.
    """

    supports_trash = False

    def __init__(self, bucket, access_key_id=None, secret_key=None, endpoint_url=None,
                 region=None, b2=False, lock=None):
        if b2 and endpoint_url is None:
            endpoint_url = b2_s3_endpoint(access_key_id, secret_key)
            if region is None:
                # https://s3.<region>.backblazeb2.com
                region = endpoint_url.split('.')[1]
        self.s3 = boto3.client('s3', aws_access_key_id=access_key_id, aws_secret_access_key=secret_key,
                               endpoint_url=endpoint_url, region_name=region)
        self.bucket = bucket
        self.lock = lock or {}
        self.lock_mode = self.lock.get('mode', 'COMPLIANCE')
        self.transfer_config = TransferConfig(multipart_threshold=64 * 1024 * 1024)

    def _lock_args(self, lock_days):
        """Object-lock parameters for an upload/copy - the bucket must have been
        created with Object Lock enabled or these requests will be rejected."""
        if not lock_days:
            return {}
        return {'ObjectLockMode': self.lock_mode,
                'ObjectLockRetainUntilDate': datetime.now(timezone.utc) + timedelta(days=lock_days)}

    @staticmethod
    def _folder_handle(prefix):
        return {'id': prefix, 'name': prefix.rsplit('/', 1)[-1], 'web_link': None}

    def ensure_folder(self, path, parent=None):
        prefix = f"{parent['id']}/{path}" if parent else path
        return self._folder_handle(prefix.strip('/'))

    def get_folder(self, path, parent=None):
        return self.ensure_folder(path, parent)

    def normalise(self, key, size, etag, modified, metadata=None):
        etag = (etag or '').strip('"')
        if metadata is None and (self._is_multipart(etag)):
            metadata = self._head_metadata(key)
        metadata = metadata or {}
        file_hash = metadata.get('md5') if self._is_multipart(etag) else etag
        modified = self.to_local_naive(modified)
        return {'id': key,
                'name': key.rsplit('/', 1)[-1],
                'size': size,
                'hash': file_hash or None,
                'created': modified,  # S3 only records last modification
                'modified': modified,
                'metadata': metadata,
                'web_link': None}

    @staticmethod
    def _is_multipart(etag):
        return '-' in etag

    def _head_metadata(self, key):
        return self.s3.head_object(Bucket=self.bucket, Key=key).get('Metadata', {})

    def list_files(self, folder, metadata_filter=None, deleted=False, include_metadata=False):
        if deleted:
            return []
        prefix = folder['id'] + '/'
        need_metadata = include_metadata or bool(metadata_filter)
        files = []
        for page in self.s3.get_paginator('list_objects_v2').paginate(Bucket=self.bucket,
                                                                      Prefix=prefix, Delimiter='/'):
            for s3_object in page.get('Contents', []):
                if s3_object['Key'] == prefix:
                    continue  # zero-byte directory marker
                metadata = self._head_metadata(s3_object['Key']) if need_metadata else None
                f = self.normalise(s3_object['Key'], s3_object['Size'], s3_object['ETag'],
                                   s3_object['LastModified'], metadata=metadata)
                if self.matches_metadata(f, metadata_filter):
                    files.append(f)
        return files

    def walk(self, folder):
        prefix = folder['id'] + '/'
        for page in self.s3.get_paginator('list_objects_v2').paginate(Bucket=self.bucket, Prefix=prefix):
            for s3_object in page.get('Contents', []):
                if s3_object['Key'].endswith('/'):
                    continue
                relative = s3_object['Key'][len(prefix):]
                path = relative.rsplit('/', 1)[0] if '/' in relative else ''
                yield path, self.normalise(s3_object['Key'], s3_object['Size'], s3_object['ETag'],
                                           s3_object['LastModified'])

    def get_file(self, file_id):
        try:
            head = self.s3.head_object(Bucket=self.bucket, Key=file_id)
        except ClientError as e:
            if e.response['Error']['Code'] in ('404', 'NoSuchKey', 'NotFound'):
                raise StorageFileNotFound(file_id)
            raise
        return self.normalise(file_id, head['ContentLength'], head['ETag'], head['LastModified'],
                              metadata=head.get('Metadata', {}))

    def find_file(self, folder, name):
        return self.get_file(f"{folder['id']}/{name}")

    def upload(self, folder, name, stream, metadata=None, lock_days=None):
        key = f"{folder['id']}/{name}"
        extra_args = {'Metadata': {k: str(v) for k, v in metadata.items()}} if metadata else {}
        extra_args.update(self._lock_args(lock_days))
        self.s3.upload_fileobj(stream, self.bucket, key, ExtraArgs=extra_args or None,
                               Config=self.transfer_config)
        return self.get_file(key)

    def keep_version(self, stored_file, lock_days=None):
        # server-side managed copy (handles > 5GB multipart) - no data transfer or
        # delete permission needed, and the copy gets its own object lock
        version_key = self.version_name(stored_file['id'])
        self.s3.copy({'Bucket': self.bucket, 'Key': stored_file['id']}, self.bucket, version_key,
                     ExtraArgs=self._lock_args(lock_days) or None, Config=self.transfer_config)
        return version_key

    def extend_retention(self, folder, min_days, workers=8):
        """Ensure every object under folder keeps at least min_days of object-lock
        retention. Extending retention is always allowed; shortening never is, so this
        is safe to re-run. Costs 1-2 API calls per object - schedule it rather than
        running it with every backup."""
        target = datetime.now(timezone.utc) + timedelta(days=min_days)
        keys = []
        for page in self.s3.get_paginator('list_objects_v2').paginate(Bucket=self.bucket,
                                                                      Prefix=folder['id'] + '/'):
            keys += [s3_object['Key'] for s3_object in page.get('Contents', [])
                     if not s3_object['Key'].endswith('/')]
        stats = {'checked': 0, 'extended': 0, 'errors': []}
        stats_lock = threading.Lock()

        def extend(key):
            try:
                head = self.s3.head_object(Bucket=self.bucket, Key=key)
                retain_until = head.get('ObjectLockRetainUntilDate')
                if retain_until is None or retain_until < target:
                    self.s3.put_object_retention(
                        Bucket=self.bucket, Key=key,
                        Retention={'Mode': head.get('ObjectLockMode') or self.lock_mode,
                                   'RetainUntilDate': target})
                    with stats_lock:
                        stats['extended'] += 1
            except Exception as e:
                with stats_lock:
                    stats['errors'].append(f'{key}: {e}')
            with stats_lock:
                stats['checked'] += 1

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(extend, keys))
        return stats

    def verify_upload(self, stored_file, local_path):
        saved = self.get_file(stored_file['id'])
        if saved['size'] != os.path.getsize(local_path):
            return False
        # boto3 checksums every part in transit, so when no comparable md5 is available
        # (multipart upload with no md5 metadata) a size match is the best remaining check
        if saved['hash']:
            return saved['hash'] == self.md5sum(local_path)
        return True

    def download(self, stored_file, local_folder=None):
        if local_folder:
            self.s3.download_file(self.bucket, stored_file['id'],
                                  os.path.join(local_folder, stored_file['name']),
                                  Config=self.transfer_config)
            return stored_file['name']
        stream = BytesIO()
        self.s3.download_fileobj(self.bucket, stored_file['id'], stream, Config=self.transfer_config)
        stream.seek(0)
        return stream

    def delete(self, file_id):
        self.s3.delete_object(Bucket=self.bucket, Key=file_id)

    def storage_info(self, folder=None):
        name = f's3://{self.bucket}'
        if folder:
            name += f"/{folder['id']}"
        return {'name': name, 'web_link': None, 'used': None, 'limit': None}
