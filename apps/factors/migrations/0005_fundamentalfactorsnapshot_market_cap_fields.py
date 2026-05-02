from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('factors', '0004_remove_northbound_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='fundamentalfactorsnapshot',
            name='circ_mv',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=20, null=True, verbose_name='Circulating Market Value'),
        ),
        migrations.AddField(
            model_name='fundamentalfactorsnapshot',
            name='float_share',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=20, null=True, verbose_name='Float Share'),
        ),
        migrations.AddField(
            model_name='fundamentalfactorsnapshot',
            name='free_share',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=20, null=True, verbose_name='Free Float Share'),
        ),
        migrations.AddField(
            model_name='fundamentalfactorsnapshot',
            name='total_mv',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=20, null=True, verbose_name='Total Market Value'),
        ),
        migrations.AddField(
            model_name='fundamentalfactorsnapshot',
            name='total_share',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=20, null=True, verbose_name='Total Share'),
        ),
    ]