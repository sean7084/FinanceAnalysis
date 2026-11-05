"""Rewrite OHLCV history so every row carries its unadjusted close and factor.

The writer used to store TuShare's forward-adjusted price and nothing else, so an
asset synced across an ex-dividend event ended up with its older rows on one basis
and its newer rows on another. ``raw_close`` and ``adj_factor`` now make that
recoverable, and this command pays down the existing backlog by re-fetching each
asset's whole stored history through the current writer.

Two properties worth knowing before running it:

* It only queues assets that are actually missing a ``raw_close``, so re-running it
  after an interruption costs only the unfinished assets rather than re-fetching the
  ones already done. Keeping up an already-repaired asset is the daily dispatcher's
  job, not this command's.
* Each asset is repaired from its own earliest stored date, not from
  ``HISTORICAL_DATA_FLOOR``. Rows older than the floor would otherwise stay without a
  ``raw_close`` forever, and the all-or-nothing re-base rule in
  ``apps.markets.tasks._rebase_pre_window_bars`` then refuses to re-base that asset on
  every future ex-dividend event -- a one-way degradation that quietly re-creates the
  original defect.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Min, Q
from django.utils import timezone

from apps.markets.models import Asset, OHLCV
from apps.markets.tasks import sync_asset_history


class Command(BaseCommand):
    help = ('Re-fetch OHLCV history so every row stores its unadjusted close and '
            'adjustment factor on one shared basis.')

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group()
        group.add_argument('--synced-universe', action='store_true',
                           help='Repair index-tagged assets, the population the models read. Default.')
        group.add_argument('--all-assets', action='store_true',
                           help='Repair every asset with stored OHLCV, including those outside the sync scope.')
        group.add_argument('--symbols', default='',
                           help='Comma-separated ts_codes, e.g. 600519.SH,000001.SZ.')
        parser.add_argument('--limit', type=int, default=0, help='Stop after this many assets.')
        parser.add_argument('--max-calls', type=int, default=0,
                            help='Stop after this many TuShare calls (two per asset). 0 means no cap.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would be re-fetched without calling the provider.')

    def handle(self, *args, **options):
        assets = self._select_assets(options)
        if not assets:
            raise CommandError(
                'No asset needs repair under this selection: either nothing matched, or '
                'every matched row already carries a raw_close.'
            )

        budget = max(0, int(options['max_calls'] or 0))
        if budget:
            assets = assets[:budget // 2]

        total_legacy = sum(entry['legacy_rows'] for entry in assets)
        self.stdout.write(f'{len(assets)} asset(s), {total_legacy:,} row(s) without raw_close.')
        if options['dry_run']:
            for entry in assets[:20]:
                self.stdout.write(f'  {entry["asset"].ts_code}  {entry["legacy_rows"]:>6} row(s) '
                                  f'from {entry["first_date"]}')
            if len(assets) > 20:
                self.stdout.write(f'  ... and {len(assets) - 20} more')
            return ''

        today = timezone.localdate()
        repaired = failed = calls = 0
        for position, entry in enumerate(assets, start=1):
            asset = entry['asset']
            result = sync_asset_history(
                asset.symbol,
                asset.name,
                asset.market.code,
                False,
                repair_start_date=entry['first_date'].isoformat(),
                repair_end_date=today.isoformat(),
                allow_pre_floor_repair=True,
            )
            calls += 2
            if isinstance(result, str) and result.startswith('Error'):
                failed += 1
                self.stdout.write(self.style.ERROR(f'  {asset.ts_code}: {result}'))
            else:
                repaired += 1
            if position % 25 == 0 or position == len(assets):
                self.stdout.write(f'  {position}/{len(assets)} assets done '
                                  f'({calls} calls, {failed} failed)')

        remaining = OHLCV.objects.filter(raw_close__isnull=True).count()
        self.stdout.write(self.style.SUCCESS(
            f'repaired={repaired} failed={failed} calls={calls}; '
            f'rows still without raw_close across the whole table: {remaining:,}'
        ))
        self.stdout.write(
            'Derived data for these assets (technical indicators, factor scores, model '
            'features, predictions) was computed against the pre-repair prices and now '
            'disagrees with them. Recompute in catch-up dependency order before trusting '
            'any output.'
        )
        return ''

    def _select_assets(self, options):
        tokens = [token.strip().upper() for token in str(options['symbols'] or '').split(',') if token.strip()]
        if tokens:
            assets = Asset.objects.filter(ts_code__in=tokens)
        elif not options['all_assets']:
            assets = Asset.objects.exclude(membership_tags=[])
        else:
            assets = Asset.objects.all()

        # One aggregate does the whole selection: which assets have rows, when each
        # series starts, and how many of its rows still lack a raw_close. Per-asset
        # counts here would be one query per asset for information already in the group.
        stats = {
            row['asset_id']: row
            for row in OHLCV.objects.filter(asset__in=assets)
            .values('asset_id')
            .annotate(
                legacy_rows=Count('id', filter=Q(raw_close__isnull=True)),
                first_date=Min('date'),
            )
        }
        selected = [
            {'asset': asset,
             'legacy_rows': stats[asset.pk]['legacy_rows'],
             'first_date': stats[asset.pk]['first_date']}
            for asset in assets.filter(pk__in=stats).order_by('ts_code')
            # Nothing missing is nothing to do. Skipping fully-repaired assets is what
            # makes "re-run it to resume" cheap rather than merely safe: without it a
            # resume re-fetches every asset already done. Regular syncing of an asset
            # that is already repaired is the daily dispatcher's job, not this one's.
            if stats[asset.pk]['legacy_rows'] > 0
        ]
        if options['limit']:
            selected = selected[:max(0, int(options['limit']))]
        return selected
