from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import HttpResponseForbidden, JsonResponse
from django.views import View
from django.views.generic import TemplateView
from django.shortcuts import redirect
from .tasks import backup
from .backup import Backup
from .status import get_backup_status

GB = 1024 * 1024 * 1024


class BackupInfo(PermissionRequiredMixin, TemplateView):

    template_name = "cloud_backup/info.html"
    permission_required = 'access_admin'

    def get_context_data(self, **kwargs):
        # always the default config: this fallback UI only exists when django-modals and
        # friends are not installed, and a single static page has nowhere to pick one
        db = Backup().get_backup_db()
        info = db.storage.storage_info(db.base_backup_dir)
        return {
            'name': info['name'],
            'web_link': info['web_link'],
            'space_used': info['used'] / GB if info['used'] is not None else None,
            'space_available': info['limit'] / GB if info['limit'] is not None else None,
            # the buttons this page offers all write, so a restore_only default config
            # (BACKUP_RESTORE_ONLY) shows the listing alone
            'restore_only': db.config.restore_only,
            'supports_trash': db.storage.supports_trash and not db.config.restore_only,
            'protection': db.protection_rows(),
            'files': db.get_db_backup_files(),
            'deleted_files': db.get_db_backup_files(deleted=True),
        }


class BackupView(PermissionRequiredMixin, TemplateView):
    permission_required = 'access_admin'

    def get(self, request, *args, **kwargs):
        backup.delay()
        return redirect('cloud_backup:backup-info')


class EmptyTrashView(PermissionRequiredMixin, TemplateView):
    permission_required = 'access_admin'

    def get(self, request, *args, **kwargs):
        Backup().empty_trash()
        return redirect('cloud_backup:backup-info')


class BackupStatusView(View):
    """Json answer to "are the backups current?" - see status.py. Meant for a monitoring
    agent, so has_permission() is the hook a host overrides to accept its own monitoring
    credential (a header token, an allow-listed address); by default the reader needs
    the backup permission or to be a superuser.

    ``?config=<name>`` (repeatable) limits the destinations; ``?refresh=1`` bypasses the
    short cache."""
    permission_required = 'access_admin'

    def has_permission(self, request):
        user = request.user
        return user.is_authenticated and (user.is_superuser or user.has_perm(self.permission_required))

    def get(self, request, *args, **kwargs):
        if not self.has_permission(request):
            return HttpResponseForbidden()
        return JsonResponse(get_backup_status(request.GET.getlist('config') or None, refresh='refresh' in request.GET))
