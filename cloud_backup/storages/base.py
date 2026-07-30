import datetime
import hashlib


class StorageFileNotFound(Exception):
    pass


class BackupStorage:
    """
    Interface for a backup destination. Implementations normalise files to plain dicts:

        {'id': str,          # backend reference - gdrive file id, s3 key, azure blob name
         'name': str,        # filename without any path
         'size': int,
         'hash': str,        # md5 hex digest where the backend can supply one, else None
         'created': datetime,   # naive local time, to match PruneBackups comparisons
         'modified': datetime,  # naive local time
         'metadata': dict,   # custom key/value pairs stored with the file
         'web_link': str}    # browser URL if the backend has one, else None

    Folders are opaque dict handles produced by ensure_folder/get_folder and must only
    be passed back to the storage that created them. Path arguments use '/' separators.
    """

    supports_trash = False
    lock = {}  # object-lock config where the backend supports it (currently only s3)

    def lock_days(self, kind):
        """Object-lock retention days configured for 'db' or 'file' uploads, or None
        when the backend has no object lock configured."""
        return self.lock.get(kind + '_days')

    def ensure_folder(self, path, parent=None):
        """Return a folder handle for path (relative to parent), creating it if needed."""
        raise NotImplementedError

    def get_folder(self, path, parent=None):
        """Return a folder handle or None if the folder does not exist."""
        raise NotImplementedError

    def list_files(self, folder, metadata_filter=None, deleted=False, include_metadata=False):
        """
        List files directly inside a folder as normalised dicts.
        :param folder: folder handle
        :param metadata_filter: dict of metadata key/values every returned file must match
        :param deleted: list trashed files instead (backends without trash return [])
        :param include_metadata: guarantee 'metadata' is populated (may cost extra
                                 requests on backends that can't list metadata)
        """
        raise NotImplementedError

    def list_folders(self, folder):
        """List the sub-folders directly inside a folder as folder handles
        (each usable with list_files/list_folders)."""
        raise NotImplementedError

    def walk(self, folder, include_metadata=False):
        """Yield (relative_path, file_dict) for every file under folder, recursively.
        relative_path is '' for direct children, otherwise 'sub/folders'.
        include_metadata guarantees 'metadata' is populated (may cost extra requests
        on backends that can't list metadata)."""
        raise NotImplementedError

    def get_file(self, file_id):
        """Return the normalised dict for a file reference, or raise StorageFileNotFound."""
        raise NotImplementedError

    def find_file(self, folder, name):
        """Return the normalised dict for a named file in a folder, or raise StorageFileNotFound."""
        raise NotImplementedError

    def upload(self, folder, name, stream, metadata=None, lock_days=None):
        """Upload a binary stream and return the normalised dict of the stored file.
        lock_days applies object-lock retention where the backend supports it and is
        ignored otherwise."""
        raise NotImplementedError

    def keep_version(self, stored_file, lock_days=None):
        """Preserve the current contents of a stored file so that an upload under the
        same name cannot destroy it. Backends use their cheapest native mechanism
        (server-side copy, snapshot or rename) - no data is re-transferred and no
        delete permission is needed. Returns a reference to the preserved copy where
        the backend has one."""
        raise NotImplementedError('This storage backend cannot keep file versions')

    @staticmethod
    def version_name(name):
        return f'{name}.{datetime.datetime.today().strftime("%Y_%m_%d_%H_%M")}'

    def extend_retention(self, folder, min_days, workers=8):
        """Ensure every file under folder keeps at least min_days of object-lock
        retention. No-op (returns None) on backends without object lock."""
        return None

    def verify_upload(self, stored_file, local_path):
        """Check a completed upload against the local source file."""
        raise NotImplementedError

    def download(self, stored_file, local_folder=None):
        """Download a file. Returns the filename written when local_folder is given,
        otherwise a BytesIO of the contents."""
        raise NotImplementedError

    def delete(self, file_id):
        """Remove a file - to trash where supported, otherwise permanently."""
        raise NotImplementedError

    def restore_deleted(self, file_id):
        raise NotImplementedError('This storage backend does not support undelete')

    def empty_trash(self):
        """Permanently remove trashed files. No-op where there is no trash."""

    def storage_info(self, folder=None):
        """
        Describe the destination for display.
        :return: {'name': location name, 'web_link': browser URL or None,
                  'used': bytes used or None, 'limit': bytes available or None}
        """
        raise NotImplementedError

    def protection_info(self):
        """
        Describe the destination's data-protection configuration (soft delete,
        versioning, WORM/immutability) for display. Costs a few extra API requests,
        so only call it for info pages, not during backups.
        :return: list of {'label': str,
                          'status': 'Enabled'|'Disabled'|'Suspended'|'Unknown',
                          'detail': str or None}
        """
        return []

    @staticmethod
    def md5sum(filename, block_size=65536):
        file_hash = hashlib.md5()
        with open(filename, 'rb') as f:
            for block in iter(lambda: f.read(block_size), b''):
                file_hash.update(block)
        return file_hash.hexdigest()

    @staticmethod
    def to_local_naive(dt):
        """Convert an aware datetime to naive local time, matching how Google Drive
        timestamps have always been normalised (PruneBackups compares them with
        datetime.today())."""
        if dt is None:
            return None
        if dt.tzinfo is not None:
            dt = dt.astimezone(tz=None).replace(tzinfo=None)
        return dt

    @staticmethod
    def matches_metadata(file_dict, metadata_filter):
        if not metadata_filter:
            return True
        metadata = file_dict.get('metadata') or {}
        return all(metadata.get(k) == v for k, v in metadata_filter.items())
