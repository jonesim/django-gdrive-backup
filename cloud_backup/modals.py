import json

from django.contrib.auth.mixins import PermissionRequiredMixin, UserPassesTestMixin
from django_modals.helper import modal_button, ajax_modal_redirect, modal_button_method
from django_modals.modals import Modal
from django_modals.task_modals import TaskModal
from ajax_helpers.utils import is_ajax

from cloud_backup.backup import Backup
from cloud_backup.config import config_at
from cloud_backup.utils import allowed_to_restore, RESTORE_BLOCKED_MESSAGE


class SuperUserMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_superuser


class RestoreAllowedMixin(SuperUserMixin):
    """Server-side enforcement of BACKUP_ALLOW_RESTORE — hiding the buttons is not enough."""

    def dispatch(self, request, *args, **kwargs):
        if not allowed_to_restore():
            return self.command_response('message', text=RESTORE_BLOCKED_MESSAGE)
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


class ConfirmBackupModal(SuperUserMixin, Modal):

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


class AdminTaskModal(PermissionRequiredMixin, TaskModal):
    """Read-only tasks (e.g. verify) need the same access as the backup pages,
    not superuser."""
    permission_required = 'access_admin'
    refresh_ms = 500


class ConfirmEmptyTrashModal(SuperUserMixin, Modal):

    modal_title = 'Warning'

    def modal_content(self):
        return 'Are you sure you want to permanently remove deleted items?'

    def button_empty_trash(self, **_kwargs):
        Backup(config=config_at(self.slug.get('config'))).storage.empty_trash()
        return self.command_response('reload')

    def get_modal_buttons(self):
        return [modal_button_method('Confirm', 'empty_trash'),
                modal_button('Cancel', 'close', 'btn-secondary')]
