import os
import datetime
import logging
import subprocess
import urllib.parse
from .base_backup import BaseBackup
from .prune_backups import PruneBackups
from .compression import decompress, compress

logger = logging.getLogger(__name__)
compression = 'bz2'


class DatabaseUploadError(Exception):
    pass


class BackupDb(BaseBackup):

    def __init__(self, google_credentials, google_backup_dir, database, local_backup_dir, schema=None):
        super().__init__(google_credentials, google_backup_dir)
        self.postgres_backup = PostgresBackup(database, schema)
        self.local_backup_dir = local_backup_dir

    def backup_db_gdrive(self):
        logger.info('Backing up database')
        if self.postgres_backup.schema:
            logger.info(' schema ' + self.postgres_backup.schema)
        backup_filename = self.postgres_backup.backup_db(self.local_backup_dir)
        local_path = os.path.join(self.local_backup_dir, backup_filename)
        logger.info('Copying backup to Google Drive')
        with open(local_path, 'rb') as backup_stream:
            google_file = self.drive.create_file_stream(backup_filename, self.base_backup_dir, backup_stream)
        if self.check_upload(google_file, local_path):
            logger.info('Deleting temporary backup file')
            os.remove(local_path)
        else:
            raise DatabaseUploadError

    def restore_gdrive_db(self, file_id=None, file_name=None):
        file_name = self.drive.get_file_contents(file_id=file_id, file_name=file_name, folder=self.base_backup_dir,
                                                 local_folder=self.local_backup_dir)
        self.postgres_backup.restore_db(os.path.join(self.local_backup_dir, file_name))

    def get_db_backup_files(self, trashed=False):
        return self.drive.file_list(q=self.drive.build_q(trashed=trashed, folder=self.base_backup_dir)
                                    + " and mimeType contains 'application/x-'", orderBy='createdTime desc')

    def get_latest_db_backup(self):
        files = self.get_db_backup_files()
        if len(files) > 0:
            return files[0]

    def prune_old_backups(self, recipe):
        backups = self.get_db_backup_files()
        backup_dict = {b['createdTime']: b for b in backups}
        pb = PruneBackups(backup_dict)
        removal = pb.backups_to_remove(recipe)
        for k in removal:
            self.drive.service.files().update(fileId=removal[k]['id'], body={'trashed': True}).execute()


class PostgresBackup:

    def __init__(self, database, schema=None):
        self.schema = schema
        self.connection_string = (f'postgresql://{database["USER"]}:{urllib.parse.quote(database["PASSWORD"])}'
                                  f'@{database["HOST"]}/{database["NAME"]}')

    def psql(self, commands):
        subprocess.call(['psql', '-d',  self.connection_string] + commands)

    def restore_db(self, backup_file):
        decompressed_name = decompress(backup_file)
        self.psql(['-f', decompressed_name])
        os.remove(decompressed_name)

    def backup_db(self, backup_local_db_dir):
        if not os.path.exists(backup_local_db_dir):
            os.makedirs(backup_local_db_dir)
        filename = 'db_' + datetime.datetime.today().strftime('%Y_%m_%d_%H_%M')
        logger.info('Creating backup file ' + filename)
        backup_path = backup_local_db_dir + '/' + filename
        with open(backup_path, 'wb') as db_backup:
            commands = ['pg_dump', '-d', self.connection_string, '-c']
            if self.schema:
                commands += ['-n', self.schema]
            dump_process = subprocess.Popen(commands, stdout=db_backup)
            dump_process.wait()
        compress(backup_path, compression)
        return filename + '.' + compression
