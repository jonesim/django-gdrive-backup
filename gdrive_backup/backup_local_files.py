import os
from .base_backup import BaseBackup


class BackupLocal(BaseBackup):

    def backup_to_storages(self, source_dir, backup_dir_prefix):
        self.logger.info(f'Backing up {source_dir} to {backup_dir_prefix}')
        file_hashes = self.get_file_hashes(backup_dir_prefix)

        for f in os.listdir(source_dir):
            full_filename = os.path.join(source_dir, f)
            if os.path.isfile(full_filename):
                # Construct the storage file name
                storage_file_name = os.path.join(backup_dir_prefix, f)

                if self.md5sum(full_filename) not in file_hashes.get(storage_file_name, []):
                    self.logger.info('Backup - ' + f)
                    self.upload_file(full_filename, storage_file_name)
                else:
                    self.logger.info('    Exists - ' + f)
            elif os.path.isdir(full_filename):
                # Recursive call for directories
                self.backup_to_storages(full_filename, os.path.join(backup_dir_prefix, f))

    def restore_from_storage(self, storage_dir_prefix, destination_root):
        # Check and create the destination directory if it doesn't exist
        if not os.path.exists(destination_root):
            os.makedirs(destination_root)

        storages = self.get_storages()

        # Get the list of files in the specified storage directory
        # Note: This assumes a flat structure where all files are directly under the given prefix
        storage_files = storages.listdir(storage_dir_prefix)[1]

        for file_name in storage_files:
            storage_file_path = os.path.join(storage_dir_prefix, file_name)
            local_file_path = os.path.join(destination_root, file_name)

            if storages.isdir(storage_file_path):
                # Recursive call for directories (if the storage backend supports directories)
                self.restore_from_storage(storage_file_path, local_file_path)
            else:
                # Download and save the file to the local destination
                self.download_file(storage_file_path, local_file_path)
