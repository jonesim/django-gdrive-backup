from django.apps import apps
from django.urls import path


if all([apps.is_installed(m) for m in ['django_modals', 'django_datatables', 'django_menus', 'ajax_helpers']]):

    from .tasks import ajax_restore, ajax_backup
    from . import enhanced_views as views
    from . import modals as modals

    app_name = 'gdrive_backup'
    urlpatterns = [
        path('', views.BackupView.as_view(), name='backup_info'),
        path('<str:schema>/', views.BackupView.as_view(), name='schema_info'),
        path('<str:schema>/tables/', views.SchemaTableView.as_view(), name='schema_tables'),
        path('modal/backup/<str:slug>/', modals.SuperUserTaskModal.as_view(task=ajax_backup), name='django_backup'),
        path('modal/confim_restore/<str:base64>/', modals.ConfirmRestoreModal.as_view(), name='confirm_restore_db'),
        path('modal/restore/<str:base64>/', modals.SuperUserTaskModal.as_view(task=ajax_restore), name='restore_db'),
        path('modal/confim_backup/<str:slug>/', modals.ConfirmBackupModal.as_view(), name='confirm_backup'),
        path('modal/confirm_empty_trash/', modals.ConfirmEmptyTrashModal.as_view(), name='confirm_empty_trash'),
        path('modal/confirm_drop_schema/<str:slug>/', modals.ConfirmDropSchemaModal.as_view(),
             name='confirm_drop_schema'),
    ]

else:

    from . import views

    urlpatterns = [
        path('', views.BackupInfo.as_view(), name='backup-info'),
        path('backupnow', views.BackupView.as_view(), name='backup-now'),
        path('empty-trash', views.EmptyTrashView.as_view(), name='empty-trash'),
    ]
