from django.apps import apps
from django.core.management.base import BaseCommand
from ...import_data import import_data


class Command(BaseCommand):
    def handle(self, *args, **options):
        print('g')
        import_data(apps.get_app_config('backup_examples').path)
