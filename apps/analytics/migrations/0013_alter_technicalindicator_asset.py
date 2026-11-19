"""Drop the redundant standalone ``(asset_id)`` index on TechnicalIndicator.

It is a strict prefix of the hot ``(asset_id, timestamp, indicator_type)`` index, which
serves every asset-only lookup and the FK cascade equally well.

``SeparateDatabaseAndState`` on purpose: a plain ``AlterField(db_index=False)`` on a
ForeignKey makes Django drop and re-add the FK constraint, and re-adding it re-validates
every row -- a full validation of 174M rows under ACCESS EXCLUSIVE. The constraint itself
is unchanged and still enforced; only its redundant supporting index goes away, so the
database side is a bare DROP INDEX (instant) and the state side records db_index=False.
"""
import django.db.models.deletion
from django.db import migrations, models

INDEX = 'analytics_technicalindicator_asset_id_92f7da98'


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0012_alter_technicalindicator_options'),
        ('markets', '0016_remove_ohlcv_markets_ohl_asset_i_b6bce1_idx_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name='technicalindicator',
                    name='asset',
                    field=models.ForeignKey(
                        db_index=False,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='indicators',
                        to='markets.asset',
                        verbose_name='Asset',
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=f'DROP INDEX IF EXISTS {INDEX}',
                    reverse_sql=(
                        f'CREATE INDEX IF NOT EXISTS {INDEX} '
                        'ON analytics_technicalindicator (asset_id)'
                    ),
                ),
            ],
        ),
    ]