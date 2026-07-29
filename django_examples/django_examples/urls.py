from django.contrib import admin
from django.urls import include, path
from django.views.generic.base import RedirectView
from django.apps import apps


from modal_2fa.utils import get_custom_auth

from backup_examples.views.branded_backup import BrandedBackupView, BrandedSchemaTableView
from cloud_backup.urls import backup_urlpatterns


urlpatterns = [
    path('admin/', admin.site.urls),
    path('src/', include('show_src_code.urls')),
    path('favicon.ico', RedirectView.as_view(url='/static/modal_examples/favicon.ico', permanent=True)),
    path('backup/', include('cloud_backup.urls')),
    # Branded example (see backup_examples/views/branded_backup.py). Both instances share
    # the 'cloud_backup' namespace so internal links resolve to the first-registered one
    # (/backup/); a real project mounts only one of the two.
    path('branded-backup/', include((backup_urlpatterns(
        backup_view=BrandedBackupView, schema_table_view=BrandedSchemaTableView), 'cloud_backup'))),
]

urlpatterns += [
    path('', include(get_custom_auth().paths(include_admin=False))),
]

#a = apps.get_app_configs()
#for c in a:

#    if hasattr(c, 'urls'):
#        urlpatterns += [path('', include(c.urls))]
