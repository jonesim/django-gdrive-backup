from django.conf import settings

RESTORE_BLOCKED_MESSAGE = 'Restore is not allowed on this server (BACKUP_ALLOW_RESTORE is not enabled)'


def allowed_to_restore():
    return getattr(settings, 'BACKUP_ALLOW_RESTORE', getattr(settings, 'DEBUG', False))
