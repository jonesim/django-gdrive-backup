import os
import logging
from .base_backup import BaseBackup

logger = logging.getLogger(__name__)


class BackupLocal(BaseBackup):

    def backup_to_drive(self, source_dir, google_drive_dir):
        logger.info(f'Backing up {source_dir} to {google_drive_dir}')
        gdrive_backup_dir = self.drive.find_create_folder(google_drive_dir, folder=self.base_backup_dir)
        file_hashes = self.get_file_hashes(gdrive_backup_dir)
        for f in os.listdir(source_dir):
            full_filename = os.path.join(source_dir, f)
            if os.path.isfile(full_filename):
                if self.md5sum(full_filename) not in file_hashes.get(full_filename, []):
                    logger.info('Backup - ' + f)
                    with open(full_filename, 'rb') as backup_stream:
                        self.drive.create_file_stream(full_filename, gdrive_backup_dir, backup_stream)
                else:
                    logger.info('    Exists - ' + f)
            elif os.path.isdir(full_filename):
                self.backup_to_drive(full_filename, google_drive_dir + '/' + f)

    def restore_gdrive_folder(self, g_drive_folder_name, destination_root):
        folder_id = self.drive.get_folder(g_drive_folder_name, folder=self.base_backup_dir)
        files = self.drive.file_list(q=self.drive.build_q(folder=folder_id))
        folder = f'{destination_root}/{g_drive_folder_name}'
        if not os.path.exists(folder):
            os.mkdir(folder)
        for f in files:
            if f.get('mimeType') == 'application/vnd.google-apps.folder':
                self.restore_gdrive_folder(f'{g_drive_folder_name}/{f["name"]}', destination_root)
            else:
                self.drive.get_file_contents(file_id=f['id'], local_folder=folder)
