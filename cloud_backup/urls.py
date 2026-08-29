from django.apps import apps
from django.urls import path

app_name = 'cloud_backup'


if all([apps.is_installed(m) for m in ['django_modals', 'django_datatables', 'django_menus', 'ajax_helpers']]):

    from .tasks import ajax_restore, ajax_backup, ajax_verify_files
    from . import enhanced_views as views
    from . import modals as modals
    from .views import BackupStatusView

    def backup_urlpatterns(backup_view=None, schema_table_view=None, files_view=None, setup_view=None,
                           status_view=None, runs_view=None):
        """Full enhanced pattern list with the page views swappable, so a branded
        subclass keeps the URL names the menus and modals reverse. Host usage:
        path('backup/', include((backup_urlpatterns(backup_view=MyBackupView), 'cloud_backup')))"""
        backup_view = backup_view or views.BackupView
        schema_table_view = schema_table_view or views.SchemaTableView
        files_view = files_view or views.BackupFilesView
        setup_view = setup_view or views.StorageSetupView
        status_view = status_view or BackupStatusView
        runs_view = runs_view or views.BackupRunsView
        return [
            path('', backup_view.as_view(), name='backup_info'),
            path('files/', files_view.as_view(), name='backup_files_root'),
            path('files/<int:backup_dir>/', files_view.as_view(), name='backup_files'),
            path('files/<int:backup_dir>/<path:sub_path>/', files_view.as_view(), name='backup_files_path'),
            # single-segment paths must stay above <str:schema>, which matches anything
            path('setup/', setup_view.as_view(), name='storage_setup'),
            path('status/', status_view.as_view(), name='backup_status'),
            path('runs/', runs_view.as_view(), name='backup_runs'),
            path('modal/verify_files/<str:slug>/', modals.AdminTaskModal.as_view(task=ajax_verify_files),
                 name='verify_files'),
            path('<str:schema>/', backup_view.as_view(), name='schema_info'),
            path('<str:schema>/tables/', schema_table_view.as_view(), name='schema_tables'),
            path('modal/backup/<str:slug>/', modals.BackupTaskModal.as_view(task=ajax_backup),
                 name='django_backup'),
            path('modal/confim_restore/<str:base64>/', modals.ConfirmRestoreModal.as_view(),
                 name='confirm_restore_db'),
            path('modal/restore/<str:base64>/', modals.RestoreTaskModal.as_view(task=ajax_restore),
                 name='restore_db'),
            path('modal/confim_backup/<str:slug>/', modals.ConfirmBackupModal.as_view(), name='confirm_backup'),
            path('modal/confirm_empty_trash/', modals.ConfirmEmptyTrashModal.as_view(), name='confirm_empty_trash'),
            # same name with a slug so the button can say which config's trash to empty;
            # the argument-less pattern above stays for anything reversing it without one
            path('modal/confirm_empty_trash/<str:slug>/', modals.ConfirmEmptyTrashModal.as_view(),
                 name='confirm_empty_trash'),
        ]

    urlpatterns = backup_urlpatterns()

else:

    from . import views

    urlpatterns = [
        path('', views.BackupInfo.as_view(), name='backup-info'),
        path('backupnow', views.BackupView.as_view(), name='backup-now'),
        path('empty-trash', views.EmptyTrashView.as_view(), name='empty-trash'),
        path('status/', views.BackupStatusView.as_view(), name='backup_status'),
    ]
