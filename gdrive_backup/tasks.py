import logging

from celery import shared_task
from django.db import connection

from .backup import Backup

logger = logging.getLogger(__name__)


@shared_task
def backup():
    Backup().backup_db_and_folders()


@shared_task
def backup_all_schemas():
    Backup().backup_db_and_folders(all_schemas=True)


@shared_task
def empty_trash():
    Backup().storage.empty_trash()


@shared_task
def extend_retention():
    """Top up object-lock retention so every file backup keeps at least the configured
    min_days of protection. Schedule daily via beat (interval must be shorter than
    min_days)."""
    Backup().extend_file_retention()


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
        if slug.get('drop_schema'):
            with connection.cursor() as cursor:
                cursor.execute(f'DROP SCHEMA {slug.get("drop_schema")} CASCADE')
                cursor.execute(f'CREATE SCHEMA {slug.get("drop_schema")}')
        Backup(StateLogger(self)).get_backup_db().restore_db_from_storage(file_id=slug['pk'])
        return {'commands': [ajax_command('message', text='Restore Complete'), ajax_command('reload')]}

except ModuleNotFoundError:
    pass
