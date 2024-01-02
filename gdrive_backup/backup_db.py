import datetime
import json
import os
import subprocess
import urllib.parse
from tempfile import NamedTemporaryFile

import requests


from .base_backup import BaseBackup
from .compression import decompress, compress
from .prune_backups import PruneBackups

compression = 'bz2'


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


class BackupDb(BaseBackup):

    def __init__(self, base_backup_dir, database, local_backup_dir, logger, schema=None,
                 table=None):
        super().__init__(base_backup_dir, logger)
        self.postgres_backup = PostgresBackup(database, self.logger, schema, table)
        self.local_backup_dir = local_backup_dir

    def backup_db_and_upload(self):
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
        filename += f'_{datetime.datetime.today().strftime("%Y_%m_%d_%H_%M")}'
        app_filename = f'{filename}.json'
        filename += f'.{compression}'

        backup_app_stream = NamedTemporaryFile(delete=False)
        backup_app_filename = backup_app_stream.name
        with open(backup_app_filename, 'w') as json_file:
            json.dump(app_properties, json_file)

        backup_stream = NamedTemporaryFile(delete=False)
        backup_filename = self.postgres_backup.backup_db('', backup_stream.name)

        upload_filename = os.path.join(self.base_backup_dir, filename)
        self.logger.info('uploading backup')
        self.upload_file(local_file_path=backup_filename,
                         upload_filename=upload_filename)

        upload_app_filename = os.path.join(self.base_backup_dir, app_filename)
        self.upload_file(local_file_path=backup_app_filename,
                         upload_filename=upload_app_filename)

        if not self.check_upload(storage_file_id=upload_filename, local_file_path=backup_filename):
            raise DatabaseUploadError
        os.remove(backup_filename)
        os.remove(backup_app_filename)

    def restore_cloud_storage_db(self, file_id=None, file_name=None):
        assert False
        # self.download_file(file_id, )
        # file_name = self.get_file_contents(file_path=file_name, local_folder=self.base_backup_dir)
        # if file_info.get('appProperties', {}).get('table'):
        #     delete_table(file_info["appProperties"]["schema"], file_info["appProperties"]["table"])
        # self.postgres_backup.restore_db(os.path.join(self.local_backup_dir, file_name))

    def get_db_backup_files(self):
        storages = self.get_storages()
        _, file_names = storages.listdir(self.base_backup_dir)

        # List of dictionaries with file name and creation time
        files_with_details = []

        for file_name in file_names:
            if file_name.endswith('.json'):
                continue

            file_path = os.path.join(self.base_backup_dir, file_name)

            raw_file_path = file_path.split('.')[0]
            try:
                with storages.open(f'{raw_file_path}.json', 'r') as json_file:
                    app_data = json.load(json_file)
            except FileNotFoundError:
                app_data = {}
            try:
                # Getting creation time

                creation_time = storages.get_modified_time(file_path)

                # Getting file size
                file_size = storages.size(file_path)
                # Convert creation time to a readable format if necessary, e.g., time.ctime(creation_time)
                files_with_details.append({
                    'name': file_name,
                    'created_time': creation_time,
                    'size': file_size,  # Size in bytes
                    'ip_address': app_data.get('ip_address'),
                    'table': app_data.get('table')

                })
            except OSError:
                # Handle error if file is not accessible
                pass

        return files_with_details

    def get_latest_db_backup(self):
        files = self.get_db_backup_files()
        if len(files) > 0:
            return files[0]

    def prune_old_backups(self, recipe):
        backup_dict = self.get_db_backup_files()
        backup_dict = {b['created_time']: b['name'] for b in backup_dict}
        pb = PruneBackups(backup_dict)
        removal = pb.backups_to_remove(recipe)

        for file in removal.values():
            delete_file = os.path.join(self.base_backup_dir, file)
            self.trash_file(delete_file)


class PostgresBackup:

    def __init__(self, database, logger, schema=None, table=None):
        self.logger = logger
        self.schema = schema
        self.table = table
        self.connection_string = (f'postgresql://{database["USER"]}:{urllib.parse.quote(database["PASSWORD"])}'
                                  f'@{database["HOST"]}/{database["NAME"]}')

    def psql(self, commands):
        subprocess.call(['psql', '-d',  self.connection_string] + commands)

    def restore_db(self, backup_file):
        decompressed_name = decompress(backup_file)
        self.psql(['-f', decompressed_name])
        os.remove(decompressed_name)

    def backup_db(self, backup_local_db_dir, filename):
        self.logger.info('Creating backup file ' + filename)
        if backup_local_db_dir:
            if not os.path.exists(backup_local_db_dir):
                os.makedirs(backup_local_db_dir)
            backup_path = backup_local_db_dir + '/' + filename
        else:
            backup_path = filename
        with open(backup_path, 'wb') as db_backup:
            commands = ['pg_dump', '-d', self.connection_string]
            if self.table:
                self.logger.info(f'Backing up table {self.schema}.{self.table}')
                commands += ['-a', '-t', f'{self.schema}.{self.table}']
            elif self.schema:
                self.logger.info(f'Backing up schema {self.schema}')
                commands += ['-c', '-n', self.schema]
            else:
                self.logger.info(f'Backing up database')
                commands += ['-c']
            dump_process = subprocess.Popen(commands, stdout=db_backup)
            dump_process.wait()
        compress(backup_path, compression)
        return backup_path + '.' + compression
