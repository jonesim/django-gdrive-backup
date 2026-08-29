import os
from io import BytesIO

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import ContainerClient

from .base import BackupStorage, StorageFileNotFound


class AzureStorage(BackupStorage):
    """
    Azure Blob Storage destination. Configure with either a connection string or an
    account_url plus credential (account key or SAS token). Folders are key prefixes;
    deletes are permanent unless the storage account has soft delete enabled.
    """

    supports_trash = False

    def __init__(self, container, connection_string=None, account_url=None, credential=None):
        self._connection_string = connection_string
        self._account_url = account_url
        self._credential = credential
        if connection_string:
            self.container = ContainerClient.from_connection_string(connection_string, container)
        else:
            self.container = ContainerClient(account_url, container, credential=credential)

    @staticmethod
    def _folder_handle(prefix):
        return {'id': prefix, 'name': prefix.rsplit('/', 1)[-1], 'web_link': None}

    def ensure_folder(self, path, parent=None):
        prefix = f"{parent['id']}/{path}" if parent else path
        return self._folder_handle(prefix.strip('/'))

    def get_folder(self, path, parent=None):
        return self.ensure_folder(path, parent)

    def normalise(self, blob):
        metadata = blob.metadata or {}
        content_md5 = getattr(blob.content_settings, 'content_md5', None)
        file_hash = bytes(content_md5).hex() if content_md5 else metadata.get('md5')
        return {'id': blob.name,
                'name': blob.name.rsplit('/', 1)[-1],
                'size': blob.size,
                'hash': file_hash,
                'created': self.to_local_naive(blob.creation_time),
                'modified': self.to_local_naive(blob.last_modified),
                'metadata': metadata,
                'web_link': None}

    def list_files(self, folder, metadata_filter=None, deleted=False, include_metadata=False):
        if deleted:
            return []
        prefix = folder['id'] + '/'
        files = []
        for blob in self.container.walk_blobs(name_starts_with=prefix, delimiter='/', include=['metadata']):
            if not hasattr(blob, 'size'):
                continue  # BlobPrefix entries are sub-folders
            f = self.normalise(blob)
            if self.matches_metadata(f, metadata_filter):
                files.append(f)
        return files

    def list_folders(self, folder):
        prefix = folder['id'] + '/'
        return [self._folder_handle(blob.name.rstrip('/'))
                for blob in self.container.walk_blobs(name_starts_with=prefix, delimiter='/')
                if not hasattr(blob, 'size')]  # BlobPrefix entries are the sub-folders

    def walk(self, folder, include_metadata=False):
        # include_metadata is ignored - list_blobs always includes metadata below
        prefix = folder['id'] + '/'
        for blob in self.container.list_blobs(name_starts_with=prefix, include=['metadata']):
            relative = blob.name[len(prefix):]
            path = relative.rsplit('/', 1)[0] if '/' in relative else ''
            yield path, self.normalise(blob)

    def get_file(self, file_id):
        try:
            return self.normalise(self.container.get_blob_client(file_id).get_blob_properties())
        except ResourceNotFoundError:
            raise StorageFileNotFound(file_id)

    def find_file(self, folder, name):
        return self.get_file(f"{folder['id']}/{name}")

    def upload(self, folder, name, stream, metadata=None, lock_days=None):
        # lock_days is ignored - Azure immutability policies are container-level config
        key = f"{folder['id']}/{name}"
        if metadata:
            metadata = {k: str(v) for k, v in metadata.items()}
        self.container.get_blob_client(key).upload_blob(stream, overwrite=True, metadata=metadata,
                                                        validate_content=True)
        return self.get_file(key)

    def keep_version(self, stored_file, lock_days=None):
        # snapshots are Azure's cheap native versioning; the overwrite that follows
        # leaves the snapshot intact
        return self.container.get_blob_client(stored_file['id']).create_snapshot()

    def verify_upload(self, stored_file, local_path):
        saved = self.get_file(stored_file['id'])
        if saved['size'] != os.path.getsize(local_path):
            return False
        # each chunk is checksummed in transit (validate_content), so when no md5 is
        # recorded a size match is the best remaining check
        if saved['hash']:
            return saved['hash'] == self.md5sum(local_path)
        return True

    def download(self, stored_file, local_folder=None):
        downloader = self.container.get_blob_client(stored_file['id']).download_blob()
        if local_folder:
            with open(os.path.join(local_folder, stored_file['name']), 'wb') as f:
                downloader.readinto(f)
            return stored_file['name']
        return BytesIO(downloader.readall())

    def delete(self, file_id):
        self.container.get_blob_client(file_id).delete_blob()

    def storage_info(self, folder=None):
        name = f'azure://{self.container.container_name}'
        if folder:
            name += f"/{folder['id']}"
        return {'name': name, 'web_link': None, 'used': None, 'limit': None}

    def _service_client(self):
        from azure.storage.blob import BlobServiceClient
        if self._connection_string:
            return BlobServiceClient.from_connection_string(self._connection_string)
        return BlobServiceClient(self._account_url, credential=self._credential)

    @staticmethod
    def _protection_unknown(label, error):
        code = getattr(error, 'error_code', None) or type(error).__name__
        return {'label': label, 'status': 'Unknown', 'detail': f'could not query ({code})'}

    def destination_status(self, root=None):
        name = self.container.container_name
        try:
            self.container.get_container_properties()
        except Exception as e:  # noqa: BLE001 - the panel must always render
            code = getattr(e, 'error_code', None) or type(e).__name__
            if code in ('ContainerNotFound', 'ResourceNotFoundError'):
                return {'state': 'missing', 'label': 'Container', 'status': 'Disabled',
                        'detail': f'{name} does not exist'}
            if getattr(e, 'status_code', None) == 403 or code in ('AuthorizationFailure',
                                                                  'ClientAuthenticationError'):
                return {'state': 'denied', 'label': 'Container', 'status': 'Unknown',
                        'detail': f'these credentials cannot see {name}'}
            return {'state': 'unknown', 'label': 'Container', 'status': 'Unknown',
                    'detail': f'could not check {name} ({code})'}
        return {'state': 'ok', 'label': 'Container', 'status': 'Enabled', 'detail': f'{name} is accessible'}

    def protection_info(self, tier_prefixes=None, expire_days=None, purge_days=None):
        protection = []
        try:
            # account-level query - fails with a container-scoped SAS
            policy = self._service_client().get_service_properties().get('delete_retention_policy')
            if policy and policy.enabled:
                protection.append({'label': 'Soft delete', 'status': 'Enabled',
                                   'detail': f'deleted blobs recoverable for {policy.days} days'})
            else:
                protection.append({'label': 'Soft delete', 'status': 'Disabled',
                                   'detail': 'deleted blobs are gone immediately'})
        except Exception as e:  # noqa: BLE001 - always render the panel
            protection.append(self._protection_unknown('Soft delete', e))
        try:
            # versioning status is only exposed via the ARM management API, but when it
            # is enabled every blob carries a version id - check one blob as a proxy
            first = next(iter(self.container.list_blobs(results_per_page=1)), None)
            if first is None:
                protection.append({'label': 'Blob versioning', 'status': 'Unknown',
                                   'detail': 'no blobs in container to check'})
            else:
                version_id = self.container.get_blob_client(first.name).get_blob_properties().version_id
                protection.append(
                    {'label': 'Blob versioning', 'status': 'Enabled' if version_id else 'Disabled',
                     'detail': 'overwritten and deleted blobs are kept as previous versions'
                               if version_id else None})
        except Exception as e:  # noqa: BLE001
            protection.append(self._protection_unknown('Blob versioning', e))
        try:
            props = self.container.get_container_properties()
            worm = getattr(props, 'immutable_storage_with_versioning', None)
            details = []
            if getattr(worm, 'enabled', False):
                details.append('version-level immutability')
            if getattr(props, 'has_immutability_policy', False):
                details.append('container immutability policy')
            if getattr(props, 'has_legal_hold', False):
                details.append('legal hold')
            protection.append({'label': 'Immutability (WORM)',
                               'status': 'Enabled' if details else 'Disabled',
                               'detail': ', '.join(details) or None})
        except Exception as e:  # noqa: BLE001
            protection.append(self._protection_unknown('Immutability (WORM)', e))
        return protection
