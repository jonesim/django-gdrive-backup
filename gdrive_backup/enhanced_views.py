import base64
import datetime
import json
from io import BytesIO

from ajax_helpers.mixins import AjaxHelpers, AjaxTaskMixin
from django.conf import settings
from django.contrib.auth.mixins import PermissionRequiredMixin
from django_datatables.columns import DateTimeColumn, DatatableColumn, ColumnLink, ColumnBase
from django_datatables.datatables import DatatableView
from django_datatables.helpers import row_button, overwrite_cell
from django_menus.menu import MenuMixin
from django_modals.datatables import ModalLink
from django_modals.decorators import ConfirmAjaxMethod
from django_modals.helper import reverse_modal
from openpyxl import Workbook

from gdrive_backup.backup import Backup
from .sql_functions import get_schemas, get_schema_tables, get_table_column_names, get_table_data
from .tasks import ajax_backup


def restore_table_button(text):
    return ModalLink(row=True, base64=True, modal_name='gdrive_backup:confirm_restore_db',
                     css_class='btn btn-danger btn-sm', title='Restore', button_text=text,
                     enabled=getattr(settings, 'DEBUG', False))


class TableBackup(AjaxTaskMixin, AjaxHelpers):

    tasks = {'backup': ajax_backup}

    # noinspection PyUnresolvedReferences
    def set_cell_commands(self, table_id, row_no, html):
        self.setup_tables()
        self.add_command(overwrite_cell(
            self.tables[table_id], row_no, 'Backup', f'<span class="text-success">{html}</span>')
        )

    def row_backup_schema(self, *, row_no, table_id, **_kwargs):
        self.set_cell_commands(table_id, row_no, '<div class="spinner-border spinner-border-sm"></div> Backing up')
        if table_id == 'schema_tables':
            if hasattr(self, 'schema'):
                # noinspection PyUnresolvedReferences
                task_kwargs = dict(schema=self.schema, table=row_no[1:])
            else:
                # noinspection PyUnresolvedReferences
                task_kwargs = dict(schema=self.kwargs['schema'], table=row_no[1:])
        else:
            # noinspection PyUnresolvedReferences
            task_kwargs = dict(schema=row_no[1:])
        return self.start_task('backup', task_kwargs=task_kwargs, result_kwargs=dict(table_id=table_id, row_no=row_no))

    def task_state_success(self, *, table_id, row_no, **_kwargs):
        self.set_cell_commands(table_id, row_no, '<i class="fas fa-check-circle"></i> Backup Complete')
        self.add_command('reload_table', table_id='files')
        return self.command_response()


class BackupView(TableBackup,  PermissionRequiredMixin,  MenuMixin, DatatableView):

    template_name = 'gdrive_backup/backup.html'
    permission_required = 'access_admin'

    def setup_menu(self):
        self.add_menu('buttons', menu_type='buttons')
        if self.schema:
            self.add_menu('breadcrumbs', menu_type='breadcrumb').add_items(
                ('gdrive_backup:backup_info', 'backup'),
                ('gdrive_backup:schema_info', self.schema, {'url_args': [self.schema]}),
            )
            self.menus['buttons'].add_items(
                (f'gdrive_backup:confirm_backup,schema-{self.schema}', f'BACKUP {self.schema}'),
                ('gdrive_backup:schema_tables', 'View Tables', {'url_args': [self.schema]}),
            )
        else:
            self.menus['buttons'].add_items(
                ('gdrive_backup:confirm_backup,-', 'Backup database'),
                ('gdrive_backup:confirm_backup,all_schemas-True', 'Backup All Schemas',
                 {'visible': len(self.schemas) > 1}),
                (f'gdrive_backup:schema_info,{self.schemas[0][0]}', f'View {self.schemas[0][0]}',
                 {'visible': len(self.schemas) == 1}),
                ('gdrive_backup:confirm_empty_trash', 'Empty Trash', {'css_classes': 'btn btn-warning'}),
                ('gdrive_backup:confirm_drop_schema,-', 'Drop Public Schema',
                 {'css_classes': 'btn btn-danger', 'visible': getattr(settings, 'DEBUG', False)}),
            )

    # noinspection PyAttributeOutsideInit
    def dispatch(self, request, *args, schema=None, **kwargs):
        self.schema = schema
        self.schemas = get_schemas()
        return super().dispatch(request, *args, **kwargs)

    def add_tables(self):
        self.add_table('files')
        self.add_table('deleted_files')
        if not self.schema and len(self.schemas) > 1:
            self.add_table('schemas')

    @staticmethod
    def setup_files(table):
        table.add_columns('.id', 'ip_address', 'table', 'name', 'size',
                          DateTimeColumn(title='Backup Date', field='createdTime'),
                          DatatableColumn(column_name='drop_restore',
                                          render=[row_button('drop_restore', 'Drop Restore',
                                                             button_classes='btn btn-warning btn-sm',)]),
                          restore_table_button('Restore DB'))
        table.sort('-createdTime')
        table.table_options['stateSave'] = False

    @ConfirmAjaxMethod(message='This will overwrite the current database and data could be lost')
    def row_drop_restore(self, row_data, **kwargs):
        table_row = json.loads(row_data)
        return self.command_response('show_modal',
                                     modal = reverse_modal('gdrive_backup:restore_db'
                                                           ,base64={'pk': table_row[0], 'drop_schema': 'public'}))

    @staticmethod
    def setup_deleted_files(table):
        table.add_columns('.id', 'name', 'size', DateTimeColumn(title='Backup Date', field='createdTime'),
                          DatatableColumn(column_name='Undelete', render=[row_button(
                              'undelete', 'Undelete', button_classes='btn btn-secondary btn-sm'
                          )]))
        table.sort('-createdTime')
        table.table_options['stateSave'] = False

    def row_undelete(self, row_no, **_kwargs):
        db = Backup().get_backup_db()
        db.drive.service.files().update(fileId=row_no[1:], body={'trashed': False}).execute()
        return self.command_response('reload')

    def setup_schemas(self, table):
        table.add_columns(
            'schema', 'size',
            ColumnLink(
                column_name='view_schema', link_ref_column='schema', url_name='gdrive_backup:schema_info',
                link_html='<button class="btn btn-sm btn-outline-dark">VIEW</button>'
            ),
            ColumnBase(column_name='Backup',
                       render=[row_button('backup_schema', 'Backup', button_classes='btn btn-success btn-sm',)])
        )
        table.table_data = [{'schema': s[0], 'size': s[1]} for s in self.schemas]
        table.table_options['column_id'] = 0
        table.sort('schema')
        table.table_options['stateSave'] = False

    def get_context_data(self, **kwargs):
        self.add_page_command('ajax_post', data={'ajax': 'read_storage_info'})
        context = super().get_context_data(**kwargs)
        context['schema'] = self.schema
        return context

    def ajax_read_storage_info(self, **_kwargs):
        db = Backup().get_backup_db(schema=self.schema)
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

    def get_table_query(self, table, **kwargs):
        trashed = {} if table.table_id == 'files' else {'trashed': True}
        files = Backup().get_backup_db(schema=self.schema).get_db_backup_files(**trashed)
        return [dict(**f, **f.get('appProperties', {})) for f in files if not f.get('appProperties', {}).get('table')]


