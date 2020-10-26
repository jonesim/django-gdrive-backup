from django.urls import path
from . import views


urlpatterns = [
    path('', views.BackupInfo.as_view(), name='backup-info'),
    path('backupnow', views.BackupView.as_view(), name='backup-now')
]
