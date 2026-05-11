from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('factors', '0005_fundamentalfactorsnapshot_market_cap_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='factorscore',
            name='pe_ttm_percentile_score',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=7, null=True, verbose_name='PE TTM Percentile Score'),
        ),
        migrations.AddField(
            model_name='fundamentalfactorsnapshot',
            name='pe_ttm',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True, verbose_name='PE TTM'),
        ),
    ]