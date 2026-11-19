"""Create the covering unique index that replaces the jsonb-keyed uniqueness.

Column order is ``(asset_id, indicator_type, timestamp, param_key)`` -- NOT
``(asset_id, indicator_type, param_key, timestamp)``. The deciding consumer is the DQ
chunk bulk read, which wants *every* variant inside a date window: with param_key ahead of
timestamp the window stops being an index boundary and the scan would cover the whole
16-year history instead of the requested months. In this order DQ reads exactly its
window, the trainer's single-variant read is an index-only scan, and the per-asset descent
filters param_key inside the index rather than fetching the heap.

``INCLUDE (value)`` is what makes index-only scans possible: every column the bulk readers
project is then in the index. It costs roughly 1.5 GiB.

NULLS DISTINCT (the default) on purpose, NOT ``NULLS NOT DISTINCT``: SMA and EMA
legitimately store six variants per (asset, timestamp), and before the backfill every one
of those rows has a NULL key. Treating NULLs as equal would make those legitimate rows
conflict. A test constructing NULL-key rows caught this.

PREREQUISITES -- do not apply before all hold:
  1. ``backfill_indicator_param_key`` completed (remaining_null=0).
  2. Same-timestamp BBANDS/FIB_RET duplicates removed, if the full-table check found any.
     The old payload-keyed uniqueness permitted them; this index will reject them, and
     ``get_or_create`` would raise MultipleObjectsReturned.
  3. The BBANDS/FIB_RET writer lookup fix deployed, so no new duplicate-key rows appear.
"""
from django.db import migrations, models

INDEX = 'ti_session_variant_uniq'


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0015_retire_jsonb_unique_early'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddConstraint(
                    model_name='technicalindicator',
                    constraint=models.UniqueConstraint(
                        fields=('asset', 'indicator_type', 'timestamp', 'param_key'),
                        name=INDEX,
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        f'CREATE UNIQUE INDEX {INDEX} '
                        'ON analytics_technicalindicator '
                        '(asset_id, indicator_type, "timestamp", param_key) '
                        'INCLUDE (value)'
                    ),
                    reverse_sql=f'DROP INDEX IF EXISTS {INDEX}',
                ),
            ],
        ),
    ]