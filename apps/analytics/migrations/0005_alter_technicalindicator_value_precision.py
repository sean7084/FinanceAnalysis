from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0004_phase10_signal_events'),
    ]

    operations = [
        migrations.AlterField(
            model_name='technicalindicator',
            name='value',
            field=models.DecimalField(decimal_places=8, max_digits=24, verbose_name='Value'),
        ),
    ]