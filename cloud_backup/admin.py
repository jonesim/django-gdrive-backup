from django.contrib import admin

from .models import BackupRun


@admin.register(BackupRun)
class BackupRunAdmin(admin.ModelAdmin):
    """Read-only run log. Rows are written by runs.record_run and pruned automatically
    after BACKUP_RUN_HISTORY_DAYS; the enhanced UI has its own page (backup/runs/), this
    is for host projects running the basic UI - or anyone already living in admin."""

    list_display = ('started', 'config', 'kind', 'status', 'duration_display', 'short_error')
    list_filter = ('config', 'kind', 'status')
    date_hierarchy = 'started'
    search_fields = ('error',)

    @admin.display(description='Duration')
    def duration_display(self, run):
        return run.duration_display()

    @admin.display(description='Error')
    def short_error(self, run):
        return run.error[:120]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
