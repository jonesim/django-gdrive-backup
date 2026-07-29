import logging
import re

from celery import shared_task
from django.db import connection

from .backup import Backup
from .utils import allowed_to_restore, RESTORE_BLOCKED_MESSAGE

logger = logging.getLogger(__name__)

SCHEMA_NAME_RE = re.compile(r'^[a-z_][a-z0-9_]*$')


@shared_task
def backup(config=None):
    """Beat schedules can run several configs, e.g. kwargs={'config': 'staging'}"""
    Backup(config=config).backup_db_and_folders()


@shared_task
def backup_all_schemas(config=None):
    Backup(config=config).backup_db_and_folders(all_schemas=True)


@shared_task
def empty_trash(config=None):
    Backup(config=config).storage.empty_trash()


@shared_task
def extend_retention(config=None):
    """Top up object-lock retention so every file backup keeps at least the configured
    min_days of protection. Schedule daily via beat (interval must be shorter than
    min_days)."""
    Backup(config=config).extend_file_retention()


class StateLogger:

    def __init__(self, task):
        self.task = task

    def _log(self, log, text):
        log(text)
        self.task.update_state(state='PROGRESS', meta={'message': text})

    def info(self, text):
        self._log(logger.info, text)

    def warning(self, text):
        self._log(logger.warning, text)

    def error(self, text):
        self._log(logger.error, text)


try:
    from ajax_helpers.utils import ajax_command

    @shared_task(bind=True)
    def ajax_backup(self, **kwargs):
        task_kwargs = kwargs['slug'] if 'slug' in kwargs else kwargs
        Backup(StateLogger(self)).backup_db_and_folders(**task_kwargs)
        return {'commands': [ajax_command('message', text='Backup Complete'), ajax_command('reload')]}

    @shared_task(bind=True)
    def ajax_restore(self, *, slug, **_kwargs):
        if not allowed_to_restore():
            return {'commands': [ajax_command('message', text=RESTORE_BLOCKED_MESSAGE)]}
        drop_schema = slug.get('drop_schema')
        if drop_schema:
            if not SCHEMA_NAME_RE.match(drop_schema):
                raise ValueError(f'Invalid schema name for drop_schema: {drop_schema!r}')
            with connection.cursor() as cursor:
                cursor.execute(f'DROP SCHEMA "{drop_schema}" CASCADE')
                cursor.execute(f'CREATE SCHEMA "{drop_schema}"')
        Backup(StateLogger(self)).get_backup_db().restore_db_from_storage(file_id=slug['pk'])
        return {'commands': [ajax_command('message', text='Restore Complete'), ajax_command('reload')]}

except ModuleNotFoundError:
    pass
