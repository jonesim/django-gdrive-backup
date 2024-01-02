import hashlib
import os
from io import BytesIO

from django.conf import settings
from django.core.files import File
from django.core.files.storage import get_storage_class


class BaseBackup:

    def __init__(self, base_backup_dir, logger):
        self.logger = logger
        self.base_backup_dir = base_backup_dir
        self._storage = None

    def get_existing_backup_files(self, backup_dir_prefix, extra_query=None):
        # List to store file names
        existing_files = []

        # Django storages doesn't handle nested directories in the same way as Google Drive.
        # Instead, we use the directory prefix to filter relevant files.
        # For AWS S3, this would correspond to the 'folder' in the bucket.

        # Iterate over all files in the storage
        storage = self.get_storages()
        for file_name in storage.listdir(backup_dir_prefix)[
            1]:  # [1] is for files, [0] would be for directories
            # Apply any extra query filtering here if necessary
            if extra_query and not self.matches_extra_query(file_name, extra_query):
                continue

            # Add the file path (or name) to the list
            existing_files.append(os.path.join(backup_dir_prefix, file_name))

        return existing_files

    def matches_extra_query(self, file_name, extra_query):
        # Implement your custom filtering logic based on file_name and extra_query criteria
        # Return True if the file matches the extra query criteria, False otherwise
        pass

    @staticmethod
    def get_md5(f):
        return f.get('md5Checksum')

    def get_file_hashes(self, directory, get_hash=None, extra_query=None):
        """
        :param directory: Can be string of directory name/path or dictionary with google directory id
        :param get_hash:
        :param extra_query:
        :return:
        """
        if get_hash is None:
            get_hash = self.get_md5
        files = self.get_existing_backup_files(directory, extra_query=extra_query)
        file_hashes = {}
        for f in files:
            file_hashes.setdefault(f['name'], []).append(get_hash(f))
        return file_hashes

    @staticmethod
    def md5sum(filename, block_size=65536):
        file_hash = hashlib.md5()
        with open(filename, "rb") as f:
            for block in iter(lambda: f.read(block_size), b""):
                file_hash.update(block)
        return file_hash.hexdigest()

    def check_upload(self, storage_file_id, local_file_path):
        storage = self.get_storages()
        # Check if the file exists in the storage
        if not storage.exists(storage_file_id):
            return False

        # Calculate MD5 checksum for the local file
        md5_local = self.md5sum(local_file_path)

        # Read and calculate MD5 checksum for the file in storage
        try:
            with storage.open(storage_file_id, 'rb') as storage_file:
                file_content = storage_file.read()
        except FileNotFoundError:
            # Handle the case where the file is not found in the storage
            return False

        md5_storage = hashlib.md5(file_content).hexdigest()
        # Compare MD5 checksums
        return md5_local == md5_storage

    def upload_file(self, local_file_path, upload_filename):
        storage = self.get_storages()
        with open(local_file_path, 'rb') as f:
            file = File(f)
            storage_file_id = storage.save(upload_filename, file)
        return storage.url(storage_file_id)

    def download_file(self, storage_file_path, local_file_path):
        # Open the file from storage and write its content to a local file
        storage = self.get_storages()
        with storage.open(storage_file_path, 'rb') as storage_file:
            with open(local_file_path, 'wb') as local_file:
                local_file.write(storage_file.read())

    def get_file_contents(self, file_path, local_folder=None):
        """
        Fetches the content of a file from storage.
        :param file_path: Path of the file in storage.
        :param local_folder: If set, it will download the file to the local drive and return the filename.
        :return: If local_folder is set, the file name; otherwise, a BytesIO stream.
        """
        storage = self.get_storages()
        if not storage.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        if local_folder:
            # Download file to local folder
            local_file_path = os.path.join(local_folder, os.path.basename(file_path))
            with storage.open(file_path, 'rb') as storage_file:
                with open(local_file_path, 'wb') as local_file:
                    local_file.write(storage_file.read())
            return local_file_path
        else:
            # Return file content as BytesIO stream
            with storage.open(file_path, 'rb') as storage_file:
                file_content = storage_file.read()
            return BytesIO(file_content)

    def trash_file(self, file_path):
        storage = self.get_storages()
        if storage.exists(file_path):
            storage.delete(file_path)
        raw_path = file_path.split('.')[0]
        app_file_path = f'{raw_path}.json'
        if storage.exists(app_file_path):
            storage.delete(app_file_path)

    def get_storages(self):
        if self._storage is None:
            storage_class = get_storage_class(settings.BACKUP_STORAGE_CLASS)
            self._storage = storage_class(**settings.BACKUP_STORAGE_KWARGS)
        return self._storage
