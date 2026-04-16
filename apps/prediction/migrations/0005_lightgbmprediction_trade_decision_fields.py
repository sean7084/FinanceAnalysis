from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('prediction', '0004_predictionresult_trade_decision_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='lightgbmprediction',
            name='risk_reward_ratio',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=12, null=True, verbose_name='Risk Reward Ratio'),
        ),
        migrations.AddField(
            model_name='lightgbmprediction',
            name='stop_loss_price',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True, verbose_name='Stop Loss Price'),
        ),
        migrations.AddField(
            model_name='lightgbmprediction',
            name='suggested',
            field=models.BooleanField(db_index=True, default=False, verbose_name='Suggested'),
        ),
        migrations.AddField(
            model_name='lightgbmprediction',
            name='target_price',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True, verbose_name='Target Price'),
        ),
        migrations.AddField(
            model_name='lightgbmprediction',
            name='trade_score',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=12, null=True, verbose_name='Trade Score'),
        ),
    ]