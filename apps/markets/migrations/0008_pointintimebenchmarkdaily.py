from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('markets', '0007_remove_obsolete_daily_beat_rows'),
    ]

    operations = [
        migrations.CreateModel(
            name='PointInTimeBenchmarkDaily',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('benchmark_code', models.CharField(db_index=True, max_length=40, verbose_name='Benchmark Code')),
                ('benchmark_name', models.CharField(max_length=120, verbose_name='Benchmark Name')),
                ('trade_date', models.DateField(db_index=True, verbose_name='Trade Date')),
                ('daily_return', models.DecimalField(decimal_places=8, default=0, max_digits=12, verbose_name='Daily Return')),
                ('nav', models.DecimalField(decimal_places=8, max_digits=20, verbose_name='Net Asset Value')),
                ('constituent_count', models.PositiveIntegerField(default=0, verbose_name='Constituent Count')),
                ('overlap_count', models.PositiveIntegerField(default=0, verbose_name='Overlap Count')),
                ('weighting_method', models.CharField(default='free_float_market_cap', max_length=60, verbose_name='Weighting Method')),
                ('metadata', models.JSONField(blank=True, default=dict, verbose_name='Metadata')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Point In Time Benchmark Daily',
                'verbose_name_plural': 'Point In Time Benchmark Daily',
                'ordering': ['-trade_date', 'benchmark_code'],
                'unique_together': {('benchmark_code', 'trade_date')},
            },
        ),
        migrations.AddIndex(
            model_name='pointintimebenchmarkdaily',
            index=models.Index(fields=['benchmark_code', 'trade_date'], name='markets_poi_benchma_8ab4b4_idx'),
        ),
    ]