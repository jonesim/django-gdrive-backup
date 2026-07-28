import hashlib


class BaseBackup:

    def __init__(self, storage, base_backup_dir, logger):
        self.storage = storage
        self.logger = logger
        self.base_backup_dir = storage.ensure_folder(base_backup_dir)

    def get_existing_backup_files(self, backup_dir, include_metadata=False):
        if isinstance(backup_dir, str):
            backup_dir = self.storage.ensure_folder(backup_dir, parent=self.base_backup_dir)
        return self.storage.list_files(backup_dir, include_metadata=include_metadata)

    @staticmethod
    def get_hash(f):
        return f.get('hash')

    def get_file_hashes(self, directory, get_hash=None, include_metadata=False):
        """
        :param directory: folder path string or a folder handle from the storage
        :param get_hash: optional override to hash a file dict differently
        :param include_metadata: pass True when get_hash reads file metadata
        """
        if get_hash is None:
            get_hash = self.get_hash
        files = self.get_existing_backup_files(directory, include_metadata=include_metadata)
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

    def check_upload(self, stored_file, local_file):
        return self.storage.verify_upload(stored_file, local_file)
