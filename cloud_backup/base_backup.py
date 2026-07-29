import hashlib

from .config import BackupConfig, CHANGED_HISTORY, CHANGED_OVERWRITE, CHANGED_PROTECT  # noqa: F401 re-export


class BaseBackup:

    def __init__(self, storage, base_backup_dir, logger, config=None):
        self.storage = storage
        self.logger = logger
        self.base_backup_dir = storage.ensure_folder(base_backup_dir)
        # no config = the default config resolved from the legacy global settings
        self.config = config if config is not None else BackupConfig()
        self.encryption_key = self.config.encryption_key

    def get_existing_backup_files(self, backup_dir, include_metadata=False):
        if isinstance(backup_dir, str):
            backup_dir = self.storage.ensure_folder(backup_dir, parent=self.base_backup_dir)
        return self.storage.list_files(backup_dir, include_metadata=include_metadata)

    @staticmethod
    def get_hash(f):
        return f.get('hash')

    @staticmethod
    def content_hash(f):
        """The md5 of the backed-up content: prefers the md5 recorded in metadata at
        upload time (still the plaintext hash when the stored bytes are encrypted)
        over the storage backend's hash of the stored bytes."""
        return (f.get('metadata') or {}).get('md5') or f.get('hash')

    def get_files_by_name(self, directory, include_metadata=False):
        """
        :param directory: folder path string or a folder handle from the storage
        :return: {file name: [file dicts]} for the files already in the backup
        """
        files_by_name = {}
        for f in self.get_existing_backup_files(directory, include_metadata=include_metadata):
            files_by_name.setdefault(f['name'], []).append(f)
        return files_by_name

    def get_file_hashes(self, directory, get_hash=None, include_metadata=False):
        """
        :param directory: folder path string or a folder handle from the storage
        :param get_hash: optional override to hash a file dict differently
        :param include_metadata: pass True when get_hash reads file metadata
        """
        if get_hash is None:
            get_hash = self.get_hash
        return {name: [get_hash(f) for f in files]
                for name, files in self.get_files_by_name(directory, include_metadata).items()}

    @staticmethod
    def md5sum(filename, block_size=65536):
        file_hash = hashlib.md5()
        with open(filename, "rb") as f:
            for block in iter(lambda: f.read(block_size), b""):
                file_hash.update(block)
        return file_hash.hexdigest()

    def check_upload(self, stored_file, local_file):
        return self.storage.verify_upload(stored_file, local_file)
