import json

from django.contrib.auth.mixins import PermissionRequiredMixin, UserPassesTestMixin
from django_modals.helper import modal_button, ajax_modal_redirect, modal_button_method
from django_modals.modals import Modal
from django_modals.task_modals import TaskModal
from ajax_helpers.utils import is_ajax

from cloud_backup.backup import Backup
from cloud_backup.config import config_at, safe_config
from cloud_backup.utils import allowed_to_restore, BACKUP_BLOCKED_MESSAGE, RESTORE_BLOCKED_MESSAGE


class SuperUserMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_superuser


class RestoreAllowedMixin(SuperUserMixin):
    """Server-side enforcement of BACKUP_ALLOW_RESTORE — hiding the buttons is not enough."""

    def dispatch(self, request, *args, **kwargs):
        if not allowed_to_restore():
            return self.command_response('message', text=RESTORE_BLOCKED_MESSAGE)
        return super().dispatch(request, *args, **kwargs)


class BackupAllowedMixin(SuperUserMixin):
    """Server-side enforcement of restore_only, the same way RestoreAllowedMixin enforces
    BACKUP_ALLOW_RESTORE. Backup.check_writable() is still the backstop, but a modal that
    opens and then fails its task is a worse answer than one that says why.

    split_slug() runs in BaseModalMixin.dispatch, which is further down the MRO than this,
    so the config index is read from the raw slug rather than self.slug."""

    def dispatch(self, request, *args, **kwargs):
        # anyone else falls through to the permission check rather than being told
        # anything about the destination
        if request.user.is_superuser:
            slug = (kwargs.get('slug') or '').split('-')
            config_values = [slug[k + 1] for k in range(0, len(slug) - 1, 2) if slug[k] == 'config']
            config = safe_config(config_at(config_values[0] if config_values else None))
            if config is not None and config.restore_only:
                return self.command_response('message', text=BACKUP_BLOCKED_MESSAGE)
        return super().dispatch(request, *args, **kwargs)


class ConfirmRestoreModal(RestoreAllowedMixin, Modal):

    modal_title = 'Warning'

    def get_modal_buttons(self):
        # the payload is the dict the restore row buttons build, or a bare row list from a
        # datatable ModalLink; anything past the pk has to be forwarded explicitly
        payload = {'pk': self.slug['base64'][0] if 'base64' in self.slug else self.slug['pk']}
        if 'config' in self.slug:
            payload['config'] = self.slug['config']
        return [
            modal_button('Confirm', ajax_modal_redirect('cloud_backup:restore_db', base64=payload), 'btn-danger'),
            modal_button('Cancel', 'close', 'btn-secondary')
        ]

    def modal_content(self):
        return 'This will overwrite the current database and data could be lost.'


class ConfirmBackupModal(BackupAllowedMixin, Modal):

    modal_title = 'Warning'

    def get_modal_buttons(self):
        return [
            modal_button('Yes', ajax_modal_redirect('cloud_backup:django_backup', slug=self.kwargs['slug']),
                         'btn-warning'),
            modal_button('Cancel', 'close', 'btn-secondary')
        ]

    def modal_content(self):
        return 'Are you sure you want to backup?'


class SuperUserTaskModal(SuperUserMixin, TaskModal):
    refresh_ms = 500

    def dispatch(self, request, *args, **kwargs):
        if is_ajax(request) and request.content_type == 'application/json':
            response = json.loads(request.body)
            if response.get('ajax') == 'check_result':
                self.test_func = lambda: True
        return super().dispatch(request, *args, **kwargs)


class RestoreTaskModal(RestoreAllowedMixin, SuperUserTaskModal):
    pass


class BackupTaskModal(BackupAllowedMixin, SuperUserTaskModal):
    pass


class AdminTaskModal(PermissionRequiredMixin, TaskModal):
    """Read-only tasks (e.g. verify) need the same access as the backup pages,
    not superuser."""
    permission_required = 'access_admin'
    refresh_ms = 500


class ConfirmEmptyTrashModal(BackupAllowedMixin, Modal):

    modal_title = 'Warning'

    def modal_content(self):
        return 'Are you sure you want to permanently remove deleted items?'

    def button_empty_trash(self, **_kwargs):
        Backup(config=config_at(self.slug.get('config'))).empty_trash()
        return self.command_response('reload')

    def get_modal_buttons(self):
        return [modal_button_method('Confirm', 'empty_trash'),
                modal_button('Cancel', 'close', 'btn-secondary')]
