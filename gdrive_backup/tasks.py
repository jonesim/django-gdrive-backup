import logging

from celery import shared_task
from .backup import Backup


@shared_task
def backup():
    Backup().backup_db_and_folders()


try:
    from ajax_helpers.utils import ajax_command

    class EventLogHandler(logging.Handler):
        def __init__(self, task):
            logging.Handler.__init__(self)
            self.task = task

        def emit(self, record):
            self.task.update_state(state='PROGRESS', meta={'message': record.msg})

    def get_logger(task):
        logger = logging.getLogger(__name__)
        handler = EventLogHandler(task)
        logger.addHandler(handler)

    @shared_task(bind=True)
    def ajax_backup(self, **_kwargs):
        Backup(get_logger(self)).backup_db_and_folders()
        return {'commands': [ajax_command('message', text='Backup Complete'), ajax_command('reload')]}


    @shared_task(bind=True)
    def ajax_restore(self, *, slug, **_kwargs):
        Backup(get_logger(self)).get_backup_db().restore_gdrive_db(file_id=slug['pk'])
        return {'commands': [ajax_command('message', text='Restore Complete'), ajax_command('reload')]}

except ModuleNotFoundError:
    pass
