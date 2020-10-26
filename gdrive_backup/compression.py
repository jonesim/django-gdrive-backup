import os
import bz2
import subprocess
from shutil import copyfileobj


def decompress(filename):
    extension = filename[filename.rfind('.') + 1:].lower()
    decompressed_name = filename[:- 1 * (len(extension) + 1)]

    if extension == 'bz2':
        with bz2.BZ2File(filename, 'rb') as input_file:
            with open(decompressed_name, 'wb') as output:
                copyfileobj(input_file, output)
        os.remove(filename)

    elif extension == 'gz':
        subprocess.call(['gunzip', filename])

    else:
        raise ValueError('Unknown file type: ' + filename)

    return decompressed_name


def compress(filename, compression_type):
    if compression_type == 'bz2':
        with open(filename, 'rb') as input_file:
            with bz2.BZ2File(filename + '.' + compression_type, 'wb', compresslevel=9) as output:
                copyfileobj(input_file, output, 1024*1024)
        os.remove(filename)

    elif compression_type == 'gz':
        subprocess.call(['gzip', filename])

    return filename + '.' + compression_type
