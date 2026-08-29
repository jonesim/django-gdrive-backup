"""Record each backup run in the database - see models.BackupRun.

Recording must never break a backup: every database write here is wrapped, and a failure
to write the row is logged and ignored. The one case that matters is a run that fails
with a database error (the database itself is what is being backed up): the failure row
cannot be written either, so the status check reports the run as still running and, once
it is older than the schedule allows, as stuck.
"""
import contextlib
import datetime
import logging

from django.conf import settings
from django.utils import timezone

from .models import BackupRun

logger = logging.getLogger(__name__)

# How long finished runs are kept. A setting rather than a config option because the
# rows are pruned as a side effect of writing new ones, whichever config wrote them.
DEFAULT_HISTORY_DAYS = 90


def history_days():
    return getattr(settings, 'BACKUP_RUN_HISTORY_DAYS', DEFAULT_HISTORY_DAYS)


def _save(run, log):
    try:
        run.save()
        return run
    except Exception as e:  # noqa: BLE001 - the backup carries on without its record
        log.warning(f'Could not record the backup run: {e}')
        return None


@contextlib.contextmanager
def record_run(config_name, kind, log=None):
    """Wrap a run: writes a running row first (so a crash that never returns still
    leaves a trace), then marks it finished. Yields the detail dict the body may fill
    in; it is stored with the row."""
    log = log or logger
    run = _save(BackupRun(config=config_name, kind=kind), log)
    detail = {}
    try:
        yield detail
    except BaseException as e:
        finish(run, BackupRun.FAILURE, detail, f'{type(e).__name__}: {e}', log)
        raise
    finish(run, BackupRun.SUCCESS, detail, '', log)


def finish(run, status, detail, error, log):
    if run is None:
        return
    run.status = status
    run.finished = timezone.now()
    run.detail = detail
    run.error = error[:5000]
    if _save(run, log):
        prune(log)


def prune(log=logger):
    cutoff = timezone.now() - datetime.timedelta(days=history_days())
    try:
        BackupRun.objects.filter(started__lt=cutoff).exclude(status=BackupRun.RUNNING).delete()
    except Exception as e:  # noqa: BLE001
        log.warning(f'Could not prune old backup runs: {e}')


def latest_run(config_name, kind):
    """The most recent run of one kind for a config, or None - raises on a database
    error so the caller can report the check as unknown rather than missing."""
    return BackupRun.objects.filter(config=config_name, kind=kind).order_by('-started').first()
