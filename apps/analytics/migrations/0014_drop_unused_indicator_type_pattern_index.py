"""Drop the unused ``varchar_pattern_ops`` index on ``indicator_type``.

This index was created outside Django -- it appears in no model and no prior migration --
so it is removed with raw SQL and causes no migration-state drift.

``varchar_pattern_ops`` only serves ``LIKE 'prefix%'`` and regex operators. A repo-wide
search found no ``indicator_type__startswith/contains/regex`` lookup and no raw SQL,
``.extra()`` or ``RawSQL`` anywhere in ``apps/``, so nothing can use it. Its lifetime
``idx_scan`` was 671 against 94,480 for the plain ``indicator_type`` index, which is
retained for the DQ coverage and screener scans.
"""
from django.db import migrations

INDEX = 'analytics_technicalindicator_indicator_type_674d366e_like'


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0013_alter_technicalindicator_asset'),
    ]

    operations = [
        migrations.RunSQL(
            sql=f'DROP INDEX IF EXISTS {INDEX}',
            reverse_sql=(
                f'CREATE INDEX IF NOT EXISTS {INDEX} '
                'ON analytics_technicalindicator (indicator_type varchar_pattern_ops)'
            ),
        ),
    ]