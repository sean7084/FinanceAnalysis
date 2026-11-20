"""Reconcile the reconstructed current-ST set against stock_basic names (B2 / B3 gate).

namechange records changes, not states: a stock already ST before its first
record never appears (the blind spot). This command reports both diff directions
with samples; ANY unexplained diff exits non-zero. Card B3 (ST_LAYER_ENABLED)
may flip only after this report is archived with diff=0 or every diff explained.
"""
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.markets.models import Asset, IndexMembership
from apps.markets.price_limits import is_st_name
from apps.markets.st_history import st_asset_ids_as_of


class Command(BaseCommand):
    help = 'Reconcile reconstructed current-ST intervals vs current asset names (ST enablement gate).'

    def add_arguments(self, parser):
        parser.add_argument('--as-of', default='', help='YYYY-MM-DD (default today).')
        parser.add_argument('--report-dir', default='docs/reports/stock')

    def handle(self, *args, **options):
        from pathlib import Path

        as_of = timezone.localdate() if not options['as_of'] else timezone.datetime.fromisoformat(options['as_of']).date()
        universe_ids = set(IndexMembership.objects.values_list('asset_id', flat=True).distinct())
        assets = {a.id: a for a in Asset.objects.filter(id__in=universe_ids)}
        reconstructed = st_asset_ids_as_of(as_of) & universe_ids
        current_st = {aid for aid, a in assets.items() if is_st_name(a.name)}

        missing = sorted(current_st - reconstructed)  # blind-spot candidates
        extra = sorted(reconstructed - current_st)    # interval says ST, name does not

        out_dir = Path(options['report_dir'])
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f'st-reconciliation-{as_of.isoformat()}.md'
        lines = [
            f'# ST reconciliation — as-of {as_of.isoformat()}',
            '',
            f'- Historical-union assets: {len(assets)}',
            f'- Reconstructed current-ST: {len(reconstructed)}',
            f'- Current-name ST (stock_basic): {len(current_st)}',
            f'- Missing (current ST, no interval — blind-spot candidates): {len(missing)}',
            f'- Extra (interval ST, name not ST): {len(extra)}',
            '',
            '## Missing (blind-spot candidates)',
            '',
            '| ts_code | name |',
            '| :--- | :--- |',
        ]
        for aid in missing[:200]:
            a = assets[aid]
            lines.append(f'| {a.ts_code} | {a.name} |')
        lines += ['', '## Extra', '', '| ts_code | name |', '| :--- | :--- |']
        for aid in extra[:200]:
            a = assets[aid]
            lines.append(f'| {a.ts_code} | {a.name} |')
        lines += [
            '',
            '## Enablement rule (plan D5 / card B3)',
            '',
            'ST_LAYER_ENABLED may flip only when this report is archived with diff=0, or every',
            'diff is explained (e.g. renamed away from ST after as-of; blind-spot bounded by the',
            'preregistered two-variant sensitivity |delta mean rank IC| < 0.01).',
        ]
        out_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        self.stdout.write(f'report -> {out_path}')

        if missing or extra:
            raise CommandError(
                f'ST reconciliation diff: {len(missing)} missing, {len(extra)} extra. '
                'Archive explanations before enabling the ST layer (card B3).'
            )
        self.stdout.write(self.style.SUCCESS('ST reconciliation: diff=0.'))