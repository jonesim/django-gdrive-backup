import logging

from celery import shared_task
from .backup import Backup

logger = logging.getLogger(__name__)


@shared_task
def backup():
    Backup().backup_db_and_folders()


class StateLogger:

    def __init__(self, task):
        self.task = task

    def info(self, text):
        logger.info(text)
        self.task.update_state(state='PROGRESS', meta={'message': text})


try:
    from ajax_helpers.utils import ajax_command

    @shared_task(bind=True)
    def ajax_backup(self, schema=None, table=None, **kwargs):
        if 'slug' in kwargs:
            schema = kwargs['slug'].get('pk')
        Backup(StateLogger(self)).backup_db_and_folders(schema=schema, table=table)
        return {'commands': [ajax_command('message', text='Backup Complete'), ajax_command('reload')]}

    @shared_task(bind=True)
    def ajax_restore(self, *, slug, **_kwargs):
        Backup(StateLogger(self)).get_backup_db().restore_gdrive_db(file_id=slug['pk'])
        return {'commands': [ajax_command('message', text='Restore Complete'), ajax_command('reload')]}

except ModuleNotFoundError:
    pass
