from django.contrib.auth.mixins import PermissionRequiredMixin
from django.views.generic import TemplateView
from django.shortcuts import redirect
from .tasks import backup
from .backup import Backup

GB = 1024 * 1024 * 1024


class BackupInfo(PermissionRequiredMixin, TemplateView):

    template_name = "gdrive_backup/info.html"
    permission_required = 'access_admin'

    def get_context_data(self, **kwargs):
        db = Backup().get_backup_db()
        info = db.storage.storage_info(db.base_backup_dir)
        return {
            'name': info['name'],
            'web_link': info['web_link'],
            'space_used': info['used'] / GB if info['used'] is not None else None,
            'space_available': info['limit'] / GB if info['limit'] is not None else None,
            'supports_trash': db.storage.supports_trash,
            'files': db.get_db_backup_files(),
            'deleted_files': db.get_db_backup_files(deleted=True),
        }


class BackupView(PermissionRequiredMixin, TemplateView):
    permission_required = 'access_admin'

    def get(self, request, *args, **kwargs):
        backup.delay()
        return redirect('backup-info')


class EmptyTrashView(PermissionRequiredMixin, TemplateView):
    permission_required = 'access_admin'

    def get(self, request, *args, **kwargs):
        Backup().storage.empty_trash()
        return redirect('backup-info')
