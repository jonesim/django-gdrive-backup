from django.contrib import admin
from django.urls import include, path
from django.views.generic.base import RedirectView
from django.apps import apps


from modal_2fa.utils import get_custom_auth


urlpatterns = [
    path('admin/', admin.site.urls),
    path('src/', include('show_src_code.urls')),
    path('favicon.ico', RedirectView.as_view(url='/static/modal_examples/favicon.ico', permanent=True)),
    path('backup/', include('gdrive_backup.urls')),
]

urlpatterns += [
    path('', include(get_custom_auth().paths(include_admin=False))),
]

#a = apps.get_app_configs()
#for c in a:

#    if hasattr(c, 'urls'):
#        urlpatterns += [path('', include(c.urls))]
