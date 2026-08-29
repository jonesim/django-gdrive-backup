import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='BackupRun',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('config', models.CharField(max_length=100)),
                ('kind', models.CharField(choices=[('backup', 'Backup'), ('promote', 'Tier promotion'),
                                                   ('extend_retention', 'Retention extension')], max_length=20)),
                ('status', models.CharField(choices=[('running', 'Running'), ('success', 'Success'),
                                                     ('failure', 'Failure')], default='running', max_length=10)),
                ('started', models.DateTimeField(default=django.utils.timezone.now)),
                ('finished', models.DateTimeField(blank=True, null=True)),
                ('error', models.TextField(blank=True)),
                ('detail', models.JSONField(blank=True, default=dict)),
            ],
            options={
                'ordering': ['-started'],
                'indexes': [models.Index(fields=['config', 'kind', '-started'],
                                         name='cloud_backu_config_0f7b3c_idx')],
            },
        ),
    ]
