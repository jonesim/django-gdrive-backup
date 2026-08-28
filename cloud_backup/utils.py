from django.conf import settings

RESTORE_BLOCKED_MESSAGE = 'Restore is not allowed on this server (BACKUP_ALLOW_RESTORE is not enabled)'
BACKUP_BLOCKED_MESSAGE = ('This destination is restore only - it holds backups made elsewhere and nothing '
                          'is written to it from here')


def allowed_to_restore():
    return getattr(settings, 'BACKUP_ALLOW_RESTORE', getattr(settings, 'DEBUG', False))
