from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('markets', '0009_asset_lifecycle_support'),
    ]

    operations = [
        migrations.AddField(
            model_name='exchangetradingcalendar',
            name='is_open',
            field=models.BooleanField(db_index=True, default=True, verbose_name='Is Open'),
        ),
        migrations.RemoveField(
            model_name='exchangetradingcalendar',
            name='previous_trade_date',
        ),
    ]