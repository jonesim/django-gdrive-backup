from django.db import models
from django.utils import timezone


class BackupRun(models.Model):
    """One run of a backup, tier promotion or retention extension, recorded by
    runs.record_run so the status check (status.py) can say whether the task ran and
    succeeded without a Celery result backend - and so a beat that has stopped, which
    raises nothing anywhere, shows up as a run that never happened.

    The rows live in the database being backed up, so a restore rewinds the history
    to whatever the dump held - the destination listing, not this table, is the proof
    that dumps exist."""

    BACKUP = 'backup'
    PROMOTE = 'promote'
    EXTEND_RETENTION = 'extend_retention'
    KINDS = ((BACKUP, 'Backup'), (PROMOTE, 'Tier promotion'), (EXTEND_RETENTION, 'Retention extension'))

    RUNNING = 'running'
    SUCCESS = 'success'
    FAILURE = 'failure'
    STATUSES = ((RUNNING, 'Running'), (SUCCESS, 'Success'), (FAILURE, 'Failure'))

    config = models.CharField(max_length=100)
    kind = models.CharField(max_length=20, choices=KINDS)
    status = models.CharField(max_length=10, choices=STATUSES, default=RUNNING)
    started = models.DateTimeField(default=timezone.now)
    finished = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    # what the run did - schemas dumped, folders backed up, promotion counts - for the
    # status endpoint and the run log, not parsed by anything
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-started']
        indexes = [models.Index(fields=['config', 'kind', '-started'])]

    def __str__(self):
        return f'{self.config} {self.kind} {self.started:%Y-%m-%d %H:%M} {self.status}'

    @property
    def duration(self):
        if self.finished is None:
            return None
        return (self.finished - self.started).total_seconds()

    def duration_display(self):
        if self.finished is None:
            return ''
        minutes, seconds = divmod(int(self.duration), 60)
        if not minutes:
            return f'{seconds} s'
        hours, minutes = divmod(minutes, 60)
        if not hours:
            return f'{minutes} min {seconds:02d} s'
        return f'{hours} h {minutes:02d} min'
