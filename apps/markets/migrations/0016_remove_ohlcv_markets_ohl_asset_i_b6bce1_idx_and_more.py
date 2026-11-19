"""Drop two redundant indexes on OHLCV.

``markets_ohl_asset_i_b6bce1_idx`` was a byte-identical duplicate of the unique constraint's
own ``(asset_id, date)`` index -- both were declared, one via ``Meta.indexes`` and one via
``unique_together``. ``markets_ohlcv_asset_id_e6944299`` is the FK's standalone index, a
strict prefix of that same unique index.

The FK part uses ``SeparateDatabaseAndState`` so the constraint is not dropped and re-added:
re-adding would re-validate every row under ACCESS EXCLUSIVE, which is unnecessary when
only the redundant supporting index is being removed.
"""
import django.db.models.deletion
from django.db import migrations, models

FK_INDEX = 'markets_ohlcv_asset_id_e6944299'


class Migration(migrations.Migration):

    dependencies = [
        ('markets', '0015_seed_weekly_suspension_backfill'),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='ohlcv',
            name='markets_ohl_asset_i_b6bce1_idx',
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name='ohlcv',
                    name='asset',
                    field=models.ForeignKey(
                        db_index=False,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='ohlcv_data',
                        to='markets.asset',
                        verbose_name='Asset',
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=f'DROP INDEX IF EXISTS {FK_INDEX}',
                    reverse_sql=(
                        f'CREATE INDEX IF NOT EXISTS {FK_INDEX} ON markets_ohlcv (asset_id)'
                    ),
                ),
            ],
        ),
    ]