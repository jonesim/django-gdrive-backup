import os
from .base_backup import BaseBackup


class BackupLocal(BaseBackup):

    def backup_folder(self, source_dir, backup_dir):
        self.logger.info(f'Backing up {source_dir} to {backup_dir}')
        storage_dir = self.storage.ensure_folder(backup_dir, parent=self.base_backup_dir)
        file_hashes = self.get_file_hashes(storage_dir)
        for f in os.listdir(source_dir):
            full_filename = os.path.join(source_dir, f)
            if os.path.isfile(full_filename):
                md5 = self.md5sum(full_filename)
                # older Google Drive backups were stored under the full local path,
                # so check that name as well to avoid re-uploading them all
                if md5 not in file_hashes.get(f, []) + file_hashes.get(full_filename, []):
                    self.logger.info('Backup - ' + f)
                    with open(full_filename, 'rb') as backup_stream:
                        self.storage.upload(storage_dir, f, backup_stream, metadata={'md5': md5})
                else:
                    self.logger.info('    Exists - ' + f)
            elif os.path.isdir(full_filename):
                self.backup_folder(full_filename, backup_dir + '/' + f)

    # previous name, kept for compatibility
    backup_to_drive = backup_folder

    def restore_folder(self, folder_name, destination_root):
        folder = self.storage.get_folder(folder_name, parent=self.base_backup_dir)
        for path, stored_file in self.storage.walk(folder):
            local_folder = os.path.join(destination_root, folder_name, path)
            os.makedirs(local_folder, exist_ok=True)
            self.storage.download(stored_file, local_folder=local_folder)

    # previous name, kept for compatibility
    restore_gdrive_folder = restore_folder
