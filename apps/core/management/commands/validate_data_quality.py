import csv
import json
from bisect import bisect_right
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from apps.analytics.models import TechnicalIndicator
from apps.factors.models import CapitalFlowSnapshot, FactorScore, FundamentalFactorSnapshot
from apps.macro.models import MacroSnapshot, MarketContext
from apps.markets.models import Asset, OHLCV
from apps.sentiment.models import SentimentScore


GLOBAL_FLOOR_DATE = date(2001, 1, 1)
DEFAULT_TECHNICAL_INDICATORS = ('RS_SCORE',)
FACTOR_SCORE_FIELDS = (
    'pe_percentile_score',
    'pb_percentile_score',
    'roe_trend_score',
    'main_force_flow_score',
    'margin_flow_score',
    'technical_reversal_score',
    'sentiment_score',
    'fundamental_score',
    'capital_flow_score',
    'technical_score',
    'composite_score',
    'bottom_probability_score',
)
FACTOR_NEUTRAL_DEFAULT_FIELDS = (
    'technical_score',
    'technical_reversal_score',
    'fundamental_score',
    'capital_flow_score',
    'sentiment_score',
    'roe_trend_score',
)
FUNDAMENTAL_FIELDS = ('pe', 'pb', 'roe', 'roe_qoq')
CAPITAL_FLOW_FIELDS = ('main_force_net_5d', 'margin_balance_change_5d')
SENTIMENT_FIELDS = ('positive_score', 'neutral_score', 'negative_score', 'sentiment_score')


def _cell(value):
    if value is None:
        return ''
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


class ReportWriter:
    def __init__(self, output_dir, max_detail_rows):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_detail_rows = max(0, int(max_detail_rows or 0))
        self.detail_rows_written = 0
        self.detail_rows_dropped = 0
        self.handles = []
        self.writers = {}
        self._open_writers()

    def _open_writers(self):
        specs = {
            'affected_asset_dates': [
                'issue_type', 'severity', 'table', 'field', 'asset_id', 'asset_symbol',
                'asset_ts_code', 'asset_name', 'date', 'details',
            ],
            'cross_table_gaps': [
                'issue_type', 'severity', 'required_table', 'field', 'asset_id', 'asset_symbol',
                'asset_ts_code', 'asset_name', 'date', 'details',
            ],
            'continuity_gaps': [
                'asset_id', 'asset_symbol', 'asset_ts_code', 'asset_name', 'list_date',
                'expected_start', 'expected_end', 'first_observed_date', 'last_observed_date',
                'expected_count', 'actual_count', 'missing_count', 'missing_pct', 'gap_start',
                'gap_end', 'gap_missing_count',
            ],
        }
        for name, fieldnames in specs.items():
            handle = (self.output_dir / f'{name}.csv').open('w', newline='', encoding='utf-8')
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            self.handles.append(handle)
            self.writers[name] = writer

    def write_detail(self, name, row):
        if self.max_detail_rows and self.detail_rows_written >= self.max_detail_rows:
            self.detail_rows_dropped += 1
            return
        self.writers[name].writerow({key: _cell(value) for key, value in row.items()})
        self.detail_rows_written += 1

    def write_csv(self, name, fieldnames, rows):
        path = self.output_dir / f'{name}.csv'
        with path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            for row in rows:
                writer.writerow({field: _cell(row.get(field)) for field in fieldnames})

    def write_json(self, name, payload):
        path = self.output_dir / f'{name}.json'
        with path.open('w', encoding='utf-8') as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True, default=str)

    def close(self):
        for handle in self.handles:
            handle.close()


