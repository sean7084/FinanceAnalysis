"""Retire the jsonb-keyed uniqueness EARLY, before the param_key backfill finishes.

Reordered from the original create-before-drop plan on measured evidence: a wait-event
profile of the running backfill showed 79% IO/DataFileRead, and per-index attribution put
**65% of physical index reads** (12,595 of 19,361 blocks in 60s) on this one 40 GiB index.
It is removed by this campaign anyway, so maintaining it through 174M updates is pure cost.

Safe to drop early because:
  * no celery beat/worker/runserver is running, so nothing is inserting;
  * the backfill only UPDATEs param_key -- it cannot create rows or duplicates;
  * the hot ``(asset_id, timestamp, indicator_type)`` index is RETAINED here, because it is
    the access path the asset-year chunks use. Dropping it now would turn every chunk into
    a sequential scan. It goes in 0017, after the covering index exists.

Uniqueness is absent between this migration and 0016. That is acceptable only inside the
maintenance window with writers stopped. Rollback is reverse_sql-free (Django recreates the
constraint) but rebuilding a 40 GiB index is slow, which is the price of the reordering.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0014_drop_unused_indicator_type_pattern_index'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='technicalindicator',
            unique_together=set(),
        ),
    ]