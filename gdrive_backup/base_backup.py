import os
import hashlib
from google_client.drive import GoogleDrive


class BaseBackup:

    def __init__(self, google_credentials, base_backup_dir, logger):
        self.drive = GoogleDrive(google_credentials)
        self.logger = logger
        self.base_backup_dir = self.drive.find_create_folder(base_backup_dir, shared_with_me=True)

    def get_existing_backup_files(self, google_drive_dir, extra_query=None):
        if not extra_query:
            extra_query = {}
        if type(google_drive_dir) != dict:
            gdrive_backup_dir = self.drive.find_create_folder(google_drive_dir, folder=self.base_backup_dir)
        else:
            gdrive_backup_dir = google_drive_dir
        return self.drive.file_list(q=self.drive.build_q(folder=gdrive_backup_dir['id'], **extra_query))

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

    def check_upload(self, google_file, local_file):
        saved_file = self.drive.service.files().get(fileId=google_file['id'], fields='size, md5Checksum').execute()
        md5 = self.md5sum(local_file)
        file_length = os.path.getsize(local_file)
        if md5 == saved_file['md5Checksum'] and file_length == int(saved_file['size']):
            return True
        return False
