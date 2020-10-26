from celery import shared_task
from .backup import Backup


@shared_task
def backup():
    Backup().backup_db_and_folders()
