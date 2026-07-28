import datetime
import os
import subprocess
import urllib.parse
from tempfile import NamedTemporaryFile, TemporaryFile

import requests

from .base_backup import BaseBackup
from .compression import decompress
from .prune_backups import PruneBackups
from .sql_functions import delete_table

DUMP_EXTENSION = 'dump'


def get_ip_address():
    try:
        ip_response = requests.get('https://api.ipify.org/')
        if ip_response.status_code == 200 and len(ip_response.text) < 16:
            ip_address = ip_response.text.replace('.', '_')
        else:
            ip_address = 'fail'
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.HTTPError):
        ip_address = 'exception'
    return ip_address


class DatabaseUploadError(Exception):
    pass


class DatabaseBackupError(Exception):
    pass


class BackupDb(BaseBackup):

    def __init__(self, google_credentials, google_backup_dir, database, local_backup_dir, logger, schema=None,
                 table=None, exclude_tables=None, exclude_table_data=None):
        super().__init__(google_credentials, google_backup_dir, logger)
        self.postgres_backup = PostgresBackup(database, self.logger, schema, table,
                                              exclude_tables=exclude_tables,
                                              exclude_table_data=exclude_table_data)
        self.local_backup_dir = local_backup_dir

    def backup_db_gdrive(self):
        app_properties = {'ip_address': get_ip_address()}
        if self.postgres_backup.table:
            app_properties['schema'] = self.postgres_backup.schema
            app_properties['table'] = self.postgres_backup.table
            filename = f'table_{self.postgres_backup.table}'
        elif self.postgres_backup.schema:
            app_properties['schema'] = self.postgres_backup.schema
            filename = f'schema_{self.postgres_backup.schema}'
        else:
            filename = 'db'
        filename += f'_{datetime.datetime.today().strftime("%Y_%m_%d_%H_%M")}.{DUMP_EXTENSION}'
        # only the unique name is wanted - backup_db writes to <name>.dump, so the placeholder
        # file itself is closed and removed rather than left open for the life of the process
        backup_stream = NamedTemporaryFile(delete=False)
        backup_stream.close()
        os.remove(backup_stream.name)
        backup_filename = None
        try:
            backup_filename = self.postgres_backup.backup_db('', backup_stream.name)
            self.logger.info('Copying backup to Google Drive')
            with open(backup_filename, 'rb') as compressed_file:
                google_file = self.drive.create_file_stream(filename, self.base_backup_dir, compressed_file,
                                                            body={'appProperties': app_properties})
            if not self.check_upload(google_file, backup_filename):
                raise DatabaseUploadError
        finally:
            if backup_filename and os.path.exists(backup_filename):
                os.remove(backup_filename)

    def restore_gdrive_db(self, file_id=None, file_name=None):
        file_info = self.drive.get_file(file_id=file_id)
        file_name = self.drive.get_file_contents(file_id=file_id, file_name=file_name, folder=self.base_backup_dir,
                                                 local_folder=self.local_backup_dir)
        if file_info.get('appProperties', {}).get('table'):
            delete_table(file_info["appProperties"]["schema"], file_info["appProperties"]["table"])
        self.postgres_backup.restore_db(os.path.join(self.local_backup_dir, file_name))

    def get_db_backup_files(self, trashed=False, extra_q=''):
        return self.drive.file_list(q=f"{self.drive.build_q(trashed=trashed, folder=self.base_backup_dir)}"
                                    f" and (mimeType contains 'application/x-' or name contains '.dump'){extra_q}",
                                    orderBy='createdTime desc')

    def get_latest_db_backup(self):
        files = self.get_db_backup_files()
        if len(files) > 0:
            return files[0]

    def prune_old_backups(self, recipe):
        backups = self.get_db_backup_files(extra_q=(f" and appProperties has "
                                                    f"{{ key='ip_address' and value='{get_ip_address()}'}}"))
        backup_dict = {b['createdTime']: b for b in backups}
        pb = PruneBackups(backup_dict)
        removal = pb.backups_to_remove(recipe)
        for k in removal:
            self.drive.service.files().update(fileId=removal[k]['id'], body={'trashed': True},
                                              supportsAllDrives=True).execute()


class PostgresBackup:

    def __init__(self, database, logger, schema=None, table=None, exclude_tables=None, exclude_table_data=None):
        self.logger = logger
        self.schema = schema
        self.table = table
        self.exclude_tables = exclude_tables or []
        self.exclude_table_data = exclude_table_data or []
        self.connection_string = (f'postgresql://{database["USER"]}:{urllib.parse.quote(database["PASSWORD"])}'
                                  f'@{database["HOST"]}/{database["NAME"]}')

    def psql(self, commands):
        return subprocess.call(['psql', '-d',  self.connection_string] + commands)

    def restore_db(self, backup_file):
        if backup_file.endswith('.' + DUMP_EXTENSION):
            return_code = subprocess.call(['pg_restore', '-d', self.connection_string, '--clean', '--if-exists',
                                           backup_file])
            command = 'pg_restore'
            os.remove(backup_file)
        else:
            decompressed_name = decompress(backup_file)
            return_code = self.psql(['-f', decompressed_name])
            command = 'psql'
            os.remove(decompressed_name)
        if return_code != 0:
            # not fatal - pg_restore exits non-zero for ignorable warnings under --clean --if-exists,
            # but the restore may equally have failed outright, so make it visible
            self.logger.warning(f'{command} exited with code {return_code}. Check the output above to confirm the '
                                f'restore completed as expected.')
        return return_code

    def backup_db(self, backup_local_db_dir, filename):
        self.logger.info('Creating backup file ' + filename)
        if backup_local_db_dir:
            if not os.path.exists(backup_local_db_dir):
                os.makedirs(backup_local_db_dir)
            backup_path = backup_local_db_dir + '/' + filename
        else:
            backup_path = filename
        commands = ['pg_dump', '-Fc', '-d', self.connection_string]
        if self.table:
            self.logger.info(f'Backing up table {self.schema}.{self.table}')
            commands += ['-a', '-t', f'{self.schema}.{self.table}']
        elif self.schema:
            self.logger.info(f'Backing up schema {self.schema}')
            commands += ['-n', self.schema]
        else:
            self.logger.info(f'Backing up database')
        for t in self.exclude_tables:
            commands += [f'--exclude-table={t}']
        for t in self.exclude_table_data:
            commands += [f'--exclude-table-data={t}']
        dump_path = backup_path + '.' + DUMP_EXTENSION
        try:
            # stderr goes to a temp file rather than a pipe so a noisy dump cannot fill the pipe
            # buffer and deadlock while we are still reading stdout
            with TemporaryFile() as error_file:
                with open(dump_path, 'wb') as output:
                    dump_process = subprocess.Popen(commands, stdout=subprocess.PIPE, stderr=error_file)
                    for chunk in iter(lambda: dump_process.stdout.read(1024 * 1024), b''):
                        output.write(chunk)
                    dump_process.wait()
                error_file.seek(0)
                errors = error_file.read().decode(errors='replace').strip()
            if dump_process.returncode != 0:
                if errors:
                    self.logger.error(errors)
                raise DatabaseBackupError(f'pg_dump failed with exit code {dump_process.returncode}')
            if errors:
                self.logger.warning(errors)
        except BaseException:
            # never leave a partial dump behind for the caller to upload as if it were valid
            if os.path.exists(dump_path):
                os.remove(dump_path)
            raise
        return dump_path
