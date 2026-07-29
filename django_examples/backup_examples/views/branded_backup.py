from cloud_backup.enhanced_views import BackupBaseView, SchemaTableBaseView


class BrandedBackupView(BackupBaseView):
    template_name = 'backup_examples/branded_backup.html'


class BrandedSchemaTableView(SchemaTableBaseView):
    template_name = 'backup_examples/branded_backup.html'
