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

    def walk(self, folder):
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

    def upload(self, folder, name, stream, metadata=None):
        key = f"{folder['id']}/{name}"
        if metadata:
            metadata = {k: str(v) for k, v in metadata.items()}
        self.container.get_blob_client(key).upload_blob(stream, overwrite=True, metadata=metadata,
                                                        validate_content=True)
        return self.get_file(key)

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