class Command(BaseCommand):
    help = 'Validate historical data quality and write actionable reports under reports/ without mutating model data.'

    def add_arguments(self, parser):
        parser.add_argument('--start-date', default=GLOBAL_FLOOR_DATE.isoformat())
        parser.add_argument('--end-date', default=date.today().isoformat())
        parser.add_argument('--symbols', default='', help='Comma-separated symbols or TuShare ts_codes. Default validates active assets.')
        parser.add_argument('--include-delisted', action='store_true')
        parser.add_argument('--output-dir', default='')
        parser.add_argument('--technical-indicators', default=','.join(DEFAULT_TECHNICAL_INDICATORS))
        parser.add_argument('--macro-max-age-days', type=int, default=45)
        parser.add_argument('--max-detail-rows', type=int, default=0, help='0 means write all affected asset/date rows.')
        parser.add_argument('--alert', action='store_true', help='Email a summary when critical data-quality issues are found.')
        parser.add_argument('--alert-recipients', default='', help='Comma-separated alert recipients. Falls back to settings.')
        parser.add_argument('--fail-on-critical', action='store_true')

    def handle(self, *args, **options):
        start_date = max(self._parse_date(options['start_date'], 'start-date'), GLOBAL_FLOOR_DATE)
        end_date = self._parse_date(options['end_date'], 'end-date')
        if end_date < start_date:
            raise CommandError('end-date must be on or after start-date.')

        technical_indicators = tuple(
            item.strip().upper() for item in str(options['technical_indicators'] or '').split(',') if item.strip()
        )
        if not technical_indicators:
            raise CommandError('technical-indicators must include at least one indicator type.')

        output_dir = options['output_dir'] or self._default_output_dir()
        writer = ReportWriter(output_dir, options['max_detail_rows'])
        counters = Counter()
        table_counters = Counter()
        field_counters = Counter()
        reason_counters = Counter()

        try:
            assets = list(self._asset_queryset(options).order_by('ts_code'))
            trading_dates = list(
                OHLCV.objects.filter(date__gte=start_date, date__lte=end_date)
                .values_list('date', flat=True)
                .distinct()
                .order_by('date')
            )
            if not trading_dates:
                raise CommandError('No OHLCV trading dates found in the requested validation range.')

            macro_dates = list(MacroSnapshot.objects.filter(date__lte=end_date).values_list('date', flat=True).order_by('date'))
            contexts = list(
                MarketContext.objects.filter(context_key='current', is_active=True, starts_at__lte=end_date)
                .values('id', 'starts_at', 'ends_at', 'macro_phase')
                .order_by('starts_at', 'id')
            )

            self._validate_macro_dates(
                trading_dates,
                macro_dates,
                contexts,
                options['macro_max_age_days'],
                writer,
                counters,
                table_counters,
                field_counters,
            )

            for asset in assets:
                expected_start = max(start_date, asset.list_date or GLOBAL_FLOOR_DATE)
                expected_dates = [trading_date for trading_date in trading_dates if expected_start <= trading_date <= end_date]
                if not expected_dates:
                    continue

                actual_dates = set(
                    OHLCV.objects.filter(asset=asset, date__gte=expected_start, date__lte=end_date)
                    .values_list('date', flat=True)
                )
                self._validate_continuity(asset, expected_dates, actual_dates, writer, counters, table_counters)
                self._validate_cross_tables(
                    asset,
                    actual_dates,
                    technical_indicators,
                    writer,
                    counters,
                    table_counters,
                    field_counters,
                )

            self._validate_null_fields(assets, start_date, end_date, writer, counters, field_counters, reason_counters)
            self._validate_default_buckets(
                assets,
                start_date,
                end_date,
                technical_indicators,
                writer,
                counters,
                field_counters,
                reason_counters,
            )
            self._write_summary_reports(
                writer,
                counters,
                table_counters,
                field_counters,
                reason_counters,
                assets,
                trading_dates,
                start_date,
                end_date,
                technical_indicators,
                options,
            )
        finally:
            writer.close()

        critical_count = self._critical_count(counters)
        self.stdout.write(self.style.SUCCESS(f'Data quality validation report written to {output_dir}'))
        self.stdout.write(f'critical_issues={critical_count} detail_rows_written={writer.detail_rows_written} detail_rows_dropped={writer.detail_rows_dropped}')

        if options['alert'] and critical_count:
            self._send_alert(options, output_dir, counters, critical_count)

        if options['fail_on_critical'] and critical_count:
            raise CommandError(f'Data quality validation found {critical_count} critical issues. Report: {output_dir}')

    def _parse_date(self, value, label):
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise CommandError(f'Invalid {label}: {value}. Expected YYYY-MM-DD.') from exc

    def _default_output_dir(self):
        stamp = timezone.now().strftime('%Y%m%d_%H%M%S')
        return str(Path(settings.BASE_DIR) / 'reports' / f'data_quality_{stamp}')

    def _asset_queryset(self, options):
        queryset = Asset.objects.all()
        if not options['include_delisted']:
            queryset = queryset.filter(listing_status=Asset.ListingStatus.ACTIVE)
        symbols = [item.strip() for item in str(options['symbols'] or '').split(',') if item.strip()]
        if symbols:
            queryset = queryset.filter(Q(symbol__in=symbols) | Q(ts_code__in=symbols))
        return queryset

    def _validate_macro_dates(self, trading_dates, macro_dates, contexts, macro_max_age_days, writer, counters, table_counters, field_counters):
        for trading_date in trading_dates:
            latest_macro_date = self._latest_date_lte(macro_dates, trading_date)
            if latest_macro_date is None:
                self._record_date_issue(
                    writer, counters, table_counters, field_counters,
                    issue_type='missing_macro_snapshot', severity='critical', table='macro_snapshot',
                    field='', trading_date=trading_date, details='No MacroSnapshot on or before trading date.'
                )
            elif (trading_date - latest_macro_date).days > macro_max_age_days:
                self._record_date_issue(
                    writer, counters, table_counters, field_counters,
                    issue_type='stale_macro_snapshot', severity='critical', table='macro_snapshot',
                    field='date', trading_date=trading_date,
                    details=f'Latest MacroSnapshot is {latest_macro_date}, older than {macro_max_age_days} days.'
                )

            matching_contexts = [
                context for context in contexts
                if context['starts_at'] <= trading_date and (context['ends_at'] is None or context['ends_at'] >= trading_date)
            ]
            if not matching_contexts:
                self._record_date_issue(
                    writer, counters, table_counters, field_counters,
                    issue_type='missing_market_context', severity='critical', table='market_context',
                    field='', trading_date=trading_date, details='No active MarketContext range covers trading date.'
                )
            elif len(matching_contexts) > 1:
                self._record_date_issue(
                    writer, counters, table_counters, field_counters,
                    issue_type='overlapping_market_context', severity='critical', table='market_context',
                    field='', trading_date=trading_date, details=f'{len(matching_contexts)} active ranges cover trading date.'
                )

    def _latest_date_lte(self, sorted_dates, target_date):
        index = bisect_right(sorted_dates, target_date)
        if index == 0:
            return None
        return sorted_dates[index - 1]

    def _validate_continuity(self, asset, expected_dates, actual_dates, writer, counters, table_counters):
        expected_count = len(expected_dates)
        actual_expected_count = len(actual_dates.intersection(expected_dates))
        missing_dates = [trading_date for trading_date in expected_dates if trading_date not in actual_dates]
        if not missing_dates:
            return

        self._increment(counters, 'missing_ohlcv', 'critical', len(missing_dates))
        table_counters[('ohlcv', 'critical', 'missing_ohlcv')] += len(missing_dates)
        first_observed = min(actual_dates) if actual_dates else None
        last_observed = max(actual_dates) if actual_dates else None
        missing_pct = (len(missing_dates) / expected_count) if expected_count else 0
        for gap_start, gap_end, gap_count in self._iter_contiguous_gaps(expected_dates, actual_dates):
            writer.write_detail('continuity_gaps', {
                'asset_id': asset.id,
                'asset_symbol': asset.symbol,
                'asset_ts_code': asset.ts_code,
                'asset_name': asset.name,
                'list_date': asset.list_date,
                'expected_start': expected_dates[0],
                'expected_end': expected_dates[-1],
                'first_observed_date': first_observed,
                'last_observed_date': last_observed,
                'expected_count': expected_count,
                'actual_count': actual_expected_count,
                'missing_count': len(missing_dates),
                'missing_pct': f'{missing_pct:.6f}',
                'gap_start': gap_start,
                'gap_end': gap_end,
                'gap_missing_count': gap_count,
            })
            writer.write_detail('affected_asset_dates', self._asset_issue_row(
                issue_type='missing_ohlcv_range', severity='critical', table='ohlcv', field='',
                asset=asset, trading_date=gap_start, details=f'Missing OHLCV from {gap_start} to {gap_end} ({gap_count} trading dates).'
            ))

    def _iter_contiguous_gaps(self, expected_dates, actual_dates):
        gap_start = None
        previous_missing = None
        count = 0
        for trading_date in expected_dates:
            if trading_date not in actual_dates:
                if gap_start is None:
                    gap_start = trading_date
                previous_missing = trading_date
                count += 1
                continue
            if gap_start is not None:
                yield gap_start, previous_missing, count
                gap_start = None
                previous_missing = None
                count = 0
        if gap_start is not None:
            yield gap_start, previous_missing, count

    def _validate_cross_tables(self, asset, baseline_dates, technical_indicators, writer, counters, table_counters, field_counters):
        if not baseline_dates:
            return
        min_date = min(baseline_dates)
        max_date = max(baseline_dates)

        factor_dates = set(FactorScore.objects.filter(
            asset=asset,
            date__gte=min_date,
            date__lte=max_date,
            mode=FactorScore.FactorMode.COMPOSITE,
        ).values_list('date', flat=True))
        fundamental_dates = set(FundamentalFactorSnapshot.objects.filter(asset=asset, date__gte=min_date, date__lte=max_date).values_list('date', flat=True))
        capital_flow_dates = set(CapitalFlowSnapshot.objects.filter(asset=asset, date__gte=min_date, date__lte=max_date).values_list('date', flat=True))
        sentiment_dates = set(SentimentScore.objects.filter(
            asset=asset,
            date__gte=min_date,
            date__lte=max_date,
            score_type=SentimentScore.ScoreType.ASSET_7D,
        ).values_list('date', flat=True))
        technical_pairs = set(
            TechnicalIndicator.objects.filter(
                asset=asset,
                timestamp__date__gte=min_date,
                timestamp__date__lte=max_date,
                indicator_type__in=technical_indicators,
            ).values_list('timestamp__date', 'indicator_type')
        )

        checks = (
            ('missing_factor_score', 'critical', 'factor_score', '', factor_dates),
            ('missing_fundamental_snapshot', 'warning', 'fundamental_factor_snapshot', '', fundamental_dates),
            ('missing_capital_flow_snapshot', 'warning', 'capital_flow_snapshot', '', capital_flow_dates),
            ('missing_sentiment_score', 'warning', 'sentiment_score', 'ASSET_7D', sentiment_dates),
        )
        for trading_date in sorted(baseline_dates):
            for issue_type, severity, table, field, available_dates in checks:
                if trading_date in available_dates:
                    continue
                self._record_asset_cross_gap(
                    writer, counters, table_counters, field_counters,
                    issue_type=issue_type, severity=severity, table=table, field=field,
                    asset=asset, trading_date=trading_date,
                    details='OHLCV exists for asset/date but required related row is missing.',
                )
            for indicator_type in technical_indicators:
                if (trading_date, indicator_type) in technical_pairs:
                    continue
                self._record_asset_cross_gap(
                    writer, counters, table_counters, field_counters,
                    issue_type='missing_technical_indicator', severity='critical', table='technical_indicator',
                    field=indicator_type, asset=asset, trading_date=trading_date,
                    details='OHLCV exists for asset/date but required TechnicalIndicator is missing.',
                )

    def _validate_null_fields(self, assets, start_date, end_date, writer, counters, field_counters, reason_counters):
        asset_ids = [asset.id for asset in assets]
        self._validate_model_null_fields(
            FundamentalFactorSnapshot.objects.filter(asset_id__in=asset_ids, date__gte=start_date, date__lte=end_date).select_related('asset'),
            'fundamental_factor_snapshot', FUNDAMENTAL_FIELDS, 'warning', writer, counters, field_counters, reason_counters,
        )
        self._validate_model_null_fields(
            CapitalFlowSnapshot.objects.filter(asset_id__in=asset_ids, date__gte=start_date, date__lte=end_date).select_related('asset'),
            'capital_flow_snapshot', CAPITAL_FLOW_FIELDS, 'warning', writer, counters, field_counters, reason_counters,
        )
        self._validate_model_null_fields(
            FactorScore.objects.filter(
                asset_id__in=asset_ids,
                date__gte=start_date,
                date__lte=end_date,
                mode=FactorScore.FactorMode.COMPOSITE,
            ).select_related('asset'),
            'factor_score', FACTOR_SCORE_FIELDS, 'warning', writer, counters, field_counters, reason_counters,
        )
        self._validate_model_null_fields(
            SentimentScore.objects.filter(
                asset_id__in=asset_ids,
                date__gte=start_date,
                date__lte=end_date,
                score_type=SentimentScore.ScoreType.ASSET_7D,
            ).select_related('asset'),
            'sentiment_score', SENTIMENT_FIELDS, 'warning', writer, counters, field_counters, reason_counters,
        )

    def _validate_default_buckets(self, assets, start_date, end_date, technical_indicators, writer, counters, field_counters, reason_counters):
        asset_ids = [asset.id for asset in assets]
        factor_queryset = FactorScore.objects.filter(
            asset_id__in=asset_ids,
            date__gte=start_date,
            date__lte=end_date,
            mode=FactorScore.FactorMode.COMPOSITE,
        ).select_related('asset')
        self._validate_model_value_fields(
            factor_queryset,
            'factor_score',
            FACTOR_NEUTRAL_DEFAULT_FIELDS,
            Decimal('0.5'),
            'neutral_default_value',
            'info',
            writer,
            counters,
            field_counters,
            reason_counters,
            'neutral_default_or_fallback',
        )

        technical_queryset = TechnicalIndicator.objects.filter(
            asset_id__in=asset_ids,
            timestamp__date__gte=start_date,
            timestamp__date__lte=end_date,
            indicator_type__in=technical_indicators,
            value=Decimal('0.5'),
        ).select_related('asset')
        technical_count = technical_queryset.count()
        if technical_count:
            self._increment(counters, 'technical_indicator.neutral_default_value', 'info', technical_count)
            field_counters[('technical_indicator', ','.join(technical_indicators), 'neutral_default_value', 'info')] += technical_count
            reason_counters[('technical_indicator', ','.join(technical_indicators), 'neutral_default_or_fallback', 'info')] += technical_count
            for row in technical_queryset.iterator(chunk_size=1000):
                writer.write_detail('affected_asset_dates', self._asset_issue_row(
                    issue_type='neutral_default_value', severity='info', table='technical_indicator', field=row.indicator_type,
                    asset=row.asset, trading_date=row.timestamp.date(), details='Indicator value equals 0.5 neutral/default bucket.',
                ))

        sentiment_queryset = SentimentScore.objects.filter(
            asset_id__in=asset_ids,
            date__gte=start_date,
            date__lte=end_date,
            score_type=SentimentScore.ScoreType.ASSET_7D,
            sentiment_label=SentimentScore.Label.NEUTRAL,
            sentiment_score=Decimal('0'),
        ).select_related('asset')
        sentiment_count = sentiment_queryset.count()
        if sentiment_count:
            self._increment(counters, 'sentiment_score.neutral_default_value', 'info', sentiment_count)
            field_counters[('sentiment_score', 'sentiment_score', 'neutral_default_value', 'info')] += sentiment_count
            reason_counters[('sentiment_score', 'sentiment_score', 'neutral_sentiment_or_fallback', 'info')] += sentiment_count
            for row in sentiment_queryset.iterator(chunk_size=1000):
                writer.write_detail('affected_asset_dates', self._asset_issue_row(
                    issue_type='neutral_default_value', severity='info', table='sentiment_score', field='sentiment_score',
                    asset=row.asset, trading_date=row.date, details='ASSET_7D sentiment is neutral with score 0.',
                ))

    def _validate_model_null_fields(self, queryset, table, fields, severity, writer, counters, field_counters, reason_counters):
        for field in fields:
            null_queryset = queryset.filter(**{f'{field}__isnull': True})
            count = null_queryset.count()
            if not count:
                continue
            self._increment(counters, f'{table}.{field}.null', severity, count)
            field_counters[(table, field, 'field_null', severity)] += count
            reason_counters[(table, field, self._null_reason(table, field), severity)] += count
            for row in null_queryset.iterator(chunk_size=1000):
                writer.write_detail('affected_asset_dates', self._asset_issue_row(
                    issue_type='field_null', severity=severity, table=table, field=field,
                    asset=row.asset, trading_date=row.date, details=f'{field} is NULL.',
                ))

    def _validate_model_value_fields(self, queryset, table, fields, value, issue_type, severity, writer, counters, field_counters, reason_counters, reason):
        for field in fields:
            value_queryset = queryset.filter(**{field: value})
            count = value_queryset.count()
            if not count:
                continue
            self._increment(counters, f'{table}.{field}.{issue_type}', severity, count)
            field_counters[(table, field, issue_type, severity)] += count
            reason_counters[(table, field, reason, severity)] += count
            for row in value_queryset.iterator(chunk_size=1000):
                writer.write_detail('affected_asset_dates', self._asset_issue_row(
                    issue_type=issue_type, severity=severity, table=table, field=field,
                    asset=row.asset, trading_date=row.date, details=f'{field} equals {value}.',
                ))

    def _null_reason(self, table, field):
        if table == 'fundamental_factor_snapshot' and field in {'roe', 'roe_qoq'}:
            return 'missing_or_not_yet_disclosed_financial_report'
        if table == 'factor_score':
            return 'missing_component_input_or_uncomputed_score'
        return 'field_null'

    def _record_asset_cross_gap(self, writer, counters, table_counters, field_counters, issue_type, severity, table, field, asset, trading_date, details):
        self._increment(counters, issue_type, severity, 1)
        table_counters[(table, severity, issue_type)] += 1
        field_counters[(table, field, issue_type, severity)] += 1
        row = self._asset_issue_row(issue_type, severity, table, field, asset, trading_date, details)
        writer.write_detail('cross_table_gaps', {
            'issue_type': issue_type,
            'severity': severity,
            'required_table': table,
            'field': field,
            'asset_id': asset.id,
            'asset_symbol': asset.symbol,
            'asset_ts_code': asset.ts_code,
            'asset_name': asset.name,
            'date': trading_date,
            'details': details,
        })
        writer.write_detail('affected_asset_dates', row)

    def _record_date_issue(self, writer, counters, table_counters, field_counters, issue_type, severity, table, field, trading_date, details):
        self._increment(counters, issue_type, severity, 1)
        table_counters[(table, severity, issue_type)] += 1
        field_counters[(table, field, issue_type, severity)] += 1
        writer.write_detail('cross_table_gaps', {
            'issue_type': issue_type,
            'severity': severity,
            'required_table': table,
            'field': field,
            'date': trading_date,
            'details': details,
        })

    def _asset_issue_row(self, issue_type, severity, table, field, asset, trading_date, details):
        return {
            'issue_type': issue_type,
            'severity': severity,
            'table': table,
            'field': field,
            'asset_id': asset.id,
            'asset_symbol': asset.symbol,
            'asset_ts_code': asset.ts_code,
            'asset_name': asset.name,
            'date': trading_date,
            'details': details,
        }

    def _increment(self, counters, issue_type, severity, count):
        counters[(issue_type, severity)] += count

    def _critical_count(self, counters):
        return sum(count for (_, severity), count in counters.items() if severity == 'critical')

    def _write_summary_reports(self, writer, counters, table_counters, field_counters, reason_counters, assets, trading_dates, start_date, end_date, technical_indicators, options):
        summary_rows = [
            {'issue_type': issue_type, 'severity': severity, 'count': count}
            for (issue_type, severity), count in sorted(counters.items())
        ]
        writer.write_csv('summary', ['issue_type', 'severity', 'count'], summary_rows)

        table_rows = [
            {'table': table, 'severity': severity, 'issue_type': issue_type, 'count': count}
            for (table, severity, issue_type), count in sorted(table_counters.items())
        ]
        writer.write_csv('missing_by_table', ['table', 'severity', 'issue_type', 'count'], table_rows)

        field_rows = [
            {'table': table, 'field': field, 'issue_type': issue_type, 'severity': severity, 'count': count}
            for (table, field, issue_type, severity), count in sorted(field_counters.items())
        ]
        writer.write_csv('missing_fields', ['table', 'field', 'issue_type', 'severity', 'count'], field_rows)

        reason_rows = [
            {'table': table, 'field': field, 'reason': reason, 'severity': severity, 'count': count}
            for (table, field, reason, severity), count in sorted(reason_counters.items())
        ]
        writer.write_csv('null_reason_buckets', ['table', 'field', 'reason', 'severity', 'count'], reason_rows)

        writer.write_json('metadata', {
            'generated_at': timezone.now().isoformat(),
            'start_date': start_date,
            'end_date': end_date,
            'global_floor_date': GLOBAL_FLOOR_DATE,
            'asset_count': len(assets),
            'trading_date_count': len(trading_dates),
            'technical_indicators': list(technical_indicators),
            'macro_max_age_days': options['macro_max_age_days'],
            'max_detail_rows': options['max_detail_rows'],
            'detail_rows_written': writer.detail_rows_written,
            'detail_rows_dropped': writer.detail_rows_dropped,
            'critical_issues': self._critical_count(counters),
        })

    def _send_alert(self, options, output_dir, counters, critical_count):
        recipients = self._alert_recipients(options)
        if not recipients:
            self.stdout.write(self.style.WARNING('Data quality alert requested, but no recipients were configured.'))
            return

        top_issues = sorted(counters.items(), key=lambda item: item[1], reverse=True)[:8]
        lines = [
            f'Data quality validation found {critical_count} critical issue(s).',
            f'Report: {output_dir}',
            '',
            'Top issue buckets:',
        ]
        for (issue_type, severity), count in top_issues:
            lines.append(f'- {severity} {issue_type}: {count}')

        send_mail(
            subject='FinanceAnalysis data quality validation alert',
            message='\n'.join(lines),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=False,
        )
        self.stdout.write(f'Data quality alert sent to {", ".join(recipients)}')

    def _alert_recipients(self, options):
        explicit = [item.strip() for item in str(options.get('alert_recipients') or '').split(',') if item.strip()]
        if explicit:
            return explicit
        configured = getattr(settings, 'DATA_QUALITY_ALERT_EMAILS', None)
        if configured:
            return list(configured)
        return [email for _, email in getattr(settings, 'ADMINS', []) if email]