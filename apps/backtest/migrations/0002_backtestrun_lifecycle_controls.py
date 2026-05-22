from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('backtest', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='backtestrun',
            name='current_task_id',
            field=models.CharField(blank=True, max_length=255, verbose_name='Current Task ID'),
        ),
        migrations.AddField(
            model_name='backtestrun',
            name='pending_control_action',
            field=models.CharField(
                choices=[('NONE', 'None'), ('PAUSE', 'Pause'), ('RESTART', 'Restart'), ('DELETE', 'Delete')],
                db_index=True,
                default='NONE',
                max_length=20,
                verbose_name='Pending Control Action',
            ),
        ),
        migrations.AlterField(
            model_name='backtestrun',
            name='status',
            field=models.CharField(
                choices=[('PENDING', 'Pending'), ('RUNNING', 'Running'), ('PAUSED', 'Paused'), ('COMPLETED', 'Completed'), ('FAILED', 'Failed')],
                db_index=True,
                max_length=20,
                verbose_name='Status',
            ),
        ),
    ]