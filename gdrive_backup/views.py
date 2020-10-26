from django.contrib.auth.mixins import PermissionRequiredMixin
from django.views.generic import TemplateView
from django.shortcuts import redirect
from .tasks import backup
from .backup import Backup


class BackupInfo(PermissionRequiredMixin, TemplateView):

    template_name = "info.html"
    permission_required = 'access_admin'

    def get_context_data(self, **kwargs):
        db = Backup().get_backup_db()
        about = (db.drive.service.about().get(fields='*').execute())
        meta = db.base_backup_dir
        meta['space_used'] = int(about['storageQuota']['usage']) / (1024*1024*1024)
        meta['space_available'] = int(about['storageQuota']['limit']) / (1024*1024*1024)
        meta['files'] = db.get_db_backup_files()
        meta['deleted_files'] = db.get_db_backup_files(trashed=True)
        return meta


class BackupView(PermissionRequiredMixin, TemplateView):
    permission_required = 'access_admin'

    def get(self, request, *args, **kwargs):
        backup.delay()
        return redirect('backup-info')
