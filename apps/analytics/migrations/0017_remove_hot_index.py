"""Drop the hot ``(asset_id, timestamp, indicator_type)`` index.

Deliberately AFTER the covering index exists (0016) and after the backfill: this index is
the access path the asset-year backfill chunks use, so dropping it earlier would turn every
chunk into a sequential scan.

It is NOT a strict prefix of ``(asset_id, indicator_type, timestamp, param_key)`` -- columns
2 and 3 are swapped. The redundancy argument is that every asset-scoped indicator read also
constrains ``indicator_type`` (the descent, the trainer, the DQ chunk scan, and the indicator
board's ``indicator_type__in``), so with type as column 2 the timestamp window is still an
index boundary. Reads constraining only ``timestamp`` use the retained ``(timestamp)`` index.

Re-measure the indicator board endpoint after this migration: it previously timed out at 25s,
and "index-only mitigates the plan change" is an argument, not a measurement.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0016_technicalindicator_covering_unique'),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='technicalindicator',
            name='analytics_t_asset_i_836ba1_idx',
        ),
    ]