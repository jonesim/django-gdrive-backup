"""Optional client-side encryption of backup content, enabled per backup config
(the 'encryption' key in a BACKUP_CONFIGS entry, or the legacy global
BACKUP_ENCRYPTION setting): True reuses the encrypted-credentials SETTINGS_KEY,
or a dedicated urlsafe-base64 32-byte key (the same format as a Fernet key, so
encrypted_credentials.encrypted_file.random_key() generates one). This module is
settings-agnostic - callers resolve their config's key with resolve_key() and
pass the raw key bytes in.

Format v1 - chunked AES-256-GCM (the STREAM construction, as used by age/Tink):

    Header (28 bytes, also the associated data for every chunk):
      0-5   magic       b'DCBENC'
      6     version     0x01
      7     flags       0x00 (reserved)
      8-11  chunk_size  uint32 big-endian (writers here use 1 MiB)
      12-27 salt        16 random bytes per file

    file_key = HKDF-SHA256(master_key, salt=salt, info=b'django-cloud-backup v1')

    Body - chunks of chunk_size plaintext (the last carries the remainder):
      nonce (12 bytes) = 11-byte big-endian chunk counter || final flag byte
      chunk = AESGCM(file_key).encrypt(nonce, plaintext, associated_data=header)

The per-file random salt makes nonce reuse across files impossible, the final
flag byte defeats truncation at a chunk boundary, and a wrong key fails cleanly
on the first chunk. Given a fixed salt the ciphertext is deterministic, which
is what lets EncryptingReader support backward seeks by re-encrypting.
"""
import base64
import binascii
import hashlib
import io
import os
import struct

from django.core.exceptions import ImproperlyConfigured

from encrypted_credentials.django_credentials import env_key_name

MAGIC = b'DCBENC'
VERSION = 1
FLAGS = 0
CHUNK_SIZE = 1024 * 1024
SALT_LEN = 16
HEADER_LEN = 28
TAG_LEN = 16
KEY_INFO = b'django-cloud-backup v1'

INSTALL_HINT = 'pip install django-cloud-backup[encryption]'


class DecryptionError(Exception):
    pass


def resolve_key(encryption_setting):
    """Turn a config's encryption value into raw key bytes: falsy -> None
    (encryption off), True -> the encrypted-credentials SETTINGS_KEY from the
    environment, a string -> a dedicated urlsafe-base64 32-byte key."""
    if not encryption_setting:
        return None
    if encryption_setting is True:
        key_base64 = os.environ.get(env_key_name)
        if not key_base64:
            raise ImproperlyConfigured(f'Backup encryption is True but {env_key_name} is not in the environment')
    else:
        key_base64 = encryption_setting
    try:
        key = base64.urlsafe_b64decode(key_base64)
    except (binascii.Error, ValueError) as e:
        raise ImproperlyConfigured('Backup encryption key is not valid urlsafe base64') from e
    if len(key) != 32:
        raise ImproperlyConfigured('Backup encryption key must decode to 32 bytes')
    return key


def _new_aesgcm(key, salt):
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    except ImportError as e:
        raise ImproperlyConfigured(f'Backup encryption requires the cryptography package - {INSTALL_HINT}') from e
    file_key = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=KEY_INFO).derive(key)
    return AESGCM(file_key)


def _build_header(salt):
    return MAGIC + bytes((VERSION, FLAGS)) + struct.pack('>I', CHUNK_SIZE) + salt


def _nonce(counter, final):
    return counter.to_bytes(11, 'big') + (b'\x01' if final else b'\x00')


