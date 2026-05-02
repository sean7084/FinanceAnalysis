from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('markets', '0005_sync_daily_refresh_pipeline'),
    ]

    operations = [
        migrations.CreateModel(
            name='BenchmarkIndexDaily',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('index_code', models.CharField(db_index=True, max_length=20, verbose_name='Index Code')),
                ('index_name', models.CharField(max_length=100, verbose_name='Index Name')),
                ('trade_date', models.DateField(db_index=True, verbose_name='Trade Date')),
                ('open', models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True, verbose_name='Open')),
                ('high', models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True, verbose_name='High')),
                ('low', models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True, verbose_name='Low')),
                ('close', models.DecimalField(decimal_places=4, max_digits=12, verbose_name='Close')),
                ('source', models.CharField(default='tushare_index_daily', max_length=50, verbose_name='Source')),
            ],
            options={
                'verbose_name': 'Benchmark Index Daily',
                'verbose_name_plural': 'Benchmark Index Daily',
                'ordering': ['-trade_date', 'index_code'],
                'unique_together': {('index_code', 'trade_date')},
            },
        ),
        migrations.AddIndex(
            model_name='benchmarkindexdaily',
            index=models.Index(fields=['index_code', 'trade_date'], name='markets_ben_index_c_786b2f_idx'),
        ),
    ]