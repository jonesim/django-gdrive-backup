from ajax_helpers.mixins import AjaxHelpers
from django.conf import settings
from django.contrib.auth.mixins import PermissionRequiredMixin, UserPassesTestMixin
from django.db import connection
from django_datatables.columns import DateTimeColumn, DatatableColumn
from django_datatables.datatables import DatatableView
from django_datatables.helpers import row_button
from django_menus.menu import MenuMixin
from django_menus.modal_item import ModalMenuItem
from django_modals.datatables import ModalLink
from django_modals.helper import modal_button, ajax_modal_redirect, modal_button_method
from django_modals.modals import Modal
from django_modals.task_modals import TaskModal
from gdrive_backup.backup import Backup


class BackupView(PermissionRequiredMixin, AjaxHelpers, MenuMixin, DatatableView):

    template_name = 'gdrive_backup.backup.html'
    permission_required = 'access_admin'

    def setup_menu(self):
        self.add_menu('buttons', menu_type='buttons').add_items(
            ModalMenuItem('gdrive_backup:confirm_backup', 'BACKUP Now'),
            ModalMenuItem('gdrive_backup:confirm_empty_trash', 'Empty Trash', css_classes='btn btn-warning'),
            ModalMenuItem('gdrive_backup:confirm_drop_schema', 'Drop Public Schema', css_classes='btn btn-danger',
                          visible=getattr(settings, 'DEBUG', False)),
        )

    def add_tables(self):
        self.add_table('files')
        self.add_table('deleted_files')

    @staticmethod
    def setup_files(table):
        table.add_columns('.id', 'name', 'size', DateTimeColumn(title='Backup Date', field='createdTime'),
                          ModalLink(
                              modal_name='gdrive_backup:confirm_restore_db', css_class='btn btn-danger btn-sm',
                              title='Restore', button_text='Restore DB', enabled=getattr(settings, 'DEBUG', False)
                          )
                          )

    @staticmethod
    def setup_deleted_files(table):
        table.add_columns('.id', 'name', 'size', DateTimeColumn(title='Backup Date', field='createdTime'),
                          DatatableColumn(column_name='Undelete', render=[row_button(
                              'undelete', 'Undelete', button_classes='btn btn-secondary btn-sm'
                          )]))

    def row_undelete(self, row_no, **_kwargs):
        db = Backup().get_backup_db()
        db.drive.service.files().update(fileId=row_no[1:], body={'trashed': False}).execute()
        return self.command_response('reload')

    def get_context_data(self, **kwargs):
        self.add_page_command('ajax_post', data={'ajax': 'read_storage_info'})
        return super().get_context_data(**kwargs)

    def ajax_read_storage_info(self, **_kwargs):
        db = Backup().get_backup_db()
        meta = db.base_backup_dir
        folder_button = '<a target="_blank" href="{}">{}</a>'.format(meta['webViewLink'], meta['name'])
        about = db.drive.service.about().get(fields='*').execute()
        return self.command_response(
            'html',
            selector='#storage_info',
            html="Google Drive Folder {}<br>{:.1f} GB Used of {:.1f} GB".format(
                folder_button,
                int(about['storageQuota']['usage']) / (1024*1024*1024),
                int(about['storageQuota']['limit']) / (1024*1024*1024)
            )
        )

    @staticmethod
    def get_table_query(table, **kwargs):
        if table.table_id == 'files':
            return Backup().get_backup_db().get_db_backup_files()
        else:
            return Backup().get_backup_db().get_db_backup_files(trashed=True)


class SuperUserMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_superuser


class ConfirmRestoreModal(SuperUserMixin, Modal):

    modal_title = 'Warning'

    def get_modal_buttons(self):
        return [
            modal_button('Confirm', ajax_modal_redirect('gdrive_backup:restore_db', slug=self.kwargs['slug']), 'btn-danger'),
            modal_button('Cancel', 'close', 'btn-secondary')
        ]

    def modal_content(self):
        return 'This will overwrite the current database and data could be lost.'


class ConfirmBackupModal(SuperUserMixin, Modal):

    modal_title = 'Warning'

    def get_modal_buttons(self):
        return [
            modal_button('Yes', ajax_modal_redirect('gdrive_backup:django_backup'), 'btn-warning'),
            modal_button('Cancel', 'close', 'btn-secondary')
        ]

    def modal_content(self):
        return 'Are you sure you want to backup?'


class SuperUserTaskModal(SuperUserMixin, TaskModal):
    refresh_ms = 500


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
