import io
import boto3
import hashlib
from .base_backup import BaseBackup


class S3File(io.RawIOBase):
    """
    Binary stream to access S3 files. Provides seek and read a variable number of bytes.
    """

    def __init__(self, s3_object):
        self.s3_object = s3_object
        self.position = 0
        self.md5 = hashlib.md5(b'')

    def __repr__(self):
        return "<%s s3_object=%r>" % (type(self).__name__, self.s3_object)

    @property
    def size(self):
        return self.s3_object.content_length

    def tell(self):
        return self.position

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_SET:
            self.position = offset
        elif whence == io.SEEK_CUR:
            self.position += offset
        elif whence == io.SEEK_END:
            self.position = self.size + offset
        else:
            raise ValueError("invalid whence (%r, should be %d, %d, %d)" % (
                whence, io.SEEK_SET, io.SEEK_CUR, io.SEEK_END
            ))
        return self.position

    def seekable(self):
        return True

    def read(self, size=-1):
        if self.position >= self.size:
            return b''
        if size == -1:
            range_header = f'bytes={self.position}'
            self.seek(offset=0, whence=io.SEEK_END)
        else:
            new_position = self.position + size
            if new_position >= self.size:
                return self.read()
            range_header = f'bytes={self.position}-{new_position - 1}'
            self.seek(offset=size, whence=io.SEEK_CUR)
        data = self.s3_object.get(Range=range_header)["Body"].read()
        self.md5.update(data)
        return data

    def readable(self):
        return True


class BackupFolders:
    """
    Keeps a dictionary of destination folder handles along with file S3 ETag hashes
    """

    def __init__(self, backup, base_folder):
        self.base_folder = base_folder
        self.backup = backup
        self.folders = \
            {'/': {'hashes': backup.get_file_hashes(base_folder),
                   'folder': backup.storage.ensure_folder(base_folder, parent=backup.base_backup_dir)}
             }

    def add_folder(self, folder):
        return {
            'hashes': self.backup.get_file_hashes(self.base_folder + '/' + folder),
            'folder': self.backup.storage.ensure_folder(folder, parent=self.folders['/']['folder']),
        }

    def file_exists(self, folder, file, file_hash):
        if file == '':
            return True
        if folder not in self.folders:
            self.folders[folder] = self.add_folder(folder)
        return file_hash in self.folders[folder]['hashes'].get(file, [])

    def parent(self, folder):
        return self.folders[folder]['folder']


class BackupS3(BaseBackup):
    """
    Copies files from a folder and sub folders in an S3 bucket to the backup storage.
    Will skip files where the S3 ETag matches the value stored in the file's metadata.
    """

    def __init__(self, access_key_id, access_key, storage, backup_dir, logger):
        super().__init__(storage, backup_dir, logger)
        self.s3 = boto3.resource('s3',  aws_access_key_id=access_key_id,  aws_secret_access_key=access_key)

    @staticmethod
    def get_etag(f):
        metadata = f.get('metadata', {})
        # S3-compatible destinations return metadata keys lower-cased
        return metadata.get('ETag', metadata.get('etag'))

    def get_file_hashes(self, directory, get_hash=None, include_metadata=True):
        return super().get_file_hashes(directory, get_hash or self.get_etag, include_metadata=True)

    def backup(self, bucket_name, prefix, destination):
        """ Backup from a S3 prefix (folder) to the backup storage
        :param bucket_name:
        :param prefix:  consider like a folder with no trailing /
        :param destination:
        :return:
        """

        folders = BackupFolders(self, destination)
        bucket = self.s3.Bucket(name=bucket_name)
        for f in bucket.objects.filter(Prefix=prefix):
            filename = f.key.split('/')[-1]
            path = f.key[len(prefix) + 1:-1*(len(filename) + 1)]
            if path == '':
                path = '/'
            if not folders.file_exists(path, filename, f.e_tag):
                self.logger.info(f'Backing up {f.key}')
                s3_file = S3File(self.s3.Object(bucket_name, f.key))
                self.storage.upload(folders.parent(path), filename, s3_file,
                                    metadata={'ETag': f.e_tag})
            else:
                self.logger.info(f'found {f.key}')
