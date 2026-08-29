import os

from google_client.drive import GoogleDrive

from .base import BackupStorage, StorageFileNotFound

FOLDER_MIME_TYPE = 'application/vnd.google-apps.folder'


class GDriveStorage(BackupStorage):
    """
    Google Drive destination through a service account. Top-level folders are either in
    a shared drive or a personal-drive folder shared with the service account
    (shared_with_me), matching how the package has always located its root folder.
    """

    supports_trash = True
    supports_empty_trash = True

    def __init__(self, credentials, shared_drive=None):
        self.drive = GoogleDrive(credentials, shared_drive=shared_drive)
        self.shared_drive = shared_drive

    def normalise(self, f):
        for time_key in ('createdTime', 'modifiedTime'):
            if isinstance(f.get(time_key), str):
                GoogleDrive.convert_dict_time(f, time_key)
        return {'id': f['id'],
                'name': f.get('name'),
                'size': int(f.get('size', 0)),
                'hash': f.get('md5Checksum'),
                'created': f.get('createdTime'),
                'modified': f.get('modifiedTime'),
                'metadata': f.get('appProperties', {}),
                'web_link': f.get('webViewLink')}

    def ensure_folder(self, path, parent=None):
        if parent is not None:
            return self._folder_handle(self.drive.find_create_folder(path, folder=parent['drive_folder']))
        if self.shared_drive:
            folder = self.drive.find_create_folder(path)
        else:
            folder = self.drive.find_create_folder(path, shared_with_me=True)
        return self._folder_handle(folder)

    def get_folder(self, path, parent=None):
        if parent is not None:
            folder = self.drive.get_folder(path, folder=parent['drive_folder'])
        elif self.shared_drive:
            folder = self.drive.get_folder(path)
        else:
            folder = self.drive.get_folder(path, shared_with_me=True)
        return self._folder_handle(folder)

    def _folder_handle(self, drive_folder):
        if not drive_folder:
            return None
        if 'drive_folder' in drive_folder:
            return drive_folder
        return {'id': drive_folder['id'], 'name': drive_folder.get('name'),
                'web_link': drive_folder.get('webViewLink'), 'drive_folder': drive_folder}

    def _list_raw(self, folder, deleted=False, extra_q=''):
        q = self.drive.build_q(folder=folder['id'], trashed=deleted)
        return self.drive.file_list(q=q + extra_q)

    def list_files(self, folder, metadata_filter=None, deleted=False, include_metadata=False):
        extra_q = ''
        for key, value in (metadata_filter or {}).items():
            extra_q += f" and appProperties has {{ key='{key}' and value='{value}'}}"
        return [self.normalise(f) for f in self._list_raw(folder, deleted=deleted, extra_q=extra_q)
                if f.get('mimeType') != FOLDER_MIME_TYPE]

    def list_folders(self, folder):
        return [self._folder_handle(f) for f in self._list_raw(folder)
                if f.get('mimeType') == FOLDER_MIME_TYPE]

    def walk(self, folder, path='', include_metadata=False):
        # include_metadata is ignored - listings already carry appProperties
        for f in self._list_raw(folder):
            if f.get('mimeType') == FOLDER_MIME_TYPE:
                sub_path = os.path.join(path, f['name']).replace(os.sep, '/')
                yield from self.walk({'id': f['id']}, sub_path)
            else:
                yield path, self.normalise(f)

    def get_file(self, file_id):
        return self.normalise(self.drive.get_file(file_id=file_id))

    def find_file(self, folder, name):
        files = self.drive.file_list(q=self.drive.build_q(name=name, folder=folder['id']))
        if not files:
            raise StorageFileNotFound(name)
        return self.normalise(files[0])

    def upload(self, folder, name, stream, metadata=None, lock_days=None):
        # lock_days is ignored - Google Drive has no object-lock equivalent
        body = {'appProperties': metadata} if metadata else None
        google_file = self.drive.create_file_stream(name, folder['drive_folder'], stream, body=body)
        return self.get_file(google_file['id'])

    def keep_version(self, stored_file, lock_days=None):
        # a metadata-only rename preserves the existing file (no data transfer); the
        # upload that follows recreates the canonical name
        new_name = self.version_name(stored_file['name'])
        self.drive.service.files().update(fileId=stored_file['id'], body={'name': new_name},
                                          supportsAllDrives=True).execute()
        return new_name

    def verify_upload(self, stored_file, local_path):
        saved_file = self.drive.service.files().get(fileId=stored_file['id'], fields='size, md5Checksum',
                                                    supportsAllDrives=True).execute()
        return (self.md5sum(local_path) == saved_file.get('md5Checksum')
                and os.path.getsize(local_path) == int(saved_file['size']))

    def download(self, stored_file, local_folder=None):
        return self.drive.get_file_contents(file_id=stored_file['id'], file_name=stored_file.get('name'),
                                            local_folder=local_folder)

    def delete(self, file_id):
        self.drive.service.files().update(fileId=file_id, body={'trashed': True},
                                          supportsAllDrives=True).execute()

    def restore_deleted(self, file_id):
        self.drive.service.files().update(fileId=file_id, body={'trashed': False},
                                          supportsAllDrives=True).execute()

    def empty_trash(self):
        self.drive.service.files().emptyTrash().execute()

    def storage_info(self, folder=None):
        about = self.drive.service.about().get(fields='*').execute()
        quota = about.get('storageQuota', {})
        return {'name': folder['name'] if folder else 'Google Drive',
                'web_link': folder.get('web_link') if folder else None,
                'used': int(quota['usage']) if quota.get('usage') else None,
                'limit': int(quota['limit']) if quota.get('limit') else None}

    def destination_status(self, root=None):
        if not root:
            return super().destination_status(root)
        try:
            folder = self.get_folder(root)
        except Exception as e:  # noqa: BLE001 - the panel must always render
            return {'state': 'unknown', 'label': 'Drive folder', 'status': 'Unknown',
                    'detail': f'could not check {root} ({type(e).__name__})'}
        if folder is None:
            return {'state': 'missing', 'label': 'Drive folder', 'status': 'Disabled',
                    'detail': f'{root} is not shared with the service account'}
        return {'state': 'ok', 'label': 'Drive folder', 'status': 'Enabled', 'detail': f'{root} is accessible'}

    def protection_info(self, tier_prefixes=None, expire_days=None, purge_days=None):
        return [{'label': 'Trash (soft delete)', 'status': 'Enabled',
                 'detail': 'deleted backups stay in trash for 30 days unless the trash is emptied'}]
