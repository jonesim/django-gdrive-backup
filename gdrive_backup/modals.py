import json

from django.contrib.auth.mixins import UserPassesTestMixin
from django.db import connection
from django_modals.helper import modal_button, ajax_modal_redirect, modal_button_method
from django_modals.modals import Modal
from django_modals.task_modals import TaskModal
from ajax_helpers.utils import is_ajax

from gdrive_backup.backup import Backup


class SuperUserMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_superuser


class ConfirmRestoreModal(SuperUserMixin, Modal):

    modal_title = 'Warning'

    def get_modal_buttons(self):
        return [
            modal_button('Confirm', ajax_modal_redirect(
                'gdrive_backup:restore_db', base64={'pk': self.slug['base64'][0]}
            ), 'btn-danger'),
            modal_button('Cancel', 'close', 'btn-secondary')
        ]

    def modal_content(self):
        return 'This will overwrite the current database and data could be lost.'


class ConfirmBackupModal(SuperUserMixin, Modal):

    modal_title = 'Warning'

    def get_modal_buttons(self):
        return [
            modal_button('Yes', ajax_modal_redirect('gdrive_backup:django_backup', slug=self.kwargs['slug']),
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


class ConfirmEmptyTrashModal(SuperUserMixin, Modal):

    modal_title = 'Warning'

    def modal_content(self):
        return 'Are you sure you want to permanently remove deleted items?'

    def button_empty_trash(self, **_kwargs):
        db = Backup().get_backup_db()
        db.drive.service.files().emptyTrash().execute()
        return self.command_response('reload')

    def get_modal_buttons(self):
        return [modal_button_method('Confirm', 'empty_trash'),
                modal_button('Cancel', 'close', 'btn-secondary')]


class ConfirmDropSchemaModal(SuperUserMixin, Modal):

    modal_title = 'Warning'

    def modal_content(self):
        return ('<div class="alert alert-danger"><strong>This will delete all data in the '
                'database and render the whole site unusable</strong><br>'
                'If the page is not refreshed a database can be restored after this process</div>')

    def button_drop_schema(self, **_kwargs):
        with connection.cursor() as cursor:
            cursor.execute("DROP SCHEMA public CASCADE")
            cursor.execute("CREATE SCHEMA public")
        return self.command_response('close')

    def get_modal_buttons(self):
        return [modal_button_method('Confirm', 'drop_schema', 'btn-danger'),
                modal_button('Cancel', 'close', 'btn-success')]
