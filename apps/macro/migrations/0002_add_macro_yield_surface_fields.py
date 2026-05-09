from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('macro', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='macrosnapshot',
            name='cn1y_yield',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=8, null=True, verbose_name='China 1Y Yield'),
        ),
        migrations.AddField(
            model_name='macrosnapshot',
            name='cn30y_yield',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=8, null=True, verbose_name='China 30Y Yield'),
        ),
        migrations.AddField(
            model_name='macrosnapshot',
            name='cn3y_yield',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=8, null=True, verbose_name='China 3Y Yield'),
        ),
        migrations.AddField(
            model_name='macrosnapshot',
            name='cn5y_yield',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=8, null=True, verbose_name='China 5Y Yield'),
        ),
        migrations.AddField(
            model_name='macrosnapshot',
            name='cn6m_yield',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=8, null=True, verbose_name='China 6M Yield'),
        ),
        migrations.AddField(
            model_name='macrosnapshot',
            name='cn7y_yield',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=8, null=True, verbose_name='China 7Y Yield'),
        ),
    ]