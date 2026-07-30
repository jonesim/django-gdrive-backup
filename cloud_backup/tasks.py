import logging
import re

from celery import shared_task
from django.db import connection

from .backup import Backup
from .backup_local_files import BackupLocal
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
        # slug values are strings, so boolean kwargs (include_db-False, all_schemas-True)
        # must be converted before reaching backup_db_and_folders
        task_kwargs = {k: v == 'True' if v in ('True', 'False') else v for k, v in task_kwargs.items()}
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

    @shared_task(bind=True)
    def ajax_verify_files(self, *, slug, **_kwargs):
        backup = Backup(StateLogger(self))
        source_dir, dest_name = backup.config.dirs[int(slug['backup_dir'])]
        local = BackupLocal(backup.storage, backup.config.root, backup.logger, config=backup.config)
        results = local.verify_folder(source_dir, dest_name)
        summary = (f"{results['matched']} matched, {len(results['changed'])} changed, "
                   f"{len(results['missing'])} missing locally")
        if results['no_checksum']:
            summary += f", {len(results['no_checksum'])} without a stored checksum"
        problems = results['changed'] + results['missing']
        if problems:
            listed = problems[:10]
            summary += ': ' + ', '.join(listed)
            if len(problems) > len(listed):
                summary += f' and {len(problems) - len(listed)} more'
        return {'commands': [ajax_command('message', text=f'Verify complete - {summary}')]}

except ModuleNotFoundError:
    pass
