from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('markets', '0008_pointintimebenchmarkdaily'),
    ]

    operations = [
        migrations.AddField(
            model_name='asset',
            name='delist_date',
            field=models.DateField(blank=True, help_text='Delisting date from TuShare stock_basic', null=True, verbose_name='Delist Date'),
        ),
        migrations.CreateModel(
            name='ExchangeTradingCalendar',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('exchange_code', models.CharField(db_index=True, max_length=10, verbose_name='Exchange Code')),
                ('trade_date', models.DateField(db_index=True, verbose_name='Trade Date')),
                ('previous_trade_date', models.DateField(blank=True, null=True, verbose_name='Previous Trade Date')),
                ('source', models.CharField(default='tushare_trade_cal', max_length=50, verbose_name='Source')),
            ],
            options={
                'verbose_name': 'Exchange Trading Calendar',
                'verbose_name_plural': 'Exchange Trading Calendars',
                'ordering': ['exchange_code', '-trade_date'],
                'unique_together': {('exchange_code', 'trade_date')},
            },
        ),
        migrations.CreateModel(
            name='AssetSuspension',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('trade_date', models.DateField(db_index=True, verbose_name='Trade Date')),
                ('suspend_type', models.CharField(default='S', max_length=1, verbose_name='Suspend Type')),
                ('suspend_timing', models.CharField(blank=True, max_length=40, null=True, verbose_name='Suspend Timing')),
                ('is_full_day', models.BooleanField(db_index=True, default=False, verbose_name='Is Full Day Suspension')),
                ('source', models.CharField(default='tushare_suspend_d', max_length=50, verbose_name='Source')),
                ('asset', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='suspension_days', to='markets.asset', verbose_name='Asset')),
            ],
            options={
                'verbose_name': 'Asset Suspension',
                'verbose_name_plural': 'Asset Suspensions',
                'ordering': ['-trade_date', 'asset_id'],
                'unique_together': {('asset', 'trade_date')},
            },
        ),
        migrations.AddIndex(
            model_name='exchangetradingcalendar',
            index=models.Index(fields=['exchange_code', 'trade_date'], name='markets_exc_exchang_1d4f01_idx'),
        ),
        migrations.AddIndex(
            model_name='assetsuspension',
            index=models.Index(fields=['asset', 'trade_date'], name='markets_ass_asset_i_8e3e4d_idx'),
        ),
        migrations.AddIndex(
            model_name='assetsuspension',
            index=models.Index(fields=['trade_date', 'is_full_day'], name='markets_ass_trade_d_f467e2_idx'),
        ),
    ]