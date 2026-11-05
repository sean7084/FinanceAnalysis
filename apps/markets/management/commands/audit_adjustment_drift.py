"""Census how much of the stored price history was written under a stale adjustment basis.

``apps.markets.adjustment_basis`` holds the arithmetic; this command is about affording it
for the whole universe.

Two facts shape the design:

* TuShare answers for a whole trading day at once. ``daily(trade_date=D)`` and
  ``adj_factor(trade_date=D)`` each return every listed stock, so one day costs two calls
  however many assets the database holds. Covering a year densely for the entire universe
  costs ~500 calls; walking ~1,900 assets individually would cost two calls *per asset*
  for the same answer.
* Coverage must be dense, so what you trade is the window, never the spacing. A basis
  seam is a step that both sides keep: sample only dates lying on one side of it and
  every row agrees with the reference, so the asset reads as clean. An evenly-spaced
  nine-date preview reported 5% on a universe a dense pass put at 40% -- that was a
  floor, not an estimate.

Every asset is compared on every sampled date it has a row for, so a thin result usually
means its history stops before the window rather than that it is clean.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max
from django.utils import timezone

from apps.core.tushare_client import call_with_retry, pro
from apps.markets.adjustment_basis import audit_asset_basis
from apps.markets.models import Asset, ExchangeTradingCalendar, OHLCV


def downstream_series(series_name):
    """Series that depend on ``series_name``, transitively, in catch-up order.

    Derived from the catch-up registry rather than written out, so the list of what a
    rebase would invalidate cannot quietly disagree with what would actually replay.
    """
    from apps.catchup import registry

    dependents = defaultdict(set)
    for descriptor in registry.SERIES:
        for prerequisite in descriptor.depends_on:
            dependents[prerequisite].add(descriptor.name)
    ordered = []
    pending = [series_name]
    while pending:
        current = pending.pop(0)
        for name in sorted(dependents.get(current, ())):
            if name not in ordered:
                ordered.append(name)
                pending.append(name)
    return sorted(ordered, key=lambda name: registry.BY_NAME[name].priority)


class Command(BaseCommand):
    help = ('Report which assets hold rows written under more than one forward-adjustment '
            'basis, and how large the resulting phantom step is.')

    FIELDNAMES = [
        'ts_code', 'rows_compared', 'rows_skipped', 'reference_factor', 'off_basis_rows',
        'off_basis_share', 'max_deviation_pct', 'first_off_basis_date', 'last_off_basis_date',
        'contiguous_prefix', 'exdiv_dates_in_span', 'verdict',
    ]

    def add_arguments(self, parser):
        parser.add_argument('--start-date', default=None,
                            help='First trading date to sample. Defaults to --window-years back.')
        parser.add_argument('--end-date', default=None,
                            help='Last trading date to sample. Defaults to today.')
        parser.add_argument('--window-years', type=int, default=1,
                            help='How far back to cover when --start-date is not given. Each '
                                 'extra trading day costs two TuShare calls for the whole market.')
        parser.add_argument('--max-dates', type=int, default=0,
                            help='Cover at most this many of the most recent trading dates. '
                                 '0 means every date in the window. Never thins coverage.')
        parser.add_argument('--max-calls', type=int, default=400,
                            help='Hard TuShare call budget. The census stops rather than spending '
                                 'the quota the daily sync needs.')
        parser.add_argument('--include-delisted', action='store_true',
                            help='Also census delisted assets, which still feed PIT backtests.')
        parser.add_argument('--output-dir', default='')
        parser.add_argument('--min-rows', type=int, default=4,
                            help='Assets with fewer comparable rows are reported as unmeasurable.')

    def handle(self, *args, **options):
        token = getattr(settings, 'TUSHARE_TOKEN', None)
        if not token:
            raise CommandError('TUSHARE_TOKEN is not configured.')

        end_date = self._parse_date(options['end_date']) or timezone.localdate()
        start_date = (self._parse_date(options['start_date'])
                      or end_date - timedelta(days=365 * max(1, options['window_years'])))
        if end_date < start_date:
            raise CommandError('end-date must be on or after start-date.')

        last_stored = self._last_stored_by_asset(options['include_delisted'])
        if not last_stored:
            raise CommandError('No OHLCV rows found; nothing to census.')
        calendar = self._open_dates(start_date, end_date)
        if not calendar:
            raise CommandError('No official trading dates in the requested window.')
        self.stdout.write(f'{len(last_stored)} assets with OHLCV rows, '
                          f'{len(calendar)} trading days in {start_date}..{end_date}')

        sample = self._select_sample(calendar, last_stored.values(),
                                     options['max_dates'], options['max_calls'])
        output_dir = Path(options['output_dir'] or self._default_output_dir())
        output_dir.mkdir(parents=True, exist_ok=True)

        provider = self._fetch_dates(sample, token)
        findings, unmeasurable = self._census(last_stored, provider, options['min_rows'])
        self._write_reports(output_dir, findings, unmeasurable, sample, last_stored)
        self._print_summary(findings, unmeasurable, last_stored, sample, output_dir)
        return ''

    def _parse_date(self, value):
        if not value:
            return None
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise CommandError(f'Invalid date: {value}. Expected YYYY-MM-DD.') from exc

    def _default_output_dir(self):
        stamp = timezone.now().strftime('%Y%m%d_%H%M%S')
        return str(Path(settings.BASE_DIR) / 'reports' / f'adj_drift_census_{stamp}')

    def _last_stored_by_asset(self, include_delisted):
        statuses = [Asset.ListingStatus.ACTIVE]
        if include_delisted:
            statuses.append(Asset.ListingStatus.DELISTED)
        return {
            row['asset__ts_code']: row['last_date']
            for row in OHLCV.objects.filter(asset__listing_status__in=statuses)
            .values('asset__ts_code').annotate(last_date=Max('date'))
        }

    def _open_dates(self, start_date, end_date):
        return list(
            ExchangeTradingCalendar.objects.filter(
                is_open=True, trade_date__gte=start_date, trade_date__lte=end_date,
            )
            .values_list('trade_date', flat=True)
            .distinct()
            .order_by('trade_date')
        )

    def _select_sample(self, calendar, anchor_dates, max_dates, max_calls):
        """Every trading date in the window, truncated from the front to fit the budget.

        Density is never traded away, only reach. A basis seam is a step that both sides
        keep: if the sample lands entirely on one side of it, every row agrees with the
        reference and the asset reads as clean.
        """
        budget = max(1, int(max_calls) // 2)
        reach = min(len(calendar), budget, int(max_dates) if max_dates else len(calendar))
        dates = calendar[-max(1, reach):]
        uncovered = sorted(set(anchor_dates) - set(dates))
        if uncovered:
            self.stdout.write(self.style.WARNING(
                f'{len(uncovered)} asset(s) have their last stored date before '
                f'{dates[0]}; their reference basis comes from the newest covered date '
                'they do have.'
            ))
        return dates

    def _fetch_dates(self, sample, token):
        client = pro(token)
        wanted = set(Asset.objects.values_list('ts_code', flat=True))
        raw = defaultdict(dict)
        factors = defaultdict(dict)
        calls = 0
        for position, trade_date in enumerate(sample, start=1):
            tag = trade_date.strftime('%Y%m%d')
            frames = (
                (call_with_retry(lambda tag=tag: client.daily(trade_date=tag),
                                 f'daily(trade_date={tag})', sleep_seconds=0.35), raw, 'close'),
                (call_with_retry(lambda tag=tag: client.adj_factor(trade_date=tag),
                                 f'adj_factor(trade_date={tag})', sleep_seconds=0.35), factors, 'adj_factor'),
            )
            calls += 2
            for frame, bucket, field in frames:
                if frame is None or frame.empty:
                    continue
                for record in frame.itertuples(index=False):
                    ts_code = getattr(record, 'ts_code', None)
                    value = getattr(record, field, None)
                    if ts_code in wanted and value is not None:
                        bucket[ts_code][trade_date] = Decimal(str(value))
            if position % 5 == 0 or position == len(sample):
                self.stdout.write(f'  {position}/{len(sample)} dates fetched ({calls} calls)')
        return {'raw': raw, 'factors': factors, 'calls': calls, 'dates': sample}

    def _census(self, last_stored, provider, min_rows):
        sampled_dates = set()
        for by_date in provider['raw'].values():
            sampled_dates.update(by_date)
        stored = defaultdict(dict)
        for ts_code, trade_date, close in (
            OHLCV.objects.filter(date__in=sampled_dates)
            .values_list('asset__ts_code', 'date', 'close')
        ):
            stored[ts_code][trade_date] = close

        findings = []
        unmeasurable = []
        floor = max(1, int(min_rows))
        for ts_code in sorted(last_stored):
            finding = audit_asset_basis(ts_code, stored.get(ts_code, {}),
                                       provider['raw'].get(ts_code, {}),
                                       provider['factors'].get(ts_code, {}))
            if finding.rows_compared < floor:
                unmeasurable.append(ts_code)
                continue
            findings.append(finding)
        return findings, unmeasurable

    def _write_reports(self, output_dir, findings, unmeasurable, sample, last_stored):
        csv_path = output_dir / 'adjustment_basis_census.csv'
        with csv_path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=self.FIELDNAMES, extrasaction='ignore')
            writer.writeheader()
            for finding in sorted(findings, key=lambda item: item.max_deviation, reverse=True):
                writer.writerow({field: _cell(finding.as_row()[field]) for field in self.FIELDNAMES})

        drifted = [finding for finding in findings if finding.drifted]
        magnitudes = sorted(finding.max_deviation for finding in drifted)
        summary = {
            'sampled_dates': len(sample),
            'assets_in_universe': len(last_stored),
            'assets_measured': len(findings),
            'assets_unmeasurable': len(unmeasurable),
            'assets_drifted': len(drifted),
            'drift_rate': round(len(drifted) / len(findings), 4) if findings else None,
            'phantom_step_pct': {
                'median': _at(magnitudes, 0.5),
                'p90': _at(magnitudes, 0.9),
                'max': round(max(magnitudes) * 100, 4) if magnitudes else None,
            },
            'worst_assets': [
                {'ts_code': finding.ts_code,
                 'phantom_step_pct': round(finding.max_deviation * 100, 4),
                 'off_basis_rows': finding.off_basis_rows,
                 'rows_compared': finding.rows_compared}
                for finding in drifted[:20]
            ],
            'tushare_calls_used': len(sample) * 2,
            'derived_series_to_recompute_on_repair': downstream_series('ohlcv'),
            'method': (
                'Sampled trading dates rather than exhausted them: a basis seam is a step '
                'every older row shares, so a few dates identify it. Naming the exact seam '
                'date needs a dense per-asset pass, which is the same pass that would '
                'rewrite the rows.'
            ),
        }
        with (output_dir / 'adjustment_basis_census_summary.json').open('w', encoding='utf-8') as handle:
            json.dump(summary, handle, indent=2, ensure_ascii=False, sort_keys=True, default=str)

    def _print_summary(self, findings, unmeasurable, last_stored, sample, output_dir):
        drifted = [finding for finding in findings if finding.drifted]
        magnitudes = sorted(finding.max_deviation for finding in drifted)
        self.stdout.write('')
        self.stdout.write(f'sampled          : {len(sample)} trading dates, {len(sample) * 2} calls')
        self.stdout.write(f'universe         : {len(last_stored)} assets')
        self.stdout.write(f'measured         : {len(findings)}  unmeasurable: {len(unmeasurable)}')
        if findings:
            self.stdout.write(f'drifted          : {len(drifted)}/{len(findings)} '
                              f'({len(drifted) / len(findings):.0%})')
        if magnitudes:
            self.stdout.write(f'phantom step     : median {_at(magnitudes, 0.5):.2f}%  '
                              f'p90 {_at(magnitudes, 0.9):.2f}%  max {max(magnitudes) * 100:.2f}%')
            self.stdout.write('worst            : ' + ', '.join(
                f'{finding.ts_code} {finding.max_deviation:.1%}'
                for finding in sorted(drifted, key=lambda item: item.max_deviation, reverse=True)[:6]
            ))
        self.stdout.write(f'report           : {output_dir}')


def _cell(value):
    if value is None:
        return ''
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bool):
        return str(value)
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return value


def _at(values, quantile):
    if not values:
        return 0.0
    return round(values[min(len(values) - 1, int(len(values) * quantile))] * 100, 4)