class SchemaTableView(TableBackup, AjaxTaskMixin, PermissionRequiredMixin, AjaxHelpers, MenuMixin, DatatableView):

    template_name = 'gdrive_backup/backup.html'
    permission_required = 'access_admin'

    def setup_menu(self):
        self.add_menu('breadcrumbs', menu_type='breadcrumb').add_items(
            ('gdrive_backup:backup_info', 'backup'),
            ('gdrive_backup:schema_info', self.kwargs['schema'], {'url_args': [self.kwargs['schema']]}),
            ('gdrive_backup:schema_tables', 'tables', {'url_args': [self.kwargs['schema']]}),
        )

    def add_tables(self):
        self.add_table('files')
        self.add_table('schema_tables')

    @staticmethod
    def setup_files(table):
        table.add_columns('.id', 'ip_address', 'table', 'name', 'size',
                          DateTimeColumn(title='Backup Date', field='createdTime'),
                          restore_table_button('Restore Table'))
        table.sort('-createdTime')
        table.table_options['stateSave'] = False

    def row_download_xls(self,  **kwargs):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(get_table_column_names(self.kwargs['schema'], table_name=kwargs['row_no'][1:]))
        for r in get_table_data(self.kwargs['schema'], table_name=kwargs['row_no'][1:]):
            sheet.append([c.replace(tzinfo=None) if isinstance(c, datetime.datetime) else c for c in r])
        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        filename = f'{kwargs["row_no"][1:]}.xlsx'
        return self.command_response('save_file', data=base64.b64encode(output.read()).decode('ascii'),
                                     filename=filename)

    def setup_schema_tables(self, table):
        table.add_columns(
            'table', 'size', ('rows', {'title': 'No. Rows (Approx)'}),
            ColumnBase(column_name='Download',
                       render=[row_button('download_xls', '<i class="far fa-file-excel"></i>',
                                          button_classes='btn btn-outline-secondary btn-sm', )]),
            ColumnBase(column_name='Backup',
                       render=[row_button('backup_schema', 'Backup', button_classes='btn btn-success btn-sm', )])
        )
        table.table_data = [{'table': s[0], 'size': s[1], 'rows': s[2]}
                            for s in get_schema_tables(self.kwargs['schema'])]
        table.table_options['column_id'] = 0
        table.sort('table')
        table.table_options['stateSave'] = False

    def get_table_query(self, table, **kwargs):
        files = Backup().get_backup_db(schema=self.kwargs.get('schema')).get_db_backup_files()
        return [dict(**f, **f['appProperties']) for f in files if f.get('appProperties', {}).get('table')]
