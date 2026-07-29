import os

from .base_backup import BaseBackup, CHANGED_HISTORY, CHANGED_PROTECT
from .encryption import EncryptingReader, decrypt_in_place


class BackupLocal(BaseBackup):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.changed_files = []

    def backup_folder(self, source_dir, backup_dir):
        self.logger.info(f'Backing up {source_dir} to {backup_dir}')
        mode = self.config.changed_files
        encrypt = self.encryption_key is not None
        lock_days = self.storage.lock_days('file')
        storage_dir = self.storage.ensure_folder(backup_dir, parent=self.base_backup_dir)
        # encrypted stored bytes hash differently from the source, so dedup then needs
        # the plaintext md5 recorded in each file's metadata at upload
        files_by_name = self.get_files_by_name(storage_dir, include_metadata=encrypt)
        for f in os.listdir(source_dir):
            full_filename = os.path.join(source_dir, f)
            if os.path.isfile(full_filename):
                md5 = self.md5sum(full_filename)
                # older Google Drive backups were stored under the full local path,
                # so check that name as well to avoid re-uploading them all
                existing = files_by_name.get(f, []) + files_by_name.get(full_filename, [])
                if md5 in [self.content_hash(e) for e in existing]:
                    self.logger.info('    Exists - ' + f)
                    continue
                if existing:
                    if mode == CHANGED_PROTECT:
                        self.changed_files.append(full_filename)
                        self.logger.warning(f'NOT backing up {full_filename} - it no longer matches '
                                            f'its existing backup')
                        continue
                    if mode == CHANGED_HISTORY:
                        self.changed_files.append(full_filename)
                        self.logger.warning(f'{full_filename} changed - keeping the previous backup version')
                        self.storage.keep_version(existing[0], lock_days=lock_days)
                self.logger.info('Backup - ' + f)
                # the metadata md5 is always the plaintext hash - it is what dedup compares
                with open(full_filename, 'rb') as backup_stream:
                    if encrypt:
                        stream = EncryptingReader(backup_stream, os.path.getsize(full_filename),
                                                  self.encryption_key, logger=self.logger)
                        metadata = {'md5': md5, 'encrypted': '1'}
                    else:
                        stream = backup_stream
                        metadata = {'md5': md5}
                    self.storage.upload(storage_dir, f, stream, metadata=metadata,
                                        lock_days=lock_days)
            elif os.path.isdir(full_filename):
                self.backup_folder(full_filename, backup_dir + '/' + f)

    # previous name, kept for compatibility
    backup_to_drive = backup_folder

    def restore_folder(self, folder_name, destination_root):
        folder = self.storage.get_folder(folder_name, parent=self.base_backup_dir)
        for path, stored_file in self.storage.walk(folder):
            local_folder = os.path.join(destination_root, folder_name, path)
            os.makedirs(local_folder, exist_ok=True)
            local_name = self.storage.download(stored_file, local_folder=local_folder)
            decrypt_in_place(os.path.join(local_folder, local_name), self.encryption_key)

    # previous name, kept for compatibility
    restore_gdrive_folder = restore_folder
