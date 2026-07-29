import concurrent.futures
import logging
import threading

import boto3
import requests
from boto3.s3.transfer import TransferConfig


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


class BackupAzureToS3:
    """
    Incremental, copy-only backup of Azure blob storage to an S3-compatible bucket
    (e.g. Backblaze B2). Nothing is ever deleted from the destination; on B2, version
    retention of overwritten files is controlled by the bucket's lifecycle rules.

    A file is copied when it is missing from the destination, its size differs, or the
    source blob was modified after the destination copy was uploaded (upload always
    happens after the source write it captured, so destination timestamps only lag a
    source change when a backup is needed).

    :param storage: a django-storages AzureStorage instance - provides the container
                    client for a single paginated listing and open() for streaming
    :param progress: optional callback receiving the stats dict after each copy
    """

    def __init__(self, storage, access_key_id, secret_key, bucket, endpoint_url=None,
                 logger=None, progress=None, workers=8):
        if endpoint_url is None:
            endpoint_url = b2_s3_endpoint(access_key_id, secret_key)
        self.s3 = boto3.client('s3',
                               aws_access_key_id=access_key_id,
                               aws_secret_access_key=secret_key,
                               endpoint_url=endpoint_url,
                               # https://s3.<region>.backblazeb2.com
                               region_name=endpoint_url.split('.')[1])
        self.storage = storage
        self.bucket = bucket
        self.logger = logger or logging.getLogger(__name__)
        self.progress = progress
        self.workers = workers
        self.transfer_config = TransferConfig(multipart_threshold=64 * 1024 * 1024)

    def list_source(self, prefix):
        return {b.name: b for b in self.storage.client.list_blobs(name_starts_with=prefix)}

    def list_destination(self, prefix):
        files = {}
        for page in self.s3.get_paginator('list_objects_v2').paginate(Bucket=self.bucket, Prefix=prefix):
            for s3_object in page.get('Contents', []):
                files[s3_object['Key']] = s3_object
        return files

    @staticmethod
    def needs_copy(blob, s3_object):
        if s3_object is None or blob.size != s3_object['Size']:
            return True
        return blob.last_modified > s3_object['LastModified']

    def copy_file(self, blob, dest_key):
        # src_last_modified_millis is the file-info key rclone uses for modtime, so
        # files uploaded here are seen as unchanged by rclone (and vice versa)
        extra = {'Metadata': {'src_last_modified_millis': str(int(blob.last_modified.timestamp() * 1000))}}
        content_type = getattr(blob.content_settings, 'content_type', None)
        if content_type:
            extra['ContentType'] = content_type
        with self.storage.open(blob.name, 'rb') as f:
            self.s3.upload_fileobj(f, self.bucket, dest_key, ExtraArgs=extra, Config=self.transfer_config)

    def backup(self, prefix, dest_prefix=None):
        """ Copy new/changed files under an Azure prefix to the bucket
        :param prefix: source folder in the Azure container, with trailing /
        :param dest_prefix: key prefix in the bucket, defaults to prefix
        :return: stats dict
        """
        if dest_prefix is None:
            dest_prefix = prefix
        source = self.list_source(prefix)
        destination = self.list_destination(dest_prefix)
        to_copy = [(blob, dest_prefix + name[len(prefix):]) for name, blob in source.items()
                   if self.needs_copy(blob, destination.get(dest_prefix + name[len(prefix):]))]

        stats = {'source_files': len(source), 'to_copy': len(to_copy), 'copied': 0,
                 'copied_bytes': 0, 'errors': []}
        lock = threading.Lock()

        def copy(item):
            blob, dest_key = item
            for attempt in (1, 2):
                try:
                    self.copy_file(blob, dest_key)
                    with lock:
                        stats['copied'] += 1
                        stats['copied_bytes'] += blob.size
                        if self.progress:
                            self.progress(stats)
                    return
                except Exception as e:
                    if attempt == 2:
                        self.logger.exception(f'failed to copy {blob.name}')
                        with lock:
                            stats['errors'].append(f'{blob.name}: {e}')

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:
            list(pool.map(copy, to_copy))
        return stats