def _chunk_count(plaintext_size):
    # an empty file still gets one (empty) authenticated chunk
    return max(1, -(-plaintext_size // CHUNK_SIZE))


def encrypted_size(plaintext_size):
    return HEADER_LEN + plaintext_size + TAG_LEN * _chunk_count(plaintext_size)


def is_encrypted(filename):
    with open(filename, 'rb') as f:
        return f.read(len(MAGIC)) == MAGIC


def encrypt_file(source_filename, dest_filename, key):
    """Encrypt source_filename to dest_filename, returning the ciphertext md5
    hexdigest (what the storage backends will see and verify against)."""
    salt = os.urandom(SALT_LEN)
    header = _build_header(salt)
    aesgcm = _new_aesgcm(key, salt)
    md5 = hashlib.md5(header)
    with open(source_filename, 'rb') as source, open(dest_filename, 'wb') as dest:
        dest.write(header)
        counter = 0
        chunk = source.read(CHUNK_SIZE)
        while True:
            next_chunk = source.read(CHUNK_SIZE)
            final = not next_chunk
            encrypted = aesgcm.encrypt(_nonce(counter, final), chunk, header)
            md5.update(encrypted)
            dest.write(encrypted)
            if final:
                break
            counter += 1
            chunk = next_chunk
    return md5.hexdigest()


def decrypt_in_place(filename, key):
    """Decrypt filename over itself if it is an encrypted backup, so restore
    code needs no knowledge of whether encryption was on when the backup was
    made. Returns False (leaving the file untouched) for plaintext backups."""
    tmp_name = filename + '.decrypt-tmp'
    with open(filename, 'rb') as source:
        header = source.read(HEADER_LEN)
        if not header.startswith(MAGIC):
            return False
        if key is None:
            raise ImproperlyConfigured(f'{filename} is an encrypted backup but this backup config '
                                       f'has no encryption key')
        if len(header) < HEADER_LEN or header[len(MAGIC)] != VERSION:
            raise DecryptionError(f'Unsupported encrypted backup header in {filename}')
        chunk_size = struct.unpack('>I', header[8:12])[0]
        if chunk_size == 0:
            raise DecryptionError(f'Unsupported encrypted backup header in {filename}')
        aesgcm = _new_aesgcm(key, header[12:])
        from cryptography.exceptions import InvalidTag
        frame_size = chunk_size + TAG_LEN
        try:
            with open(tmp_name, 'wb') as dest:
                counter = 0
                frame = source.read(frame_size)
                while True:
                    next_frame = source.read(frame_size)
                    final = not next_frame
                    try:
                        dest.write(aesgcm.decrypt(_nonce(counter, final), frame, header))
                    except InvalidTag as e:
                        raise DecryptionError(f'Could not decrypt {filename} - wrong encryption key '
                                              f'or corrupted/truncated backup file') from e
                    if final:
                        break
                    counter += 1
                    frame = next_frame
        except BaseException:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
            raise
    os.replace(tmp_name, filename)
    return True


class EncryptingReader(io.RawIOBase):
    """Read-only file-like wrapper that encrypts a seekable source stream on
    the fly, so file backups upload without a ciphertext temp file. Seekable:
    length probes (seek to end) are answered arithmetically, and backward
    seeks rewind the source and re-encrypt forward - the salt is fixed at
    construction so the ciphertext is reproducible."""

    def __init__(self, stream, plaintext_size, key, logger=None):
        self.stream = stream
        self.plaintext_size = plaintext_size
        self.chunks = _chunk_count(plaintext_size)
        self.total_size = encrypted_size(plaintext_size)
        self.salt = os.urandom(SALT_LEN)
        self.header = _build_header(self.salt)
        self.aesgcm = _new_aesgcm(key, self.salt)
        self.logger = logger
        self.restarts = 0
        self._restart()

    def _restart(self):
        self.stream.seek(0)
        self.buffer = self.header
        self.chunk_index = 0
        self.position = 0

    def _read_source(self, size):
        data = b''
        while len(data) < size:
            block = self.stream.read(size - len(data))
            if not block:
                raise IOError('Source file shrank while being encrypted for backup')
            data += block
        return data

    def _encrypt_next_chunk(self):
        final = self.chunk_index == self.chunks - 1
        size = self.plaintext_size - CHUNK_SIZE * (self.chunks - 1) if final else CHUNK_SIZE
        encrypted = self.aesgcm.encrypt(_nonce(self.chunk_index, final), self._read_source(size), self.header)
        self.chunk_index += 1
        return encrypted

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def read(self, size=-1):
        if size is None or size < 0:
            size = self.total_size - self.position
        while len(self.buffer) < size and self.chunk_index < self.chunks:
            self.buffer += self._encrypt_next_chunk()
        data = self.buffer[:size]
        self.buffer = self.buffer[len(data):]
        self.position += len(data)
        return data

    def readinto(self, b):
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_SET:
            target = offset
        elif whence == io.SEEK_CUR:
            target = self.position + offset
        elif whence == io.SEEK_END:
            target = self.total_size + offset
        else:
            raise ValueError(f'invalid whence ({whence})')
        if target == self.total_size:
            # a length probe - jump to the end without encrypting anything
            self.buffer = b''
            self.chunk_index = self.chunks
            self.position = target
            return target
        if target < self.position:
            self._restart()
            self.restarts += 1
            if self.logger and self.restarts == 3:
                self.logger.warning('Backup upload is repeatedly seeking backwards through an encrypting '
                                    'stream, which re-encrypts from the start each time')
        while self.position < target:
            if not self.read(min(CHUNK_SIZE, target - self.position)):
                break
        return self.position
