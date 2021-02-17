from django.core.management.base import BaseCommand
from ...backup import Backup


class Command(BaseCommand):
    def handle(self, *args, **options):
        Backup().backup_db_and_folders()
