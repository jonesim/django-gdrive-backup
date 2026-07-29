from django.apps import apps
from django.urls import path

app_name = 'cloud_backup'


if all([apps.is_installed(m) for m in ['django_modals', 'django_datatables', 'django_menus', 'ajax_helpers']]):

    from .tasks import ajax_restore, ajax_backup
    from . import enhanced_views as views
    from . import modals as modals

    def backup_urlpatterns(backup_view=None, schema_table_view=None):
        """Full enhanced pattern list with the two page views swappable, so a branded
        subclass keeps the URL names the menus and modals reverse. Host usage:
        path('backup/', include((backup_urlpatterns(backup_view=MyBackupView), 'cloud_backup')))"""
        backup_view = backup_view or views.BackupView
        schema_table_view = schema_table_view or views.SchemaTableView
        return [
            path('', backup_view.as_view(), name='backup_info'),
            path('<str:schema>/', backup_view.as_view(), name='schema_info'),
            path('<str:schema>/tables/', schema_table_view.as_view(), name='schema_tables'),
            path('modal/backup/<str:slug>/', modals.SuperUserTaskModal.as_view(task=ajax_backup),
                 name='django_backup'),
            path('modal/confim_restore/<str:base64>/', modals.ConfirmRestoreModal.as_view(),
                 name='confirm_restore_db'),
            path('modal/restore/<str:base64>/', modals.RestoreTaskModal.as_view(task=ajax_restore),
                 name='restore_db'),
            path('modal/confim_backup/<str:slug>/', modals.ConfirmBackupModal.as_view(), name='confirm_backup'),
            path('modal/confirm_empty_trash/', modals.ConfirmEmptyTrashModal.as_view(), name='confirm_empty_trash'),
            path('modal/confirm_drop_schema/<str:slug>/', modals.ConfirmDropSchemaModal.as_view(),
                 name='confirm_drop_schema'),
        ]

    urlpatterns = backup_urlpatterns()

else:

    from . import views

    urlpatterns = [
        path('', views.BackupInfo.as_view(), name='backup-info'),
        path('backupnow', views.BackupView.as_view(), name='backup-now'),
        path('empty-trash', views.EmptyTrashView.as_view(), name='empty-trash'),
    ]
