import csv
import json
import random
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from time import perf_counter
import time

import pandas as pd
import tushare as ts
from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from apps.analytics.models import TechnicalIndicator
from apps.core.date_floor import get_historical_data_floor
from apps.factors.fundamental_materialization import (
    iter_date_windows,
    materialize_fundamental_snapshot_rows,
    normalize_daily_basic_frame,
    normalize_fina_indicator_frame,
)
from apps.factors.models import (
    AssetMarginDetailSnapshot,
    AssetMoneyFlowSnapshot,
    CapitalFlowSnapshot,
    FactorScore,
    FundamentalFactorSnapshot,
)
from apps.macro.models import MacroSnapshot, MarketContext
from apps.markets.benchmarking import (
    PIT_UNION_BENCHMARK_CODE,
    PIT_UNION_BENCHMARK_NAME,
    pit_membership_coverage_gaps,
    point_in_time_union_asset_ids_by_dates,
    required_pit_index_codes_for_date,
)
from apps.markets.models import (
    Asset,
    AssetSuspension,
    BenchmarkIndexDaily,
    ExchangeTradingCalendar,
    IndexMembership,
    OHLCV,
    PointInTimeBenchmarkDaily,
)
from apps.sentiment.models import SentimentScore


DEFAULT_TECHNICAL_INDICATORS = (
    'ADX',
    'BBANDS',
    'EMA',
    'FIB_RET',
    'MACD',
    'MOM_10D',
    'MOM_20D',
    'MOM_5D',
    'OBV',
    'RSI',
    'RS_SCORE',
    'SMA',
    'STOCH',
)
DEFAULT_CROSS_SECTION_AUDIT_DATES = (
    '2024-09-20',
    '2024-09-23',
    '2025-01-02',
    '2025-12-31',
)
DAILY_COVERAGE_REQUIRED_FAMILIES = (
    'rs_score',
    'factor_score',
    'sentiment_score',
    'fundamental_snapshot',
    'capital_flow_snapshot',
)
DAILY_COVERAGE_FIELDS = (
    'ohlcv',
    'rs_score',
    'factor_score',
    'sentiment_score',
    'fundamental_snapshot',
    'capital_flow_snapshot',
    'pe',
    'pe_ttm',
    'pb',
    'roe',
    'roe_qoq',
    'main_force_net_5d',
    'margin_balance_change_5d',
    'pe_ttm_percentile_score',
    'pb_percentile_score',
    'composite_score',
)
SECTION_ONE_LIMITATIONS = (
    'MacroSnapshot does not yet store per-field release/available dates, so macro as-of validation is still a recency check rather than a publication-date audit.',
    'Any suspend_d-covered date is excluded from OHLCV continuity expectations; only full-day suspension overlaps with OHLCV are surfaced as dedicated lifecycle warnings.',
    'No dedicated stored z-score/quantile cross-sectional feature surface exists yet, so section-one cross-sectional audits cover RS_SCORE and stored factor/composite ranks only.',
    'TechnicalIndicator rows do not expose their full upstream lookback inputs, so validation checks timestamp/date consistency and stored historical completeness for configured indicator types rather than replaying each formula from source inputs.',
)
REPORT_DESCRIPTIONS = {
    'affected_asset_dates.csv': 'Asset-date issue ledger across all validation families with explicit metric labels.',
    'index_membership_history_gaps.csv': 'Required CSI300/CSIA500 membership coverage gaps on PIT trading dates.',
    'index_membership_monthly_blanks.csv': 'Warning months where required CSI300/CSIA500 membership snapshots are completely blank in the required portion of the month.',
    'benchmark_index_daily_gaps.csv': 'Missing official benchmark daily rows on PIT-required trading dates.',
    'pit_benchmark_daily_gaps.csv': 'Missing PIT union benchmark daily rows on PIT-required trading dates.',
    'feature_dependency_gaps.csv': 'Missing dependent feature rows relative to existing OHLCV rows or required trade-date context rows.',
    'ohlcv_continuity_gaps.csv': 'Missing OHLCV on official exchange open days after listing and before delisting, excluding suspend_d-covered dates.',
    'fundamental_snapshot_continuity_gaps.csv': 'Missing or NULL PE/PE_TTM/PB/ROE/ROE_QOQ windows relative to OHLCV-backed baseline dates.',
    'capital_flow_snapshot_continuity_gaps.csv': 'Missing or NULL Main Force Net 5D / Margin Balance Change 5D windows relative to OHLCV-backed baseline dates, including source-derived gap_reason labels.',
    'ohlcv_excused_gaps.csv': 'Missing OHLCV dates excluded from continuity expectations because they fall before list_date, on or after delist_date, or on suspend_d-covered dates.',
    'ohlcv_price_anomalies.csv': 'Per-row OHLCV price and volume anomaly checks.',
    'asset_lifecycle_issues.csv': 'Asset lifecycle checks for list_date, delist_date, exchange calendar coverage, and suspension overlaps.',
    'feature_source_asof_issues.csv': 'Feature-source as-of alignment issues such as future financial announcement references.',
    'fundamental_reconciliation_audit.csv': 'Sampled second-layer audit that recomputes stored fundamental values from upstream TuShare daily_basic and fina_indicator rows.',
    'effective_universe_daily_coverage.csv': 'Daily PIT effective-universe coverage counts by feature family.',
    'cross_section_metric_audit.csv': 'Sample-date cross-sectional audit of participants and distributions against effective_universe(date).',
    'cross_section_metric_participants.csv': 'Participant lists for sampled cross-sectional metrics.',
    'official_trading_calendar.csv': 'Official exchange open-day calendar sourced from trade_cal.',
    'summary.csv': 'Issue counts aggregated by issue_type and severity.',
    'missing_by_table.csv': 'Issue counts aggregated by owning table.',
    'missing_fields.csv': 'Issue counts aggregated by table, field, issue_type, and severity.',
    'null_reason_buckets.csv': 'Null/default buckets grouped by table, field, and inferred reason.',
    'metadata.json': 'Run metadata, limitations, and report descriptions.',
}
INDEX_CODE_LABELS = {
    '000300.SH': 'CSI 300',
    '000510.CSI': 'CSI A500',
}
USABLE_ASSET_CLIFF_DROP_RATIO = 0.2
FACTOR_SCORE_FIELDS = (
    'pe_ttm_percentile_score',
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
FUNDAMENTAL_FIELDS = ('pe', 'pe_ttm', 'pb', 'total_share', 'float_share', 'free_share', 'total_mv', 'circ_mv', 'roe', 'roe_qoq')
FUNDAMENTAL_CONTINUITY_FIELDS = ('pe', 'pe_ttm', 'pb', 'roe', 'roe_qoq')
CAPITAL_FLOW_FIELDS = ('main_force_net_5d', 'margin_balance_change_5d')
FUNDAMENTAL_RECONCILIATION_FINA_LOOKBACK_DAYS = 400
SNAPSHOT_ROW_FIELD = 'snapshot_row'
MACRO_FIELDS = (
    'dxy',
    'cny_usd',
    'cn6m_yield',
    'cn1y_yield',
    'cn3y_yield',
    'cn5y_yield',
    'cn7y_yield',
    'cn10y_yield',
    'cn30y_yield',
    'pmi_manufacturing',
    'pmi_non_manufacturing',
    'cpi_yoy',
    'ppi_yoy',
)
SENTIMENT_FIELDS = ('positive_score', 'neutral_score', 'negative_score', 'sentiment_score')
EXPECTED_FIELD_NULL_ISSUE_TYPE = 'expected_field_null'
SUSPICIOUS_FIELD_NULL_ISSUE_TYPE = 'suspicious_field_null'
FUNDAMENTAL_RECONCILIATION_FIELDNAMES = [
    'metric_family', 'metric_name', 'rule_name', 'report_scope', 'audit_status',
    'asset_id', 'asset_symbol', 'asset_ts_code', 'asset_name', 'date',
    'stored_daily_basic_trade_date', 'recomputed_daily_basic_trade_date',
    'stored_fina_indicator_ann_date', 'recomputed_fina_indicator_ann_date',
    'stored_fina_indicator_end_date', 'recomputed_fina_indicator_end_date',
    'mismatch_fields',
    'stored_pe', 'recomputed_pe',
    'stored_pe_ttm', 'recomputed_pe_ttm',
    'stored_pb', 'recomputed_pb',
    'stored_total_share', 'recomputed_total_share',
    'stored_float_share', 'recomputed_float_share',
    'stored_free_share', 'recomputed_free_share',
    'stored_total_mv', 'recomputed_total_mv',
    'stored_circ_mv', 'recomputed_circ_mv',
    'stored_roe', 'recomputed_roe',
    'stored_roe_qoq', 'recomputed_roe_qoq',
    'details',
]


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
    def __init__(self, output_dir, max_detail_rows, selected_reports=None):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_detail_rows = max(0, int(max_detail_rows or 0))
        self.selected_reports = set(selected_reports or []) or None
        self.detail_rows_written = 0
        self.detail_rows_dropped = 0
        self.handles = []
        self.writers = {}
        self.specs = {}
        self._open_writers()

    def _is_selected(self, name):
        return self.selected_reports is None or name in self.selected_reports

    def _open_writers(self):
        self.specs = {
            'affected_asset_dates': [
                'metric_family', 'metric_name', 'rule_name', 'report_scope',
                'issue_type', 'severity', 'table', 'field', 'asset_id', 'asset_symbol',
                'asset_ts_code', 'asset_name', 'date', 'details',
            ],
            'index_membership_history_gaps': [
                'metric_family', 'metric_name', 'rule_name', 'report_scope',
                'severity', 'index_code', 'index_name', 'expected_start', 'expected_end',
                'expected_trade_dates_count', 'gap_start', 'gap_end', 'gap_missing_count', 'details',
            ],
            'index_membership_monthly_blanks': [
                'metric_family', 'metric_name', 'rule_name', 'report_scope',
                'severity', 'index_code', 'index_name', 'calendar_month', 'first_expected_trade_date',
                'last_expected_trade_date', 'expected_trade_dates_count', 'actual_snapshot_count', 'details',
            ],
            'benchmark_index_daily_gaps': [
                'metric_family', 'metric_name', 'rule_name', 'report_scope',
                'severity', 'index_code', 'index_name', 'expected_start', 'expected_end',
                'expected_trade_dates_count', 'gap_start', 'gap_end', 'gap_missing_count', 'details',
            ],
            'pit_benchmark_daily_gaps': [
                'metric_family', 'metric_name', 'rule_name', 'report_scope',
                'severity', 'benchmark_code', 'benchmark_name', 'expected_start', 'expected_end',
                'expected_trade_dates_count', 'gap_start', 'gap_end', 'gap_missing_count', 'details',
            ],
            'feature_dependency_gaps': [
                'metric_family', 'metric_name', 'rule_name', 'report_scope',
                'issue_type', 'severity', 'required_table', 'field', 'asset_id', 'asset_symbol',
                'asset_ts_code', 'asset_name', 'date', 'details',
            ],
            'ohlcv_continuity_gaps': [
                'metric_family', 'metric_name', 'rule_name', 'report_scope',
                'asset_id', 'asset_symbol', 'asset_ts_code', 'asset_name', 'list_date', 'delist_date',
                'expected_start', 'expected_end', 'first_observed_date', 'last_observed_date',
                'expected_count', 'actual_count', 'missing_count', 'missing_pct', 'gap_start',
                'gap_end', 'gap_missing_count',
            ],
            'fundamental_snapshot_continuity_gaps': [
                'metric_family', 'metric_name', 'rule_name', 'report_scope',
                'table', 'field', 'asset_id', 'asset_symbol', 'asset_ts_code', 'asset_name',
                'list_date', 'delist_date', 'expected_start', 'expected_end', 'first_non_null_date', 'last_non_null_date',
                'expected_count', 'actual_count', 'snapshot_row_count', 'missing_count', 'missing_pct',
                'gap_start', 'gap_end', 'gap_missing_count',
            ],
            'capital_flow_snapshot_continuity_gaps': [
                'metric_family', 'metric_name', 'rule_name', 'report_scope',
                'table', 'field', 'asset_id', 'asset_symbol', 'asset_ts_code', 'asset_name',
                'list_date', 'delist_date', 'expected_start', 'expected_end', 'first_non_null_date', 'last_non_null_date',
                'expected_count', 'actual_count', 'snapshot_row_count', 'missing_count', 'missing_pct',
                'gap_start', 'gap_end', 'gap_missing_count', 'gap_reason',
            ],
            'ohlcv_excused_gaps': [
                'metric_family', 'metric_name', 'rule_name', 'report_scope',
                'asset_id', 'asset_symbol', 'asset_ts_code', 'asset_name', 'list_date', 'delist_date',
                'exclusion_cause', 'window_start', 'window_end', 'excluded_count',
                'full_day_excluded_count', 'timed_excluded_count', 'details',
            ],
            'ohlcv_price_anomalies': [
                'metric_family', 'metric_name', 'rule_name', 'report_scope',
                'issue_type', 'severity', 'asset_id', 'asset_symbol', 'asset_ts_code', 'asset_name',
                'date', 'field', 'open', 'high', 'low', 'close', 'adj_close', 'volume', 'amount', 'details',
            ],
            'asset_lifecycle_issues': [
                'metric_family', 'metric_name', 'rule_name', 'report_scope',
                'issue_type', 'severity', 'asset_id', 'asset_symbol', 'asset_ts_code', 'asset_name',
                'listing_status', 'list_date', 'delist_date', 'field_name', 'first_observed_date', 'last_observed_date', 'details',
            ],
            'feature_source_asof_issues': [
                'metric_family', 'metric_name', 'rule_name', 'report_scope',
                'issue_type', 'severity', 'table', 'asset_id', 'asset_symbol', 'asset_ts_code', 'asset_name',
                'date', 'source_field', 'source_date', 'details',
            ],
            'effective_universe_daily_coverage': [
                'metric_family', 'metric_name', 'rule_name', 'report_scope',
                'date', 'effective_universe_count', 'feature_non_null_count', 'usable_asset_count', 'dropped_asset_count',
                'ohlcv_count', 'rs_score_count', 'factor_score_count', 'sentiment_score_count',
                'fundamental_snapshot_count', 'capital_flow_snapshot_count', 'pe_non_null_count', 'pe_ttm_non_null_count', 'pb_non_null_count',
                'roe_non_null_count', 'roe_qoq_non_null_count', 'main_force_net_5d_non_null_count',
                'margin_balance_change_5d_non_null_count', 'pe_ttm_percentile_score_count', 'pb_percentile_score_count',
                'composite_score_count', 'missing_by_feature', 'coverage_status', 'red_flags',
            ],
            'cross_section_metric_audit': [
                'metric_family', 'metric_name', 'rule_name', 'report_scope',
                'date', 'feature_name', 'effective_universe_count', 'participant_count', 'missing_from_universe_count',
                'unexpected_outside_universe_count', 'min_value', 'p10', 'p25', 'p50', 'p75', 'p90', 'max_value',
                'bucket_0_20', 'bucket_20_40', 'bucket_40_60', 'bucket_60_80', 'bucket_80_100',
                'coverage_status', 'details',
            ],
            'cross_section_metric_participants': [
                'metric_family', 'metric_name', 'rule_name', 'report_scope',
                'date', 'feature_name', 'asset_id', 'asset_symbol', 'asset_ts_code', 'asset_name',
                'in_effective_universe', 'value',
            ],
            'official_trading_calendar': [
                'metric_family', 'metric_name', 'rule_name', 'report_scope',
                'exchange_code', 'trade_date', 'previous_trade_date', 'calendar_gap_days',
            ],
        }
        for name, fieldnames in self.specs.items():
            if not self._is_selected(name):
                continue
            handle = (self.output_dir / f'{name}.csv').open('w', newline='', encoding='utf-8')
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            self.handles.append(handle)
            self.writers[name] = writer

    def fieldnames_for(self, name):
        return self.specs[name]

    def write_detail(self, name, row):
        if name not in self.writers:
            return
        if self.max_detail_rows and self.detail_rows_written >= self.max_detail_rows:
            self.detail_rows_dropped += 1
            return
        self.writers[name].writerow({key: _cell(value) for key, value in row.items()})
        self.detail_rows_written += 1

    def write_csv(self, name, fieldnames, rows):
        if not self._is_selected(name):
            return
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
        parser.add_argument('--start-date', default=get_historical_data_floor().isoformat())
        parser.add_argument('--end-date', default=date.today().isoformat())
        parser.add_argument('--symbols', default='', help='Comma-separated symbols or TuShare ts_codes. Default validates active assets.')
        parser.add_argument('--include-delisted', action='store_true')
        parser.add_argument('--effective-universe-only', action='store_true', help='Restrict validation to assets/dates present in the PIT effective universe during the requested window.')
        parser.add_argument('--output-dir', default='')
        parser.add_argument('--technical-indicators', default=','.join(DEFAULT_TECHNICAL_INDICATORS))
        parser.add_argument('--cross-section-audit-dates', default=','.join(DEFAULT_CROSS_SECTION_AUDIT_DATES), help='Comma-separated trading dates to audit RS_SCORE/factor/composite cross-sectional participants against effective_universe(date).')
        parser.add_argument('--macro-max-age-days', type=int, default=45)
        parser.add_argument('--max-detail-rows', type=int, default=0, help='0 means write all affected asset/date rows.')
        parser.add_argument('--only-report', default='', help='Comma-separated report filenames to write, for example ohlcv_continuity_gaps.csv.')
        parser.add_argument('--fundamental-reconciliation-sample-size', type=int, default=0, help='0 disables second-layer upstream reconciliation. Positive values sample stored FundamentalFactorSnapshot rows and recompute them from TuShare daily_basic/fina_indicator.')
        parser.add_argument('--fundamental-reconciliation-seed', type=int, default=17, help='Deterministic seed for sampled fundamental reconciliation rows when the sample size is positive.')
        parser.add_argument('--alert', action='store_true', help='Email a summary when critical data-quality issues are found.')
        parser.add_argument('--alert-recipients', default='', help='Comma-separated alert recipients. Falls back to settings.')
        parser.add_argument('--fail-on-critical', action='store_true')

    def handle(self, *args, **options):
        run_started_at = timezone.now()
        run_started_perf = perf_counter()

        floor_date = get_historical_data_floor()
        start_date = max(self._parse_date(options['start_date'], 'start-date'), floor_date)
        end_date = self._parse_date(options['end_date'], 'end-date')
        if end_date < start_date:
            raise CommandError('end-date must be on or after start-date.')

        technical_indicators = tuple(dict.fromkeys(
            item.strip().upper() for item in str(options['technical_indicators'] or '').split(',') if item.strip()
        ))
        if not technical_indicators:
            raise CommandError('technical-indicators must include at least one indicator type.')
        default_bucket_technical_indicators = technical_indicators

        output_dir = options['output_dir'] or self._default_output_dir()
        writer = ReportWriter(
            output_dir,
            options['max_detail_rows'],
            selected_reports=self._parse_selected_reports(options.get('only_report')),
        )
        counters = Counter()
        table_counters = Counter()
        field_counters = Counter()
        reason_counters = Counter()

        try:
            calendar_rows, trading_calendar_by_exchange, trading_dates = self._load_official_trading_calendar(start_date, end_date)
            if not trading_dates:
                raise CommandError('No official ExchangeTradingCalendar rows found in the requested validation range.')

            self._write_trading_calendar_report(calendar_rows, writer)
            effective_universe_by_date = point_in_time_union_asset_ids_by_dates(trading_dates)
            cross_section_audit_dates = self._resolve_cross_section_audit_dates(
                options['cross_section_audit_dates'],
                start_date,
                end_date,
            )

            expected_dates_by_asset = None
            if options['effective_universe_only']:
                assets, expected_dates_by_asset = self._effective_universe_assets_and_dates(options, effective_universe_by_date)
            else:
                assets = list(self._asset_queryset(options).order_by('ts_code'))

            trading_calendar_sets_by_exchange = {
                exchange_code: set(dates)
                for exchange_code, dates in trading_calendar_by_exchange.items()
            }
            trading_date_history_by_exchange = self._load_exchange_trading_date_history(
                end_date,
                trading_calendar_by_exchange.keys(),
            )
            suspensions_by_asset = self._suspensions_by_asset(
                [asset.id for asset in assets],
                start_date,
                end_date,
            )

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
            self._validate_macro_null_fields(
                start_date,
                end_date,
                writer,
                counters,
                table_counters,
                field_counters,
                reason_counters,
            )
            self._validate_index_and_benchmark_history(
                trading_dates,
                writer,
                counters,
                table_counters,
                field_counters,
            )

            for asset in assets:
                actual_rows = list(
                    OHLCV.objects.filter(asset=asset, date__gte=start_date, date__lte=end_date)
                    .order_by('date')
                    .values('date', 'open', 'high', 'low', 'close', 'adj_close', 'volume', 'amount')
                )
                actual_dates = {row['date'] for row in actual_rows}
                asset_suspensions = suspensions_by_asset.get(asset.id, {})
                full_day_suspension_dates = {
                    trade_date
                    for trade_date, suspension_row in asset_suspensions.items()
                    if suspension_row['is_full_day']
                }
                self._validate_listing_coverage(asset, actual_rows, full_day_suspension_dates, writer, counters, table_counters, field_counters)

                exchange_calendar_dates = trading_calendar_by_exchange.get(asset.market.code, [])
                if not exchange_calendar_dates:
                    self._record_listing_issue(
                        writer,
                        counters,
                        table_counters,
                        field_counters,
                        issue_type='missing_exchange_trading_calendar',
                        severity='critical',
                        asset=asset,
                        first_observed=actual_rows[0]['date'] if actual_rows else None,
                        last_observed=actual_rows[-1]['date'] if actual_rows else None,
                        details=f'No official trading calendar rows were found for exchange {asset.market.code}.',
                        field='market.code',
                        trading_date=actual_rows[0]['date'] if actual_rows else None,
                    )
                    self._validate_ohlcv_price_anomalies(asset, actual_rows, writer, counters, table_counters, field_counters)
                    continue

                if options['effective_universe_only']:
                    candidate_dates = [
                        trading_date
                        for trading_date in (expected_dates_by_asset or {}).get(asset.id, [])
                        if trading_date in trading_calendar_sets_by_exchange.get(asset.market.code, set())
                    ]
                else:
                    candidate_dates = [trading_date for trading_date in exchange_calendar_dates if start_date <= trading_date <= end_date]

                before_list_dates = set()
                if asset.list_date is not None:
                    before_list_dates = {trading_date for trading_date in candidate_dates if trading_date < asset.list_date}

                on_or_after_delist_dates = set()
                if asset.delist_date is not None:
                    on_or_after_delist_dates = {trading_date for trading_date in candidate_dates if trading_date >= asset.delist_date}

                bounded_candidate_dates = [
                    trading_date
                    for trading_date in candidate_dates
                    if trading_date not in before_list_dates and trading_date not in on_or_after_delist_dates
                ]
                suspension_dates = {trading_date for trading_date in bounded_candidate_dates if trading_date in asset_suspensions}
                self._write_excused_ohlcv_gaps(
                    asset,
                    candidate_dates,
                    actual_dates,
                    before_list_dates,
                    on_or_after_delist_dates,
                    suspension_dates,
                    asset_suspensions,
                    writer,
                )
                expected_dates = [trading_date for trading_date in bounded_candidate_dates if trading_date not in suspension_dates]

                if expected_dates:
                    self._validate_continuity(asset, expected_dates, actual_dates, writer, counters, table_counters)
                self._validate_ohlcv_price_anomalies(asset, actual_rows, writer, counters, table_counters, field_counters)
                baseline_dates = actual_dates.intersection(set(expected_dates))
                self._validate_cross_tables(
                    asset,
                    baseline_dates,
                    actual_dates,
                    technical_indicators,
                    trading_date_history_by_exchange.get(asset.market.code, []),
                    asset_suspensions,
                    writer,
                    counters,
                    table_counters,
                    field_counters,
                )
                self._write_feature_continuity_gaps(asset, baseline_dates, writer)

            self._validate_null_fields(assets, start_date, end_date, writer, counters, field_counters, reason_counters)
            self._audit_fundamental_reconciliation(
                assets,
                start_date,
                end_date,
                floor_date,
                options['fundamental_reconciliation_sample_size'],
                options['fundamental_reconciliation_seed'],
                writer,
                counters,
                table_counters,
                field_counters,
            )
            self._validate_default_buckets(
                assets,
                start_date,
                end_date,
                default_bucket_technical_indicators,
                writer,
                counters,
                field_counters,
                reason_counters,
            )
            self._write_effective_universe_daily_coverage(
                writer,
                start_date,
                end_date,
                trading_dates,
                effective_universe_by_date,
                technical_indicators,
                counters,
                table_counters,
                field_counters,
            )
            self._write_cross_section_audit(
                writer,
                cross_section_audit_dates,
                effective_universe_by_date,
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
                floor_date,
                technical_indicators,
                options,
                run_started_at,
                run_started_perf,
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

    def _parse_selected_reports(self, raw_value):
        tokens = [token.strip() for token in str(raw_value or '').split(',') if token.strip()]
        if not tokens:
            return None

        valid_names = {
            filename[:-4]: filename
            for filename in REPORT_DESCRIPTIONS
            if filename.endswith('.csv')
        }
        normalized = set()
        invalid = []
        for token in tokens:
            base_name = token[:-4] if token.endswith('.csv') else token
            if base_name not in valid_names:
                invalid.append(token)
                continue
            normalized.add(base_name)

        if invalid:
            allowed = ', '.join(sorted(REPORT_DESCRIPTIONS.keys()))
            raise CommandError(
                f'Unsupported only-report value(s): {", ".join(invalid)}. Supported reports: {allowed}'
            )
        return normalized

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

    def _validate_index_and_benchmark_history(self, trading_dates, writer, counters, table_counters, field_counters):
        required_trade_dates_by_code = self._required_index_trade_dates_by_code(trading_dates)
        self._validate_index_membership_history(
            trading_dates,
            required_trade_dates_by_code,
            writer,
            counters,
            table_counters,
            field_counters,
        )
        self._validate_index_membership_monthly_blanks(
            required_trade_dates_by_code,
            writer,
            counters,
            table_counters,
            field_counters,
        )
        self._validate_benchmark_index_daily_history(
            required_trade_dates_by_code,
            writer,
            counters,
            table_counters,
            field_counters,
        )
        self._validate_pit_benchmark_daily_history(
            trading_dates,
            writer,
            counters,
            table_counters,
            field_counters,
        )

    def _latest_date_lte(self, sorted_dates, target_date):
        index = bisect_right(sorted_dates, target_date)
        if index == 0:
            return None
        return sorted_dates[index - 1]

    def _load_exchange_trading_date_history(self, end_date, exchange_codes):
        history_by_exchange = defaultdict(list)
        if not exchange_codes:
            return history_by_exchange
        for exchange_code, trade_date in ExchangeTradingCalendar.objects.filter(
            exchange_code__in=exchange_codes,
            trade_date__lte=end_date,
        ).order_by('exchange_code', 'trade_date').values_list('exchange_code', 'trade_date'):
            history_by_exchange[exchange_code].append(trade_date)
        return history_by_exchange

    def _nth_prior_trading_date(self, ordered_dates, target_date, periods):
        if not ordered_dates:
            return None
        index = bisect_right(ordered_dates, target_date) - 1
        if index < periods:
            return None
        return ordered_dates[index - periods]

    def _should_skip_missing_rs_score(self, asset, trading_date, actual_dates, ordered_trading_dates, asset_suspensions):
        anchor_date = self._nth_prior_trading_date(ordered_trading_dates, trading_date, 20)
        if anchor_date is None:
            return False
        if asset.list_date is not None and anchor_date < asset.list_date:
            return True
        if anchor_date in actual_dates:
            return False
        if anchor_date in asset_suspensions:
            return True
        return False

    def _required_index_trade_dates_by_code(self, trading_dates):
        required_dates_by_code = defaultdict(list)
        for trading_date in trading_dates:
            for index_code in required_pit_index_codes_for_date(trading_date):
                required_dates_by_code[index_code].append(trading_date)
        return {index_code: dates for index_code, dates in required_dates_by_code.items()}

    def _validate_index_membership_history(self, trading_dates, required_trade_dates_by_code, writer, counters, table_counters, field_counters):
        if not required_trade_dates_by_code:
            return

        missing_by_date = pit_membership_coverage_gaps(trading_dates)
        missing_dates_by_code = defaultdict(set)
        for trading_date, missing_codes in missing_by_date.items():
            for index_code in missing_codes:
                missing_dates_by_code[index_code].add(trading_date)

        issue_type = 'missing_index_membership_history'
        severity = 'critical'
        for index_code, required_dates in required_trade_dates_by_code.items():
            missing_dates = sorted(missing_dates_by_code.get(index_code, set()))
            if not missing_dates:
                continue

            self._increment(counters, issue_type, severity, len(missing_dates))
            table_counters[('index_membership', severity, issue_type)] += len(missing_dates)
            field_counters[('index_membership', 'trade_date', issue_type, severity)] += len(missing_dates)

            for gap_start, gap_end, window_dates in self._iter_target_date_windows(required_dates, missing_dates):
                writer.write_detail('index_membership_history_gaps', {
                    **self._metric_columns('membership', index_code, 'pit_membership_history_gap', 'trade_date_window'),
                    'severity': severity,
                    'index_code': index_code,
                    'index_name': INDEX_CODE_LABELS.get(index_code, index_code),
                    'expected_start': required_dates[0],
                    'expected_end': required_dates[-1],
                    'expected_trade_dates_count': len(required_dates),
                    'gap_start': gap_start,
                    'gap_end': gap_end,
                    'gap_missing_count': len(window_dates),
                    'details': 'No IndexMembership snapshot exists on or before these required PIT trading dates.',
                })

    def _validate_index_membership_monthly_blanks(self, required_trade_dates_by_code, writer, counters, table_counters, field_counters):
        if not required_trade_dates_by_code:
            return

        min_required_date = min(required_dates[0] for required_dates in required_trade_dates_by_code.values() if required_dates)
        max_required_date = max(required_dates[-1] for required_dates in required_trade_dates_by_code.values() if required_dates)
        snapshot_dates_by_code = defaultdict(set)
        for index_code, trade_date in IndexMembership.objects.filter(
            index_code__in=required_trade_dates_by_code.keys(),
            trade_date__gte=min_required_date,
            trade_date__lte=max_required_date,
        ).values_list('index_code', 'trade_date').distinct():
            snapshot_dates_by_code[index_code].add(trade_date)

        issue_type = 'blank_index_membership_month'
        severity = 'warning'
        for index_code, required_dates in required_trade_dates_by_code.items():
            for _month_key, month_dates in self._iter_required_months(required_dates):
                first_expected = month_dates[0]
                last_expected = month_dates[-1]
                actual_snapshot_count = sum(
                    1
                    for trade_date in snapshot_dates_by_code.get(index_code, set())
                    if first_expected <= trade_date <= last_expected
                )
                if actual_snapshot_count:
                    continue

                self._increment(counters, issue_type, severity, 1)
                table_counters[('index_membership', severity, issue_type)] += 1
                field_counters[('index_membership', 'calendar_month', issue_type, severity)] += 1

                writer.write_detail('index_membership_monthly_blanks', {
                    **self._metric_columns('membership', index_code, 'monthly_snapshot_blank', 'calendar_month'),
                    'severity': severity,
                    'index_code': index_code,
                    'index_name': INDEX_CODE_LABELS.get(index_code, index_code),
                    'calendar_month': first_expected.strftime('%Y-%m'),
                    'first_expected_trade_date': first_expected,
                    'last_expected_trade_date': last_expected,
                    'expected_trade_dates_count': len(month_dates),
                    'actual_snapshot_count': actual_snapshot_count,
                    'details': 'No raw IndexMembership snapshot dates were found in the required portion of this month.',
                })

    def _validate_benchmark_index_daily_history(self, required_trade_dates_by_code, writer, counters, table_counters, field_counters):
        if not required_trade_dates_by_code:
            return

        min_required_date = min(required_dates[0] for required_dates in required_trade_dates_by_code.values() if required_dates)
        max_required_date = max(required_dates[-1] for required_dates in required_trade_dates_by_code.values() if required_dates)
        actual_dates_by_code = defaultdict(set)
        for index_code, trade_date in BenchmarkIndexDaily.objects.filter(
            index_code__in=required_trade_dates_by_code.keys(),
            trade_date__gte=min_required_date,
            trade_date__lte=max_required_date,
        ).values_list('index_code', 'trade_date'):
            actual_dates_by_code[index_code].add(trade_date)

        issue_type = 'missing_benchmark_index_daily'
        severity = 'warning'
        for index_code, required_dates in required_trade_dates_by_code.items():
            missing_dates = [trading_date for trading_date in required_dates if trading_date not in actual_dates_by_code.get(index_code, set())]
            if not missing_dates:
                continue

            self._increment(counters, issue_type, severity, len(missing_dates))
            table_counters[('benchmark_index_daily', severity, issue_type)] += len(missing_dates)
            field_counters[('benchmark_index_daily', 'trade_date', issue_type, severity)] += len(missing_dates)

            for gap_start, gap_end, window_dates in self._iter_target_date_windows(required_dates, missing_dates):
                writer.write_detail('benchmark_index_daily_gaps', {
                    **self._metric_columns('benchmark', index_code, 'benchmark_index_daily_gap', 'trade_date_window'),
                    'severity': severity,
                    'index_code': index_code,
                    'index_name': INDEX_CODE_LABELS.get(index_code, index_code),
                    'expected_start': required_dates[0],
                    'expected_end': required_dates[-1],
                    'expected_trade_dates_count': len(required_dates),
                    'gap_start': gap_start,
                    'gap_end': gap_end,
                    'gap_missing_count': len(window_dates),
                    'details': 'BenchmarkIndexDaily is missing required PIT trading dates for this index.',
                })

    def _validate_pit_benchmark_daily_history(self, trading_dates, writer, counters, table_counters, field_counters):
        expected_dates = [trading_date for trading_date in trading_dates if required_pit_index_codes_for_date(trading_date)]
        if not expected_dates:
            return

        actual_dates = set(
            PointInTimeBenchmarkDaily.objects.filter(
                benchmark_code=PIT_UNION_BENCHMARK_CODE,
                trade_date__gte=expected_dates[0],
                trade_date__lte=expected_dates[-1],
            ).values_list('trade_date', flat=True)
        )
        missing_dates = [trading_date for trading_date in expected_dates if trading_date not in actual_dates]
        if not missing_dates:
            return

        issue_type = 'missing_pit_benchmark_daily'
        severity = 'warning'
        self._increment(counters, issue_type, severity, len(missing_dates))
        table_counters[('pit_benchmark_daily', severity, issue_type)] += len(missing_dates)
        field_counters[('pit_benchmark_daily', 'trade_date', issue_type, severity)] += len(missing_dates)

        for gap_start, gap_end, window_dates in self._iter_target_date_windows(expected_dates, missing_dates):
            writer.write_detail('pit_benchmark_daily_gaps', {
                **self._metric_columns('benchmark', PIT_UNION_BENCHMARK_CODE, 'pit_union_benchmark_gap', 'trade_date_window'),
                'severity': severity,
                'benchmark_code': PIT_UNION_BENCHMARK_CODE,
                'benchmark_name': PIT_UNION_BENCHMARK_NAME,
                'expected_start': expected_dates[0],
                'expected_end': expected_dates[-1],
                'expected_trade_dates_count': len(expected_dates),
                'gap_start': gap_start,
                'gap_end': gap_end,
                'gap_missing_count': len(window_dates),
                'details': 'PointInTimeBenchmarkDaily is missing required PIT trading dates.',
            })

    def _effective_universe_assets_and_dates(self, options, trading_dates):
        expected_dates_by_asset = defaultdict(list)
        for trading_date, asset_ids in trading_dates.items():
            for asset_id in asset_ids:
                expected_dates_by_asset[asset_id].append(trading_date)

        assets = list(
            self._asset_queryset(options)
            .filter(id__in=expected_dates_by_asset.keys())
            .order_by('ts_code')
        )
        return assets, expected_dates_by_asset

    def _resolve_cross_section_audit_dates(self, raw_value, start_date, end_date):
        resolved = []
        for token in [item.strip() for item in str(raw_value or '').split(',') if item.strip()]:
            try:
                target_date = date.fromisoformat(token)
            except ValueError:
                continue
            if start_date <= target_date <= end_date and target_date not in resolved:
                resolved.append(target_date)
        return sorted(resolved)

    def _iter_required_months(self, ordered_dates):
        current_key = None
        month_dates = []
        for trading_date in ordered_dates:
            month_key = (trading_date.year, trading_date.month)
            if current_key is not None and month_key != current_key and month_dates:
                yield current_key, list(month_dates)
                month_dates = []
            current_key = month_key
            month_dates.append(trading_date)
        if month_dates:
            yield current_key, list(month_dates)

    def _load_official_trading_calendar(self, start_date, end_date):
        calendar_rows = list(
            ExchangeTradingCalendar.objects.filter(trade_date__gte=start_date, trade_date__lte=end_date)
            .values('exchange_code', 'trade_date', 'previous_trade_date')
            .order_by('exchange_code', 'trade_date')
        )
        calendar_by_exchange = defaultdict(list)
        trading_dates = set()
        for row in calendar_rows:
            calendar_by_exchange[row['exchange_code']].append(row['trade_date'])
            trading_dates.add(row['trade_date'])
        return calendar_rows, dict(calendar_by_exchange), sorted(trading_dates)

    def _suspensions_by_asset(self, asset_ids, start_date, end_date):
        suspension_rows = defaultdict(dict)
        if not asset_ids:
            return suspension_rows
        for row in AssetSuspension.objects.filter(
            asset_id__in=asset_ids,
            trade_date__gte=start_date,
            trade_date__lte=end_date,
        ).values('asset_id', 'trade_date', 'suspend_type', 'suspend_timing', 'is_full_day'):
            suspension_rows[row['asset_id']][row['trade_date']] = {
                'suspend_type': row['suspend_type'],
                'suspend_timing': row['suspend_timing'],
                'is_full_day': row['is_full_day'],
            }
        return suspension_rows

    def _write_trading_calendar_report(self, calendar_rows, writer):
        rows = []
        for row in calendar_rows:
            rows.append({
                **self._metric_columns('trading_calendar', 'official_open_day', 'trade_cal_open_day', 'exchange_trade_date'),
                'exchange_code': row['exchange_code'],
                'trade_date': row['trade_date'],
                'previous_trade_date': row['previous_trade_date'],
                'calendar_gap_days': (row['trade_date'] - row['previous_trade_date']).days if row['previous_trade_date'] else '',
            })
        writer.write_csv('official_trading_calendar', writer.fieldnames_for('official_trading_calendar'), rows)

    def _validate_listing_coverage(self, asset, actual_rows, full_day_suspension_dates, writer, counters, table_counters, field_counters):
        first_observed = actual_rows[0]['date'] if actual_rows else None
        last_observed = actual_rows[-1]['date'] if actual_rows else None
        actual_dates = {row['date'] for row in actual_rows}

        if asset.list_date is None:
            self._record_listing_issue(
                writer,
                counters,
                table_counters,
                field_counters,
                issue_type='missing_list_date',
                severity='warning',
                asset=asset,
                first_observed=first_observed,
                last_observed=last_observed,
                details='Asset has no list_date, so listing-age validation is incomplete.',
                field='list_date',
            )

        if asset.listing_status == Asset.ListingStatus.DELISTED and asset.delist_date is None:
            self._record_listing_issue(
                writer,
                counters,
                table_counters,
                field_counters,
                issue_type='missing_delist_date',
                severity='warning',
                asset=asset,
                first_observed=first_observed,
                last_observed=last_observed,
                details='Asset is marked delisted but delist_date is missing.',
                field='delist_date',
                trading_date=last_observed,
            )

        if asset.list_date is not None and first_observed is not None and first_observed < asset.list_date:
            self._record_listing_issue(
                writer,
                counters,
                table_counters,
                field_counters,
                issue_type='pre_listing_ohlcv',
                severity='critical',
                asset=asset,
                first_observed=first_observed,
                last_observed=last_observed,
                details=f'First OHLCV row {first_observed} predates list_date {asset.list_date}.',
                field='list_date',
                trading_date=first_observed,
            )

        if asset.delist_date is not None:
            post_delist_dates = sorted(trading_date for trading_date in actual_dates if trading_date > asset.delist_date)
            if post_delist_dates:
                self._record_listing_issue(
                    writer,
                    counters,
                    table_counters,
                    field_counters,
                    issue_type='post_delist_ohlcv',
                    severity='critical',
                    asset=asset,
                    first_observed=first_observed,
                    last_observed=last_observed,
                    details=(
                        f'OHLCV exists after delist_date {asset.delist_date}: '
                        f'{post_delist_dates[0]}..{post_delist_dates[-1]} ({len(post_delist_dates)} rows).'
                    ),
                    field='delist_date',
                    trading_date=post_delist_dates[0],
                )

        suspension_overlap_dates = sorted(actual_dates.intersection(full_day_suspension_dates or set()))
        if suspension_overlap_dates:
            self._record_listing_issue(
                writer,
                counters,
                table_counters,
                field_counters,
                issue_type='ohlcv_on_full_day_suspension',
                severity='warning',
                asset=asset,
                first_observed=first_observed,
                last_observed=last_observed,
                details=(
                    'OHLCV exists on full-day suspension dates: '
                    f'{suspension_overlap_dates[0]}..{suspension_overlap_dates[-1]} ({len(suspension_overlap_dates)} rows).'
                ),
                field='trade_date',
                trading_date=suspension_overlap_dates[0],
            )

    def _write_excused_ohlcv_gaps(
        self,
        asset,
        candidate_dates,
        actual_dates,
        before_list_dates,
        on_or_after_delist_dates,
        suspension_dates,
        suspension_rows_by_date,
        writer,
    ):
        before_list_missing_dates = set(before_list_dates).difference(actual_dates)
        for gap_start, gap_end, window_dates in self._iter_target_date_windows(candidate_dates, before_list_missing_dates):
            writer.write_detail('ohlcv_excused_gaps', {
                **self._metric_columns('ohlcv', 'daily_bar', 'excused_before_list_date_window', 'asset_window'),
                'asset_id': asset.id,
                'asset_symbol': asset.symbol,
                'asset_ts_code': asset.ts_code,
                'asset_name': asset.name,
                'list_date': asset.list_date,
                'delist_date': asset.delist_date,
                'exclusion_cause': 'before_list_date',
                'window_start': gap_start,
                'window_end': gap_end,
                'excluded_count': len(window_dates),
                'full_day_excluded_count': 0,
                'timed_excluded_count': 0,
                'details': f'Missing OHLCV excluded because these trade dates fall before list_date {asset.list_date}.',
            })

        on_or_after_delist_missing_dates = set(on_or_after_delist_dates).difference(actual_dates)
        for gap_start, gap_end, window_dates in self._iter_target_date_windows(candidate_dates, on_or_after_delist_missing_dates):
            writer.write_detail('ohlcv_excused_gaps', {
                **self._metric_columns('ohlcv', 'daily_bar', 'excused_on_or_after_delist_date_window', 'asset_window'),
                'asset_id': asset.id,
                'asset_symbol': asset.symbol,
                'asset_ts_code': asset.ts_code,
                'asset_name': asset.name,
                'list_date': asset.list_date,
                'delist_date': asset.delist_date,
                'exclusion_cause': 'on_or_after_delist_date',
                'window_start': gap_start,
                'window_end': gap_end,
                'excluded_count': len(window_dates),
                'full_day_excluded_count': 0,
                'timed_excluded_count': 0,
                'details': f'Missing OHLCV excluded because these trade dates fall on or after delist_date {asset.delist_date}.',
            })

        suspension_missing_dates = set(suspension_dates).difference(actual_dates)
        for gap_start, gap_end, window_dates in self._iter_target_date_windows(candidate_dates, suspension_missing_dates):
            full_day_count = sum(1 for trading_date in window_dates if suspension_rows_by_date[trading_date]['is_full_day'])
            timed_count = len(window_dates) - full_day_count
            timing_values = sorted({
                suspension_rows_by_date[trading_date]['suspend_timing']
                for trading_date in window_dates
                if suspension_rows_by_date[trading_date]['suspend_timing']
            })
            details = 'Missing OHLCV excluded because suspend_d covers these trade dates.'
            if timing_values:
                preview = ', '.join(timing_values[:3])
                if len(timing_values) > 3:
                    preview = f'{preview} (+{len(timing_values) - 3} more)'
                details = f'{details} suspend_timing={preview}.'
            writer.write_detail('ohlcv_excused_gaps', {
                **self._metric_columns('ohlcv', 'daily_bar', 'excused_suspension_window', 'asset_window'),
                'asset_id': asset.id,
                'asset_symbol': asset.symbol,
                'asset_ts_code': asset.ts_code,
                'asset_name': asset.name,
                'list_date': asset.list_date,
                'delist_date': asset.delist_date,
                'exclusion_cause': 'suspension',
                'window_start': gap_start,
                'window_end': gap_end,
                'excluded_count': len(window_dates),
                'full_day_excluded_count': full_day_count,
                'timed_excluded_count': timed_count,
                'details': details,
            })

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
            writer.write_detail('ohlcv_continuity_gaps', {
                **self._metric_columns('ohlcv', 'daily_bar', 'continuity_gap', 'asset_window'),
                'asset_id': asset.id,
                'asset_symbol': asset.symbol,
                'asset_ts_code': asset.ts_code,
                'asset_name': asset.name,
                'list_date': asset.list_date,
                'delist_date': asset.delist_date,
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

    def _write_feature_continuity_gaps(self, asset, baseline_dates, writer):
        if not baseline_dates:
            return

        ordered_baseline_dates = sorted(baseline_dates)
        min_date = ordered_baseline_dates[0]
        max_date = ordered_baseline_dates[-1]

        fundamental_rows = list(
            FundamentalFactorSnapshot.objects.filter(asset=asset, date__gte=min_date, date__lte=max_date)
            .values('date', *FUNDAMENTAL_CONTINUITY_FIELDS)
        )
        self._write_feature_family_continuity_gaps(
            report_name='fundamental_snapshot_continuity_gaps',
            table='fundamental_factor_snapshot',
            fields=FUNDAMENTAL_CONTINUITY_FIELDS,
            asset=asset,
            expected_dates=ordered_baseline_dates,
            rows=fundamental_rows,
            writer=writer,
        )

        capital_flow_rows = list(
            CapitalFlowSnapshot.objects.filter(asset=asset, date__gte=min_date, date__lte=max_date)
            .values('date', *CAPITAL_FLOW_FIELDS)
        )
        moneyflow_dates = set(
            AssetMoneyFlowSnapshot.objects.filter(asset=asset, date__gte=min_date, date__lte=max_date)
            .values_list('date', flat=True)
        )
        margin_source_dates = list(
            AssetMarginDetailSnapshot.objects.filter(asset=asset, date__gte=min_date, date__lte=max_date)
            .order_by('date')
            .values_list('date', flat=True)
        )
        margin_history_rows = list(
            AssetMarginDetailSnapshot.objects.filter(asset=asset, date__lte=max_date)
            .order_by('date')
            .values_list('date', 'rzrqye')
        )
        self._write_capital_flow_continuity_gaps(
            report_name='capital_flow_snapshot_continuity_gaps',
            table='capital_flow_snapshot',
            asset=asset,
            expected_dates=ordered_baseline_dates,
            rows=capital_flow_rows,
            moneyflow_dates=moneyflow_dates,
            margin_source_dates=margin_source_dates,
            margin_history_rows=margin_history_rows,
            writer=writer,
        )

    def _write_feature_family_continuity_gaps(self, report_name, table, fields, asset, expected_dates, rows, writer):
        if not expected_dates:
            return

        expected_date_set = set(expected_dates)
        snapshot_row_dates = {row['date'] for row in rows if row['date'] in expected_date_set}
        for field in fields:
            non_null_dates = {
                row['date']
                for row in rows
                if row['date'] in expected_date_set and row.get(field) is not None
            }
            missing_dates = [trading_date for trading_date in expected_dates if trading_date not in non_null_dates]
            if not missing_dates:
                continue

            expected_count = len(expected_dates)
            actual_count = len(non_null_dates)
            missing_pct = (len(missing_dates) / expected_count) if expected_count else 0
            first_non_null_date = min(non_null_dates) if non_null_dates else None
            last_non_null_date = max(non_null_dates) if non_null_dates else None
            for gap_start, gap_end, gap_count in self._iter_contiguous_gaps(expected_dates, non_null_dates):
                writer.write_detail(report_name, {
                    **self._metric_columns('feature', field, 'continuity_gap', 'asset_field_window'),
                    'table': table,
                    'field': field,
                    'asset_id': asset.id,
                    'asset_symbol': asset.symbol,
                    'asset_ts_code': asset.ts_code,
                    'asset_name': asset.name,
                    'list_date': asset.list_date,
                    'delist_date': asset.delist_date,
                    'expected_start': expected_dates[0],
                    'expected_end': expected_dates[-1],
                    'first_non_null_date': first_non_null_date,
                    'last_non_null_date': last_non_null_date,
                    'expected_count': expected_count,
                    'actual_count': actual_count,
                    'snapshot_row_count': len(snapshot_row_dates),
                    'missing_count': len(missing_dates),
                    'missing_pct': f'{missing_pct:.6f}',
                    'gap_start': gap_start,
                    'gap_end': gap_end,
                    'gap_missing_count': gap_count,
                })

    def _write_capital_flow_continuity_gaps(
        self,
        report_name,
        table,
        asset,
        expected_dates,
        rows,
        moneyflow_dates,
        margin_source_dates,
        margin_history_rows,
        writer,
    ):
        if not expected_dates:
            return

        expected_date_set = set(expected_dates)
        snapshot_row_dates = {row['date'] for row in rows if row['date'] in expected_date_set}
        margin_history_index_by_date = {
            trading_date: index
            for index, (trading_date, _rzrqye) in enumerate(margin_history_rows)
        }
        margin_history_values = [rzrqye for _trading_date, rzrqye in margin_history_rows]
        margin_source_date_set = set(margin_source_dates)
        first_margin_source_date = margin_source_dates[0] if margin_source_dates else None
        last_margin_source_date = margin_source_dates[-1] if margin_source_dates else None

        for field in CAPITAL_FLOW_FIELDS:
            non_null_dates = {
                row['date']
                for row in rows
                if row['date'] in expected_date_set and row.get(field) is not None
            }
            missing_dates = [trading_date for trading_date in expected_dates if trading_date not in non_null_dates]
            if not missing_dates:
                continue

            expected_count = len(expected_dates)
            actual_count = len(non_null_dates)
            missing_pct = (len(missing_dates) / expected_count) if expected_count else 0
            first_non_null_date = min(non_null_dates) if non_null_dates else None
            last_non_null_date = max(non_null_dates) if non_null_dates else None
            gap_reasons_by_date = {}
            for trading_date in missing_dates:
                gap_reasons_by_date[trading_date] = self._capital_flow_gap_reason(
                    field=field,
                    trading_date=trading_date,
                    moneyflow_dates=moneyflow_dates,
                    margin_source_dates=margin_source_dates,
                    margin_source_date_set=margin_source_date_set,
                    first_margin_source_date=first_margin_source_date,
                    last_margin_source_date=last_margin_source_date,
                    margin_history_index_by_date=margin_history_index_by_date,
                    margin_history_values=margin_history_values,
                )

            for gap_start, gap_end, gap_count, gap_reason in self._iter_reasoned_contiguous_gaps(expected_dates, gap_reasons_by_date):
                writer.write_detail(report_name, {
                    **self._metric_columns('feature', field, 'continuity_gap', 'asset_field_window'),
                    'table': table,
                    'field': field,
                    'asset_id': asset.id,
                    'asset_symbol': asset.symbol,
                    'asset_ts_code': asset.ts_code,
                    'asset_name': asset.name,
                    'list_date': asset.list_date,
                    'delist_date': asset.delist_date,
                    'expected_start': expected_dates[0],
                    'expected_end': expected_dates[-1],
                    'first_non_null_date': first_non_null_date,
                    'last_non_null_date': last_non_null_date,
                    'expected_count': expected_count,
                    'actual_count': actual_count,
                    'snapshot_row_count': len(snapshot_row_dates),
                    'missing_count': len(missing_dates),
                    'missing_pct': f'{missing_pct:.6f}',
                    'gap_start': gap_start,
                    'gap_end': gap_end,
                    'gap_missing_count': gap_count,
                    'gap_reason': gap_reason,
                })

    def _capital_flow_gap_reason(
        self,
        field,
        trading_date,
        moneyflow_dates,
        margin_source_dates,
        margin_source_date_set,
        first_margin_source_date,
        last_margin_source_date,
        margin_history_index_by_date,
        margin_history_values,
    ):
        if field == 'main_force_net_5d':
            return 'missing_moneyflow_source_row'

        if not margin_source_dates:
            return 'missing_margin_detail_source_row'

        if trading_date in margin_source_date_set:
            row_index = margin_history_index_by_date.get(trading_date)
            if row_index is None or row_index < 5:
                return 'margin_diff_5_warmup_insufficient'

            same_day_balance = margin_history_values[row_index]
            fifth_prior_balance = margin_history_values[row_index - 5]
            if same_day_balance is None or fifth_prior_balance is None:
                return 'missing_margin_detail_source_row'

            return 'missing_margin_detail_source_row'

        if last_margin_source_date is not None and trading_date > last_margin_source_date:
            return 'trailing_margin_source_lag'

        if first_margin_source_date is None or trading_date < first_margin_source_date:
            return 'missing_margin_detail_source_row'

        return 'mid_history_margin_blackout'

    def _iter_reasoned_contiguous_gaps(self, ordered_dates, reasons_by_date):
        previous_index = None
        previous_date = None
        previous_reason = None
        gap_start = None
        gap_end = None
        gap_count = 0

        for index, trading_date in enumerate(ordered_dates):
            reason = reasons_by_date.get(trading_date)
            if reason is None:
                continue

            if (
                gap_start is not None
                and previous_index is not None
                and index == previous_index + 1
                and reason == previous_reason
            ):
                gap_end = trading_date
                gap_count += 1
                previous_index = index
                previous_date = trading_date
                continue

            if gap_start is not None:
                yield gap_start, gap_end, gap_count, previous_reason

            gap_start = trading_date
            gap_end = trading_date
            gap_count = 1
            previous_index = index
            previous_date = trading_date
            previous_reason = reason

        if gap_start is not None:
            yield gap_start, gap_end, gap_count, previous_reason

    def _validate_ohlcv_price_anomalies(self, asset, rows, writer, counters, table_counters, field_counters):
        for row in rows:
            trade_date = row['date']
            open_value = row['open']
            high_value = row['high']
            low_value = row['low']
            close_value = row['close']
            adj_close_value = row['adj_close']
            volume_value = row['volume']

            if high_value is not None and low_value is not None and high_value < low_value:
                self._record_ohlcv_issue(
                    writer, counters, table_counters, field_counters,
                    issue_type='ohlcv_high_below_low', severity='critical', field='high/low',
                    asset=asset, row=row, trading_date=trade_date,
                    details='OHLCV high is below low.',
                )

            for field_name, field_value in (
                ('open', open_value),
                ('high', high_value),
                ('low', low_value),
                ('close', close_value),
                ('adj_close', adj_close_value),
            ):
                if field_value is not None and field_value <= 0:
                    self._record_ohlcv_issue(
                        writer, counters, table_counters, field_counters,
                        issue_type='non_positive_ohlcv_price', severity='critical', field=field_name,
                        asset=asset, row=row, trading_date=trade_date,
                        details=f'OHLCV {field_name} is non-positive.',
                    )

            if high_value is not None and low_value is not None:
                for field_name, field_value in (('open', open_value), ('close', close_value)):
                    if field_value is None:
                        continue
                    if field_value < low_value or field_value > high_value:
                        self._record_ohlcv_issue(
                            writer, counters, table_counters, field_counters,
                            issue_type=f'ohlcv_{field_name}_outside_range', severity='critical', field=field_name,
                            asset=asset, row=row, trading_date=trade_date,
                            details=f'OHLCV {field_name} is outside the [{low_value}, {high_value}] range.',
                        )

            if volume_value is not None and volume_value < 0:
                self._record_ohlcv_issue(
                    writer, counters, table_counters, field_counters,
                    issue_type='negative_volume', severity='critical', field='volume',
                    asset=asset, row=row, trading_date=trade_date,
                    details='OHLCV volume is negative.',
                )

            if volume_value == 0 and high_value is not None and low_value is not None and high_value != low_value:
                self._record_ohlcv_issue(
                    writer, counters, table_counters, field_counters,
                    issue_type='zero_volume_with_price_range', severity='warning', field='volume',
                    asset=asset, row=row, trading_date=trade_date,
                    details='OHLCV volume is zero but the trading range is non-flat.',
                )

            if volume_value == 0 and open_value == high_value == low_value == close_value:
                self._record_ohlcv_issue(
                    writer, counters, table_counters, field_counters,
                    issue_type='suspension_like_flat_zero_volume_day', severity='info', field='volume',
                    asset=asset, row=row, trading_date=trade_date,
                    details='OHLCV row looks like a suspension-style flat day with zero volume.',
                )

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

    def _iter_target_date_windows(self, ordered_dates, target_dates):
        target_dates = set(target_dates)
        window_dates = []
        for trading_date in ordered_dates:
            if trading_date in target_dates:
                window_dates.append(trading_date)
                continue
            if window_dates:
                yield window_dates[0], window_dates[-1], list(window_dates)
                window_dates = []
        if window_dates:
            yield window_dates[0], window_dates[-1], list(window_dates)

    def _validate_cross_tables(self, asset, baseline_dates, actual_dates, technical_indicators, ordered_trading_dates, asset_suspensions, writer, counters, table_counters, field_counters):
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
            (
                'missing_factor_score',
                'critical',
                'factor_score',
                '',
                factor_dates,
                'OHLCV exists for asset/date but required FactorScore row is missing.',
            ),
            (
                'missing_fundamental_snapshot',
                'warning',
                'fundamental_factor_snapshot',
                SNAPSHOT_ROW_FIELD,
                fundamental_dates,
                'OHLCV exists for asset/date but required FundamentalFactorSnapshot row is missing.',
            ),
            (
                'missing_capital_flow_snapshot',
                'warning',
                'capital_flow_snapshot',
                SNAPSHOT_ROW_FIELD,
                capital_flow_dates,
                'OHLCV exists for asset/date but required CapitalFlowSnapshot row is missing.',
            ),
            (
                'missing_sentiment_score',
                'warning',
                'sentiment_score',
                'ASSET_7D',
                sentiment_dates,
                'OHLCV exists for asset/date but required SentimentScore row is missing.',
            ),
        )
        for trading_date in sorted(baseline_dates):
            for issue_type, severity, table, field, available_dates, details in checks:
                if trading_date in available_dates:
                    continue
                self._record_asset_cross_gap(
                    writer, counters, table_counters, field_counters,
                    issue_type=issue_type, severity=severity, table=table, field=field,
                    asset=asset, trading_date=trading_date,
                    details=details,
                )
            for indicator_type in technical_indicators:
                if (trading_date, indicator_type) in technical_pairs:
                    continue
                if indicator_type == 'RS_SCORE' and self._should_skip_missing_rs_score(
                    asset,
                    trading_date,
                    actual_dates,
                    ordered_trading_dates,
                    asset_suspensions,
                ):
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
        self._validate_capital_flow_null_fields(
            asset_ids,
            start_date,
            end_date,
            writer,
            counters,
            field_counters,
            reason_counters,
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

    def _audit_fundamental_reconciliation(self, assets, start_date, end_date, floor_date, sample_size, sample_seed, writer, counters, table_counters, field_counters):
        sample_size = max(0, int(sample_size or 0))
        if sample_size == 0:
            return

        token = getattr(settings, 'TUSHARE_TOKEN', None)
        if not token:
            raise CommandError('TUSHARE_TOKEN is required when fundamental-reconciliation-sample-size is greater than 0.')

        asset_ids = [asset.id for asset in assets]
        numeric_field_filter = Q()
        for field in FUNDAMENTAL_FIELDS:
            numeric_field_filter |= Q(**{f'{field}__isnull': False})

        base_queryset = FundamentalFactorSnapshot.objects.filter(
            asset_id__in=asset_ids,
            date__gte=start_date,
            date__lte=end_date,
        ).filter(numeric_field_filter).select_related('asset').order_by('date', 'asset_id')

        sample_ids = self._reservoir_sample_queryset_ids(base_queryset, sample_size, sample_seed)
        if not sample_ids:
            writer.write_csv('fundamental_reconciliation_audit', FUNDAMENTAL_RECONCILIATION_FIELDNAMES, [])
            return

        sampled_rows = list(base_queryset.filter(pk__in=sample_ids).order_by('date', 'asset_id'))
        sampled_rows_by_asset = defaultdict(list)
        for row in sampled_rows:
            sampled_rows_by_asset[row.asset_id].append(row)

        pro = ts.pro_api(token)
        audit_rows = []
        for sampled_asset_rows in sampled_rows_by_asset.values():
            asset = sampled_asset_rows[0].asset
            sampled_dates = [row.date for row in sampled_asset_rows]
            expected_rows_by_date = {}
            fetch_error = None
            try:
                daily_df = self._fetch_fundamental_reconciliation_daily_basic(pro, asset.ts_code, floor_date, max(sampled_dates))
                fina_df = self._fetch_fundamental_reconciliation_fina_indicator(pro, asset.ts_code, floor_date, max(sampled_dates))
                expected_rows_by_date = {
                    payload['date']: payload
                    for payload in materialize_fundamental_snapshot_rows(sampled_dates, daily_df, fina_df)
                }
            except Exception as exc:
                fetch_error = str(exc)

            for row in sampled_asset_rows:
                stored_metadata = row.metadata or {}
                stored_values = {field: getattr(row, field) for field in FUNDAMENTAL_FIELDS}
                expected_payload = self._normalize_fundamental_audit_payload(expected_rows_by_date.get(row.date))
                mismatch_fields = []
                audit_status = 'matched'
                details = 'All audited fundamental fields match the upstream recomputation.'

                if fetch_error is not None:
                    audit_status = 'fetch_failed'
                    details = f'Upstream reconciliation fetch failed: {fetch_error}'
                    self._increment(counters, 'fundamental_reconciliation_fetch_failed', 'warning', 1)
                    table_counters[('fundamental_factor_snapshot', 'warning', 'fundamental_reconciliation_fetch_failed')] += 1
                    field_counters[('fundamental_factor_snapshot', SNAPSHOT_ROW_FIELD, 'fundamental_reconciliation_fetch_failed', 'warning')] += 1
                    writer.write_detail('affected_asset_dates', self._asset_issue_row(
                        issue_type='fundamental_reconciliation_fetch_failed',
                        severity='warning',
                        table='fundamental_factor_snapshot',
                        field=SNAPSHOT_ROW_FIELD,
                        asset=row.asset,
                        trading_date=row.date,
                        details=details,
                        rule_name='fundamental_upstream_reconciliation',
                    ))
                elif expected_payload is None:
                    audit_status = 'recompute_missing'
                    details = 'No recomputed FundamentalFactorSnapshot row was produced for this sampled date.'
                    self._increment(counters, 'fundamental_reconciliation_missing_recomputed_row', 'warning', 1)
                    table_counters[('fundamental_factor_snapshot', 'warning', 'fundamental_reconciliation_missing_recomputed_row')] += 1
                    field_counters[('fundamental_factor_snapshot', SNAPSHOT_ROW_FIELD, 'fundamental_reconciliation_missing_recomputed_row', 'warning')] += 1
                    writer.write_detail('affected_asset_dates', self._asset_issue_row(
                        issue_type='fundamental_reconciliation_missing_recomputed_row',
                        severity='warning',
                        table='fundamental_factor_snapshot',
                        field=SNAPSHOT_ROW_FIELD,
                        asset=row.asset,
                        trading_date=row.date,
                        details=details,
                        rule_name='fundamental_upstream_reconciliation',
                    ))
                else:
                    for field in FUNDAMENTAL_FIELDS:
                        if stored_values[field] != expected_payload[field]:
                            mismatch_fields.append(field)
                    if mismatch_fields:
                        audit_status = 'mismatch'
                        self._increment(counters, 'fundamental_reconciliation_mismatch', 'warning', len(mismatch_fields))
                        table_counters[('fundamental_factor_snapshot', 'warning', 'fundamental_reconciliation_mismatch')] += len(mismatch_fields)
                        mismatch_detail_parts = []
                        for field in mismatch_fields:
                            field_counters[('fundamental_factor_snapshot', field, 'fundamental_reconciliation_mismatch', 'warning')] += 1
                            mismatch_detail_parts.append(f'{field}: stored={stored_values[field]} recomputed={expected_payload[field]}')
                            writer.write_detail('affected_asset_dates', self._asset_issue_row(
                                issue_type='fundamental_reconciliation_mismatch',
                                severity='warning',
                                table='fundamental_factor_snapshot',
                                field=field,
                                asset=row.asset,
                                trading_date=row.date,
                                details=f'Upstream recomputation mismatch for {field}: stored={stored_values[field]} recomputed={expected_payload[field]}.',
                                rule_name='fundamental_upstream_reconciliation',
                            ))
                        details = '; '.join(mismatch_detail_parts)

                audit_rows.append({
                    **self._metric_columns('feature_source', 'fundamental_snapshot', 'fundamental_upstream_reconciliation', 'sampled_asset_date'),
                    'audit_status': audit_status,
                    'asset_id': row.asset.id,
                    'asset_symbol': row.asset.symbol,
                    'asset_ts_code': row.asset.ts_code,
                    'asset_name': row.asset.name,
                    'date': row.date,
                    'stored_daily_basic_trade_date': stored_metadata.get('daily_basic_trade_date'),
                    'recomputed_daily_basic_trade_date': expected_payload['daily_basic_trade_date'] if expected_payload else None,
                    'stored_fina_indicator_ann_date': stored_metadata.get('fina_indicator_ann_date'),
                    'recomputed_fina_indicator_ann_date': expected_payload['fina_indicator_ann_date'] if expected_payload else None,
                    'stored_fina_indicator_end_date': stored_metadata.get('fina_indicator_end_date'),
                    'recomputed_fina_indicator_end_date': expected_payload['fina_indicator_end_date'] if expected_payload else None,
                    'mismatch_fields': ','.join(mismatch_fields),
                    'stored_pe': row.pe,
                    'recomputed_pe': expected_payload['pe'] if expected_payload else None,
                    'stored_pe_ttm': row.pe_ttm,
                    'recomputed_pe_ttm': expected_payload['pe_ttm'] if expected_payload else None,
                    'stored_pb': row.pb,
                    'recomputed_pb': expected_payload['pb'] if expected_payload else None,
                    'stored_total_share': row.total_share,
                    'recomputed_total_share': expected_payload['total_share'] if expected_payload else None,
                    'stored_float_share': row.float_share,
                    'recomputed_float_share': expected_payload['float_share'] if expected_payload else None,
                    'stored_free_share': row.free_share,
                    'recomputed_free_share': expected_payload['free_share'] if expected_payload else None,
                    'stored_total_mv': row.total_mv,
                    'recomputed_total_mv': expected_payload['total_mv'] if expected_payload else None,
                    'stored_circ_mv': row.circ_mv,
                    'recomputed_circ_mv': expected_payload['circ_mv'] if expected_payload else None,
                    'stored_roe': row.roe,
                    'recomputed_roe': expected_payload['roe'] if expected_payload else None,
                    'stored_roe_qoq': row.roe_qoq,
                    'recomputed_roe_qoq': expected_payload['roe_qoq'] if expected_payload else None,
                    'details': details,
                })

        writer.write_csv('fundamental_reconciliation_audit', FUNDAMENTAL_RECONCILIATION_FIELDNAMES, audit_rows)

    def _normalize_fundamental_audit_payload(self, payload):
        if payload is None:
            return None

        normalized_payload = dict(payload)
        for field in FUNDAMENTAL_FIELDS:
            normalized_payload[field] = self._round_fundamental_audit_value(field, normalized_payload.get(field))
        return normalized_payload

    def _round_fundamental_audit_value(self, field, value):
        if value is None:
            return None

        decimal_places = getattr(FundamentalFactorSnapshot._meta.get_field(field), 'decimal_places', None)
        if decimal_places is None:
            return value

        quantizer = Decimal('1').scaleb(-decimal_places)
        return Decimal(str(value)).quantize(quantizer)

    def _reservoir_sample_queryset_ids(self, queryset, sample_size, sample_seed):
        sample = []
        rng = random.Random(sample_seed)
        for index, pk in enumerate(queryset.values_list('pk', flat=True).iterator(chunk_size=5000), start=1):
            if len(sample) < sample_size:
                sample.append(pk)
                continue
            replacement_index = rng.randint(1, index)
            if replacement_index <= sample_size:
                sample[replacement_index - 1] = pk
        return sample

    def _fetch_fundamental_reconciliation_daily_basic(self, pro, ts_code, start_date, end_date):
        fields = 'trade_date,pe,pe_ttm,pb,total_share,float_share,free_share,total_mv,circ_mv'
        daily_df = self._call_reconciliation_tushare(
            lambda: pro.daily_basic(
                ts_code=ts_code,
                start_date=start_date.strftime('%Y%m%d'),
                end_date=end_date.strftime('%Y%m%d'),
                fields=fields,
            ),
            f'fundamental_reconciliation_daily_basic:{ts_code}:{start_date}:{end_date}',
        )
        return normalize_daily_basic_frame(daily_df)

    def _fetch_fundamental_reconciliation_fina_indicator(self, pro, ts_code, start_date, end_date):
        frames = []
        fetch_start = start_date - timedelta(days=FUNDAMENTAL_RECONCILIATION_FINA_LOOKBACK_DAYS)
        for window_start, window_end in iter_date_windows(fetch_start, end_date):
            frame = self._call_reconciliation_tushare(
                lambda ws=window_start, we=window_end: pro.fina_indicator(
                    ts_code=ts_code,
                    start_date=ws.strftime('%Y%m%d'),
                    end_date=we.strftime('%Y%m%d'),
                    fields='ann_date,end_date,roe',
                ),
                f'fundamental_reconciliation_fina_indicator:{ts_code}:{window_start}:{window_end}',
            )
            if frame is not None and not frame.empty:
                frames.append(frame)
        if not frames:
            return None
        return pd.concat(frames, ignore_index=True)

    def _call_reconciliation_tushare(self, fn, label):
        attempts = 0
        request_sleep_seconds = float(getattr(settings, 'FUNDAMENTAL_BACKFILL_REQUEST_SLEEP_SECONDS', 0.35))
        retry_sleep_seconds = float(getattr(settings, 'FUNDAMENTAL_BACKFILL_RETRY_SLEEP_SECONDS', 65.0))
        while True:
            try:
                result = fn()
                if request_sleep_seconds > 0:
                    time.sleep(request_sleep_seconds)
                return result
            except Exception as exc:
                attempts += 1
                if '频率超限' not in str(exc) or attempts >= 5:
                    raise
                self.stdout.write(
                    self.style.WARNING(
                        f'{label}: TuShare rate limit encountered, sleeping {retry_sleep_seconds:.0f}s before retry {attempts}/5.'
                    )
                )
                time.sleep(retry_sleep_seconds)

    def _validate_capital_flow_null_fields(self, asset_ids, start_date, end_date, writer, counters, field_counters, reason_counters):
        queryset = CapitalFlowSnapshot.objects.filter(
            asset_id__in=asset_ids,
            date__gte=start_date,
            date__lte=end_date,
        ).select_related('asset')
        if not queryset.filter(Q(main_force_net_5d__isnull=True) | Q(margin_balance_change_5d__isnull=True)).exists():
            return

        moneyflow_dates_by_asset = defaultdict(set)
        for asset_id, trading_date in AssetMoneyFlowSnapshot.objects.filter(
            asset_id__in=asset_ids,
            date__gte=start_date,
            date__lte=end_date,
        ).values_list('asset_id', 'date').iterator(chunk_size=5000):
            moneyflow_dates_by_asset[asset_id].add(trading_date)

        margin_history_by_asset = defaultdict(lambda: {'index_by_date': {}, 'rzrqye': []})
        for asset_id, trading_date, rzrqye in AssetMarginDetailSnapshot.objects.filter(
            asset_id__in=asset_ids,
            date__lte=end_date,
        ).order_by('asset_id', 'date').values_list('asset_id', 'date', 'rzrqye').iterator(chunk_size=5000):
            history = margin_history_by_asset[asset_id]
            history['index_by_date'][trading_date] = len(history['rzrqye'])
            history['rzrqye'].append(rzrqye)

        for field in CAPITAL_FLOW_FIELDS:
            null_queryset = queryset.filter(**{f'{field}__isnull': True})
            for row in null_queryset.iterator(chunk_size=1000):
                issue_type, reason, details = self._classify_capital_flow_null(
                    field,
                    row.asset_id,
                    row.date,
                    moneyflow_dates_by_asset,
                    margin_history_by_asset,
                )
                self._increment(counters, f'capital_flow_snapshot.{field}.{issue_type}', 'warning', 1)
                field_counters[('capital_flow_snapshot', field, issue_type, 'warning')] += 1
                reason_counters[('capital_flow_snapshot', field, reason, 'warning')] += 1
                writer.write_detail('affected_asset_dates', self._asset_issue_row(
                    issue_type=issue_type,
                    severity='warning',
                    table='capital_flow_snapshot',
                    field=field,
                    asset=row.asset,
                    trading_date=row.date,
                    details=details,
                ))

    def _classify_capital_flow_null(self, field, asset_id, trading_date, moneyflow_dates_by_asset, margin_history_by_asset):
        if field == 'main_force_net_5d':
            if trading_date not in moneyflow_dates_by_asset.get(asset_id, set()):
                return (
                    EXPECTED_FIELD_NULL_ISSUE_TYPE,
                    'missing_moneyflow_source_row',
                    'main_force_net_5d is NULL because no same-day AssetMoneyFlowSnapshot exists.',
                )
            return (
                SUSPICIOUS_FIELD_NULL_ISSUE_TYPE,
                'unexpected_null_with_moneyflow_source_row',
                'main_force_net_5d is NULL despite a same-day AssetMoneyFlowSnapshot being present.',
            )

        margin_history = margin_history_by_asset.get(asset_id)
        if not margin_history or trading_date not in margin_history['index_by_date']:
            return (
                EXPECTED_FIELD_NULL_ISSUE_TYPE,
                'missing_margin_detail_source_row',
                'margin_balance_change_5d is NULL because no same-day AssetMarginDetailSnapshot exists.',
            )

        row_index = margin_history['index_by_date'][trading_date]
        if row_index < 5:
            return (
                EXPECTED_FIELD_NULL_ISSUE_TYPE,
                'margin_diff_5_warmup_insufficient',
                f'margin_balance_change_5d is NULL because only {row_index + 1} AssetMarginDetailSnapshot observations exist by this date; diff(5) requires at least 6.',
            )

        same_day_balance = margin_history['rzrqye'][row_index]
        if same_day_balance is None:
            return (
                EXPECTED_FIELD_NULL_ISSUE_TYPE,
                'same_day_margin_balance_null',
                'margin_balance_change_5d is NULL because same-day AssetMarginDetailSnapshot.rzrqye is NULL.',
            )

        fifth_prior_balance = margin_history['rzrqye'][row_index - 5]
        if fifth_prior_balance is None:
            return (
                EXPECTED_FIELD_NULL_ISSUE_TYPE,
                'fifth_prior_margin_balance_null',
                'margin_balance_change_5d is NULL because the 5th prior AssetMarginDetailSnapshot.rzrqye is NULL.',
            )

        return (
            SUSPICIOUS_FIELD_NULL_ISSUE_TYPE,
            'unexpected_null_with_margin_source_inputs',
            'margin_balance_change_5d is NULL despite same-day and 5th-prior AssetMarginDetailSnapshot.rzrqye inputs being present.',
        )

    def _validate_macro_null_fields(self, start_date, end_date, writer, counters, table_counters, field_counters, reason_counters):
        earliest_relevant_snapshot_date = (
            MacroSnapshot.objects.filter(date__lte=start_date)
            .order_by('-date')
            .values_list('date', flat=True)
            .first()
        ) or start_date
        queryset = MacroSnapshot.objects.filter(
            date__gte=earliest_relevant_snapshot_date,
            date__lte=end_date,
        ).order_by('date')
        for field in MACRO_FIELDS:
            missing_dates = list(queryset.filter(**{f'{field}__isnull': True}).values_list('date', flat=True))
            if not missing_dates:
                continue
            reason_counters[('macro_snapshot', field, 'field_null', 'warning')] += len(missing_dates)
            for trading_date in missing_dates:
                self._record_date_issue(
                    writer,
                    counters,
                    table_counters,
                    field_counters,
                    issue_type='missing_macro_field',
                    severity='warning',
                    table='macro_snapshot',
                    field=field,
                    trading_date=trading_date,
                    details=f'{field} is NULL on MacroSnapshot {trading_date}.',
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
        metric_name = table if field == SNAPSHOT_ROW_FIELD else field or table
        writer.write_detail('feature_dependency_gaps', {
            **self._metric_columns('feature_dependency', metric_name, issue_type, 'asset_date'),
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
        writer.write_detail('feature_dependency_gaps', {
            **self._metric_columns('feature_dependency', field or table, issue_type, 'trade_date'),
            'issue_type': issue_type,
            'severity': severity,
            'required_table': table,
            'field': field,
            'date': trading_date,
            'details': details,
        })

    def _record_ohlcv_issue(self, writer, counters, table_counters, field_counters, issue_type, severity, field, asset, row, trading_date, details):
        self._increment(counters, issue_type, severity, 1)
        table_counters[('ohlcv', severity, issue_type)] += 1
        field_counters[('ohlcv', field, issue_type, severity)] += 1
        writer.write_detail('ohlcv_price_anomalies', {
            **self._metric_columns('ohlcv', 'daily_bar', issue_type, 'asset_date'),
            'issue_type': issue_type,
            'severity': severity,
            'asset_id': asset.id,
            'asset_symbol': asset.symbol,
            'asset_ts_code': asset.ts_code,
            'asset_name': asset.name,
            'date': trading_date,
            'field': field,
            'open': row.get('open'),
            'high': row.get('high'),
            'low': row.get('low'),
            'close': row.get('close'),
            'adj_close': row.get('adj_close'),
            'volume': row.get('volume'),
            'amount': row.get('amount'),
            'details': details,
        })
        writer.write_detail('affected_asset_dates', self._asset_issue_row(
            issue_type=issue_type,
            severity=severity,
            table='ohlcv',
            field=field,
            asset=asset,
            trading_date=trading_date,
            details=details,
        ))

    def _record_listing_issue(self, writer, counters, table_counters, field_counters, issue_type, severity, asset, first_observed, last_observed, details, field='list_date', trading_date=None):
        self._increment(counters, issue_type, severity, 1)
        table_counters[('asset', severity, issue_type)] += 1
        field_counters[('asset', field, issue_type, severity)] += 1
        writer.write_detail('asset_lifecycle_issues', {
            **self._metric_columns('asset_lifecycle', field or 'asset', issue_type, 'asset_lifecycle'),
            'issue_type': issue_type,
            'severity': severity,
            'asset_id': asset.id,
            'asset_symbol': asset.symbol,
            'asset_ts_code': asset.ts_code,
            'asset_name': asset.name,
            'listing_status': asset.listing_status,
            'list_date': asset.list_date,
            'delist_date': asset.delist_date,
            'field_name': field,
            'first_observed_date': first_observed,
            'last_observed_date': last_observed,
            'details': details,
        })
        writer.write_detail('affected_asset_dates', self._asset_issue_row(
            issue_type=issue_type,
            severity=severity,
            table='asset',
            field=field,
            asset=asset,
            trading_date=trading_date or first_observed or asset.list_date or asset.delist_date,
            details=details,
        ))

    def _record_source_asof_issue(self, writer, counters, table_counters, field_counters, issue_type, severity, table, asset, trading_date, source_field, source_date, details):
        self._increment(counters, issue_type, severity, 1)
        table_counters[(table, severity, issue_type)] += 1
        field_counters[(table, source_field, issue_type, severity)] += 1
        writer.write_detail('feature_source_asof_issues', {
            **self._metric_columns('feature_source_alignment', f'{table}.{source_field}', issue_type, 'asset_date'),
            'issue_type': issue_type,
            'severity': severity,
            'table': table,
            'asset_id': asset.id,
            'asset_symbol': asset.symbol,
            'asset_ts_code': asset.ts_code,
            'asset_name': asset.name,
            'date': trading_date,
            'source_field': source_field,
            'source_date': source_date,
            'details': details,
        })
        writer.write_detail('affected_asset_dates', self._asset_issue_row(
            issue_type=issue_type,
            severity=severity,
            table=table,
            field=source_field,
            asset=asset,
            trading_date=trading_date,
            details=details,
        ))

    def _build_effective_universe_bitmaps(self, effective_universe_by_date):
        asset_ids = sorted({asset_id for asset_ids in effective_universe_by_date.values() for asset_id in asset_ids})
        bit_positions = {asset_id: index for index, asset_id in enumerate(asset_ids)}
        bitmaps = {}
        for trading_date, asset_ids_for_date in effective_universe_by_date.items():
            mask = 0
            for asset_id in asset_ids_for_date:
                mask |= 1 << bit_positions[asset_id]
            bitmaps[trading_date] = mask
        return asset_ids, bit_positions, bitmaps

    def _set_feature_presence(self, feature_bitmaps, feature_name, trading_date, asset_id, bit_positions):
        position = bit_positions.get(asset_id)
        if position is None:
            return
        feature_bitmaps[feature_name][trading_date] |= 1 << position

    def _percentile_from_sorted(self, values, ratio):
        if not values:
            return ''
        index = int(round((len(values) - 1) * ratio))
        return values[index]

    def _distribution_buckets(self, values):
        buckets = {
            'bucket_0_20': 0,
            'bucket_20_40': 0,
            'bucket_40_60': 0,
            'bucket_60_80': 0,
            'bucket_80_100': 0,
        }
        for value in values:
            if value <= 0.2:
                buckets['bucket_0_20'] += 1
            elif value <= 0.4:
                buckets['bucket_20_40'] += 1
            elif value <= 0.6:
                buckets['bucket_40_60'] += 1
            elif value <= 0.8:
                buckets['bucket_60_80'] += 1
            else:
                buckets['bucket_80_100'] += 1
        return buckets

    def _write_effective_universe_daily_coverage(self, writer, start_date, end_date, trading_dates, effective_universe_by_date, technical_indicators, counters, table_counters, field_counters):
        union_asset_ids, bit_positions, membership_bitmaps = self._build_effective_universe_bitmaps(effective_universe_by_date)
        feature_bitmaps = {field: defaultdict(int) for field in DAILY_COVERAGE_FIELDS}
        if not union_asset_ids:
            writer.write_csv('effective_universe_daily_coverage', writer.fieldnames_for('effective_universe_daily_coverage'), [])
            return

        asset_map = Asset.objects.in_bulk(union_asset_ids)

        for row_date, asset_id in OHLCV.objects.filter(
            asset_id__in=union_asset_ids,
            date__gte=start_date,
            date__lte=end_date,
        ).values_list('date', 'asset_id').iterator(chunk_size=50000):
            if asset_id not in effective_universe_by_date.get(row_date, set()):
                continue
            self._set_feature_presence(feature_bitmaps, 'ohlcv', row_date, asset_id, bit_positions)

        for row in TechnicalIndicator.objects.filter(
            asset_id__in=union_asset_ids,
            timestamp__date__gte=start_date,
            timestamp__date__lte=end_date,
            indicator_type__in=technical_indicators,
        ).values('timestamp__date', 'asset_id', 'indicator_type').iterator(chunk_size=50000):
            row_date = row['timestamp__date']
            asset_id = row['asset_id']
            if asset_id not in effective_universe_by_date.get(row_date, set()):
                continue
            if row['indicator_type'] == 'RS_SCORE':
                self._set_feature_presence(feature_bitmaps, 'rs_score', row_date, asset_id, bit_positions)

        for row in SentimentScore.objects.filter(
            asset_id__in=union_asset_ids,
            date__gte=start_date,
            date__lte=end_date,
            score_type=SentimentScore.ScoreType.ASSET_7D,
        ).values('date', 'asset_id').iterator(chunk_size=50000):
            row_date = row['date']
            asset_id = row['asset_id']
            if asset_id not in effective_universe_by_date.get(row_date, set()):
                continue
            self._set_feature_presence(feature_bitmaps, 'sentiment_score', row_date, asset_id, bit_positions)

        for row in FundamentalFactorSnapshot.objects.filter(
            asset_id__in=union_asset_ids,
            date__gte=start_date,
            date__lte=end_date,
        ).select_related('asset').values('date', 'asset_id', 'pe', 'pe_ttm', 'pb', 'roe', 'roe_qoq', 'metadata').iterator(chunk_size=50000):
            row_date = row['date']
            asset_id = row['asset_id']
            if asset_id not in effective_universe_by_date.get(row_date, set()):
                continue

            self._set_feature_presence(feature_bitmaps, 'fundamental_snapshot', row_date, asset_id, bit_positions)
            for field in ('pe', 'pe_ttm', 'pb', 'roe', 'roe_qoq'):
                if row.get(field) is not None:
                    self._set_feature_presence(feature_bitmaps, field, row_date, asset_id, bit_positions)

            metadata = row.get('metadata') or {}
            asset = asset_map.get(asset_id)
            ann_date_raw = metadata.get('fina_indicator_ann_date')
            if ann_date_raw:
                ann_date = date.fromisoformat(str(ann_date_raw))
                if row_date < ann_date and asset is not None:
                    self._record_source_asof_issue(
                        writer,
                        counters,
                        table_counters,
                        field_counters,
                        issue_type='future_financial_announcement_reference',
                        severity='critical',
                        table='fundamental_factor_snapshot',
                        asset=asset,
                        trading_date=row_date,
                        source_field='fina_indicator_ann_date',
                        source_date=ann_date,
                        details=f'Fundamental snapshot on {row_date} references announcement date {ann_date}.',
                    )
            report_end_raw = metadata.get('fina_indicator_end_date')
            if report_end_raw:
                report_end_date = date.fromisoformat(str(report_end_raw))
                if row_date < report_end_date and asset is not None:
                    self._record_source_asof_issue(
                        writer,
                        counters,
                        table_counters,
                        field_counters,
                        issue_type='future_financial_report_reference',
                        severity='critical',
                        table='fundamental_factor_snapshot',
                        asset=asset,
                        trading_date=row_date,
                        source_field='fina_indicator_end_date',
                        source_date=report_end_date,
                        details=f'Fundamental snapshot on {row_date} references report end date {report_end_date} in the future.',
                    )

        for row in CapitalFlowSnapshot.objects.filter(
            asset_id__in=union_asset_ids,
            date__gte=start_date,
            date__lte=end_date,
        ).values('date', 'asset_id', 'main_force_net_5d', 'margin_balance_change_5d').iterator(chunk_size=50000):
            row_date = row['date']
            asset_id = row['asset_id']
            if asset_id not in effective_universe_by_date.get(row_date, set()):
                continue

            self._set_feature_presence(feature_bitmaps, 'capital_flow_snapshot', row_date, asset_id, bit_positions)
            if row.get('main_force_net_5d') is not None:
                self._set_feature_presence(feature_bitmaps, 'main_force_net_5d', row_date, asset_id, bit_positions)
            if row.get('margin_balance_change_5d') is not None:
                self._set_feature_presence(feature_bitmaps, 'margin_balance_change_5d', row_date, asset_id, bit_positions)

        for row in FactorScore.objects.filter(
            asset_id__in=union_asset_ids,
            date__gte=start_date,
            date__lte=end_date,
            mode=FactorScore.FactorMode.COMPOSITE,
        ).values('date', 'asset_id', 'pe_ttm_percentile_score', 'pb_percentile_score', 'composite_score').iterator(chunk_size=50000):
            row_date = row['date']
            asset_id = row['asset_id']
            if asset_id not in effective_universe_by_date.get(row_date, set()):
                continue

            self._set_feature_presence(feature_bitmaps, 'factor_score', row_date, asset_id, bit_positions)
            if row.get('pe_ttm_percentile_score') is not None:
                self._set_feature_presence(feature_bitmaps, 'pe_ttm_percentile_score', row_date, asset_id, bit_positions)
            if row.get('pb_percentile_score') is not None:
                self._set_feature_presence(feature_bitmaps, 'pb_percentile_score', row_date, asset_id, bit_positions)
            if row.get('composite_score') is not None:
                self._set_feature_presence(feature_bitmaps, 'composite_score', row_date, asset_id, bit_positions)

        previous_usable_count = None
        rows = []
        for trading_date in trading_dates:
            membership_mask = membership_bitmaps.get(trading_date, 0)
            effective_count = membership_mask.bit_count()
            if effective_count == 0:
                continue

            feature_non_null_mask = membership_mask
            for family in DAILY_COVERAGE_REQUIRED_FAMILIES:
                feature_non_null_mask &= feature_bitmaps[family].get(trading_date, 0)
            feature_non_null_count = feature_non_null_mask.bit_count()

            usable_mask = feature_non_null_mask & feature_bitmaps['ohlcv'].get(trading_date, 0)
            usable_count = usable_mask.bit_count()
            dropped_count = effective_count - usable_count

            missing_by_feature = {
                feature_name: effective_count - (feature_bitmaps[feature_name].get(trading_date, 0) & membership_mask).bit_count()
                for feature_name in DAILY_COVERAGE_FIELDS
            }
            red_flags = []
            if previous_usable_count not in (None, 0):
                usable_drop_ratio = (previous_usable_count - usable_count) / previous_usable_count
                if usable_drop_ratio >= USABLE_ASSET_CLIFF_DROP_RATIO:
                    red_flags.append(f'usable_asset_count_cliff:{usable_drop_ratio:.2%}')
                    self._increment(counters, 'usable_asset_count_cliff', 'critical', 1)
                    table_counters[('effective_universe_daily_coverage', 'critical', 'usable_asset_count_cliff')] += 1

            if any(missing_count > 0 for missing_count in missing_by_feature.values()):
                red_flags.append('missing_feature_coverage')

            rows.append({
                **self._metric_columns('coverage', 'effective_universe_daily', 'daily_feature_coverage', 'trade_date'),
                'date': trading_date,
                'effective_universe_count': effective_count,
                'feature_non_null_count': feature_non_null_count,
                'usable_asset_count': usable_count,
                'dropped_asset_count': dropped_count,
                'ohlcv_count': (feature_bitmaps['ohlcv'].get(trading_date, 0) & membership_mask).bit_count(),
                'rs_score_count': (feature_bitmaps['rs_score'].get(trading_date, 0) & membership_mask).bit_count(),
                'factor_score_count': (feature_bitmaps['factor_score'].get(trading_date, 0) & membership_mask).bit_count(),
                'sentiment_score_count': (feature_bitmaps['sentiment_score'].get(trading_date, 0) & membership_mask).bit_count(),
                'fundamental_snapshot_count': (feature_bitmaps['fundamental_snapshot'].get(trading_date, 0) & membership_mask).bit_count(),
                'capital_flow_snapshot_count': (feature_bitmaps['capital_flow_snapshot'].get(trading_date, 0) & membership_mask).bit_count(),
                'pe_non_null_count': (feature_bitmaps['pe'].get(trading_date, 0) & membership_mask).bit_count(),
                'pe_ttm_non_null_count': (feature_bitmaps['pe_ttm'].get(trading_date, 0) & membership_mask).bit_count(),
                'pb_non_null_count': (feature_bitmaps['pb'].get(trading_date, 0) & membership_mask).bit_count(),
                'roe_non_null_count': (feature_bitmaps['roe'].get(trading_date, 0) & membership_mask).bit_count(),
                'roe_qoq_non_null_count': (feature_bitmaps['roe_qoq'].get(trading_date, 0) & membership_mask).bit_count(),
                'main_force_net_5d_non_null_count': (feature_bitmaps['main_force_net_5d'].get(trading_date, 0) & membership_mask).bit_count(),
                'margin_balance_change_5d_non_null_count': (feature_bitmaps['margin_balance_change_5d'].get(trading_date, 0) & membership_mask).bit_count(),
                'pe_ttm_percentile_score_count': (feature_bitmaps['pe_ttm_percentile_score'].get(trading_date, 0) & membership_mask).bit_count(),
                'pb_percentile_score_count': (feature_bitmaps['pb_percentile_score'].get(trading_date, 0) & membership_mask).bit_count(),
                'composite_score_count': (feature_bitmaps['composite_score'].get(trading_date, 0) & membership_mask).bit_count(),
                'missing_by_feature': missing_by_feature,
                'coverage_status': 'complete' if not red_flags and dropped_count == 0 else 'issues_detected',
                'red_flags': red_flags,
            })
            previous_usable_count = usable_count

        writer.write_csv('effective_universe_daily_coverage', writer.fieldnames_for('effective_universe_daily_coverage'), rows)

    def _write_cross_section_audit(self, writer, audit_dates, effective_universe_by_date):
        if not audit_dates:
            writer.write_csv('cross_section_metric_audit', writer.fieldnames_for('cross_section_metric_audit'), [])
            writer.write_csv('cross_section_metric_participants', writer.fieldnames_for('cross_section_metric_participants'), [])
            return

        audit_dates_set = set(audit_dates)
        effective_asset_ids = {target_date: set(effective_universe_by_date.get(target_date, set())) for target_date in audit_dates}
        participant_payload = defaultdict(lambda: defaultdict(dict))

        for row in TechnicalIndicator.objects.filter(
            timestamp__date__in=audit_dates,
            indicator_type='RS_SCORE',
        ).select_related('asset').values('timestamp__date', 'asset_id', 'asset__symbol', 'asset__ts_code', 'asset__name', 'value').iterator(chunk_size=5000):
            participant_payload[row['timestamp__date']]['RS_SCORE'][row['asset_id']] = {
                'asset_id': row['asset_id'],
                'symbol': row['asset__symbol'],
                'ts_code': row['asset__ts_code'],
                'name': row['asset__name'],
                'value': float(row['value']),
            }

        for row in FactorScore.objects.filter(
            date__in=audit_dates,
            mode=FactorScore.FactorMode.COMPOSITE,
        ).select_related('asset').values(
            'date', 'asset_id', 'asset__symbol', 'asset__ts_code', 'asset__name',
            'pe_ttm_percentile_score', 'pb_percentile_score', 'composite_score',
        ).iterator(chunk_size=5000):
            payload = {
                'asset_id': row['asset_id'],
                'symbol': row['asset__symbol'],
                'ts_code': row['asset__ts_code'],
                'name': row['asset__name'],
            }
            if row['pe_ttm_percentile_score'] is not None:
                participant_payload[row['date']]['pe_ttm_percentile_score'][row['asset_id']] = {
                    **payload,
                    'value': float(row['pe_ttm_percentile_score']),
                }
            if row['pb_percentile_score'] is not None:
                participant_payload[row['date']]['pb_percentile_score'][row['asset_id']] = {
                    **payload,
                    'value': float(row['pb_percentile_score']),
                }
            if row['composite_score'] is not None:
                participant_payload[row['date']]['composite_score'][row['asset_id']] = {
                    **payload,
                    'value': float(row['composite_score']),
                }

        audit_rows = []
        participant_rows = []
        for target_date in audit_dates:
            universe_asset_ids = effective_asset_ids.get(target_date, set())
            effective_count = len(universe_asset_ids)
            for feature_name in ('RS_SCORE', 'pe_ttm_percentile_score', 'pb_percentile_score', 'composite_score'):
                participants = participant_payload.get(target_date, {}).get(feature_name, {})
                participant_ids = set(participants.keys())
                missing_ids = sorted(universe_asset_ids - participant_ids)
                unexpected_ids = sorted(participant_ids - universe_asset_ids)
                values = sorted(item['value'] for item in participants.values())
                buckets = self._distribution_buckets(values)
                if unexpected_ids:
                    coverage_status = 'outside_universe_mismatch'
                elif missing_ids:
                    coverage_status = 'incomplete'
                else:
                    coverage_status = 'complete'

                audit_rows.append({
                    **self._metric_columns('cross_section', feature_name, 'effective_universe_alignment', 'sample_trade_date'),
                    'date': target_date,
                    'feature_name': feature_name,
                    'effective_universe_count': effective_count,
                    'participant_count': len(participant_ids),
                    'missing_from_universe_count': len(missing_ids),
                    'unexpected_outside_universe_count': len(unexpected_ids),
                    'min_value': values[0] if values else '',
                    'p10': self._percentile_from_sorted(values, 0.10),
                    'p25': self._percentile_from_sorted(values, 0.25),
                    'p50': self._percentile_from_sorted(values, 0.50),
                    'p75': self._percentile_from_sorted(values, 0.75),
                    'p90': self._percentile_from_sorted(values, 0.90),
                    'max_value': values[-1] if values else '',
                    **buckets,
                    'coverage_status': coverage_status,
                    'details': {
                        'missing_asset_ids': missing_ids[:50],
                        'unexpected_asset_ids': unexpected_ids[:50],
                    },
                })

                for asset_id, payload in sorted(participants.items(), key=lambda item: item[1]['ts_code']):
                    participant_rows.append({
                        **self._metric_columns('cross_section', feature_name, 'participant_list', 'sample_trade_date'),
                        'date': target_date,
                        'feature_name': feature_name,
                        'asset_id': asset_id,
                        'asset_symbol': payload['symbol'],
                        'asset_ts_code': payload['ts_code'],
                        'asset_name': payload['name'],
                        'in_effective_universe': asset_id in universe_asset_ids,
                        'value': payload['value'],
                    })

        writer.write_csv('cross_section_metric_audit', writer.fieldnames_for('cross_section_metric_audit'), audit_rows)
        writer.write_csv('cross_section_metric_participants', writer.fieldnames_for('cross_section_metric_participants'), participant_rows)

    def _metric_columns(self, metric_family, metric_name, rule_name, report_scope):
        return {
            'metric_family': metric_family,
            'metric_name': metric_name,
            'rule_name': rule_name,
            'report_scope': report_scope,
        }

    def _metric_family_for_table(self, table):
        if table == 'asset':
            return 'asset_lifecycle'
        if table == 'ohlcv':
            return 'ohlcv'
        if table in {'macro_snapshot', 'market_context'}:
            return 'macro'
        if table in {'technical_indicator', 'factor_score', 'fundamental_factor_snapshot', 'capital_flow_snapshot', 'sentiment_score'}:
            return 'feature'
        return table

    def _asset_issue_row(self, issue_type, severity, table, field, asset, trading_date, details, metric_family=None, metric_name=None, rule_name=None, report_scope='asset_date'):
        return {
            **self._metric_columns(
                metric_family or self._metric_family_for_table(table),
                metric_name or (field or table),
                rule_name or issue_type,
                report_scope,
            ),
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

    def _write_summary_reports(self, writer, counters, table_counters, field_counters, reason_counters, assets, trading_dates, start_date, end_date, floor_date, technical_indicators, options, run_started_at, run_started_perf):
        calendar_max_gap_days = 0
        if len(trading_dates) > 1:
            calendar_max_gap_days = max(
                (current - previous).days
                for previous, current in zip(trading_dates, trading_dates[1:])
            )

        completed_at = timezone.now()
        total_elapsed_seconds = round(max(0.0, perf_counter() - run_started_perf), 3)

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
            'generated_at': completed_at.isoformat(),
            'started_at': run_started_at.isoformat(),
            'completed_at': completed_at.isoformat(),
            'total_elapsed_seconds': total_elapsed_seconds,
            'start_date': start_date,
            'end_date': end_date,
            'global_floor_date': floor_date,
            'coverage_status': 'complete' if self._critical_count(counters) == 0 else 'issues_detected',
            'asset_count': len(assets),
            'effective_universe_only': bool(options.get('effective_universe_only')),
            'trading_date_count': len(trading_dates),
            'trading_calendar_start': trading_dates[0] if trading_dates else None,
            'trading_calendar_end': trading_dates[-1] if trading_dates else None,
            'trading_calendar_max_gap_days': calendar_max_gap_days,
            'technical_indicators': list(technical_indicators),
            'macro_max_age_days': options['macro_max_age_days'],
            'max_detail_rows': options['max_detail_rows'],
            'detail_rows_written': writer.detail_rows_written,
            'detail_rows_dropped': writer.detail_rows_dropped,
            'critical_issues': self._critical_count(counters),
            'cross_section_audit_dates': self._resolve_cross_section_audit_dates(options.get('cross_section_audit_dates'), start_date, end_date),
            'section_one_limitations': list(SECTION_ONE_LIMITATIONS),
            'report_descriptions': {
                filename: description
                for filename, description in REPORT_DESCRIPTIONS.items()
                if (
                    filename == 'metadata.json'
                    or (
                        filename != 'fundamental_reconciliation_audit.csv'
                        and (writer.selected_reports is None or filename[:-4] in writer.selected_reports)
                    )
                    or (
                        filename == 'fundamental_reconciliation_audit.csv'
                        and int(options.get('fundamental_reconciliation_sample_size') or 0) > 0
                        and (writer.selected_reports is None or filename[:-4] in writer.selected_reports)
                    )
                )
            },
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