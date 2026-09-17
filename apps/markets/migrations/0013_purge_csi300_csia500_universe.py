"""Purge legacy CSI 300 / CSI A500 universe metadata.

The effective universe switched from the CSI 300 + CSI A500 point-in-time union to
a single-index CSI 500 (000905.SH) universe. This data migration removes the now
obsolete membership snapshots, official benchmark series, and the internal PIT union
benchmark rows for the legacy indices, and strips the legacy ``CSI300`` / ``CSIA500``
tags from assets. CSI 500 membership/benchmark rows are (re)populated by the
``onboard_csi500_universe`` workflow. Raw OHLCV/fundamental/technical rows for
ex-universe assets are intentionally left untouched (non-destructive).
"""

from django.db import migrations, models


LEGACY_INDEX_CODES = ('000300.SH', '000510.CSI', '399300.SZ')
LEGACY_BENCHMARK_CODE = 'CSI300_CSIA500_PIT_UNION'
LEGACY_TAGS = {'CSI300', 'CSIA500'}


def purge_legacy_universe(apps, schema_editor):
    Asset = apps.get_model('markets', 'Asset')
    IndexMembership = apps.get_model('markets', 'IndexMembership')
    BenchmarkIndexDaily = apps.get_model('markets', 'BenchmarkIndexDaily')
    PointInTimeBenchmarkDaily = apps.get_model('markets', 'PointInTimeBenchmarkDaily')

    IndexMembership.objects.filter(index_code__in=LEGACY_INDEX_CODES).delete()
    BenchmarkIndexDaily.objects.filter(index_code__in=LEGACY_INDEX_CODES).delete()
    PointInTimeBenchmarkDaily.objects.filter(benchmark_code=LEGACY_BENCHMARK_CODE).delete()

    for asset in Asset.objects.all().iterator():
        tags = asset.membership_tags or []
        if not isinstance(tags, list):
            continue
        cleaned = [tag for tag in tags if tag not in LEGACY_TAGS]
        if len(cleaned) != len(tags):
            asset.membership_tags = cleaned
            asset.save(update_fields=['membership_tags'])


class Migration(migrations.Migration):

    dependencies = [
        ('markets', '0012_rename_markets_ass_asset_i_8e3e4d_idx_markets_ass_asset_i_efbcb2_idx_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='asset',
            name='membership_tags',
            field=models.JSONField(blank=True, default=list, help_text='Current benchmark/index memberships, e.g. CSI500.', verbose_name='Membership Tags'),
        ),
        migrations.RunPython(purge_legacy_universe, migrations.RunPython.noop),
    ]
