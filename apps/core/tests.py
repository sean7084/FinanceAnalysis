import csv
import datetime
import json
import tempfile
from io import StringIO
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from django.core import mail
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.analytics.management.commands.backfill_technical_indicators import Command as TechnicalIndicatorBackfillCommand
from apps.analytics.models import SignalEvent, TechnicalIndicator
from apps.backtest.models import BacktestRun, BacktestTrade
from apps.factors.models import AssetMarginDetailSnapshot, AssetMoneyFlowSnapshot, CapitalFlowSnapshot, FactorScore, FundamentalFactorSnapshot
from apps.macro.models import EventImpactStat, MacroSnapshot, MarketContext
from apps.markets.benchmarking import PIT_UNION_BENCHMARK_CODE
from apps.markets.models import Asset, AssetSuspension, BenchmarkIndexDaily, ExchangeTradingCalendar, IndexMembership, Market, OHLCV
from apps.prediction.models import PredictionResult
from apps.prediction.models_lightgbm import EnsembleWeightSnapshot, LightGBMPrediction
from apps.sentiment.models import ConceptHeat, NewsArticle, SentimentScore


REQUIRED_TECHNICAL_INDICATORS = (
    ('ADX', Decimal('20.00000000'), {'timeperiod': 14}),
    ('BBANDS', Decimal('10.00000000'), {'timeperiod': 20, 'nbdevup': 2, 'nbdevdn': 2}),
    ('EMA', Decimal('10.30000000'), {'timeperiod': 20}),
    ('FIB_RET', Decimal('10.00000000'), {'lookback_days': 60}),
    ('MACD', Decimal('0.12000000'), {'fastperiod': 12, 'slowperiod': 26, 'signalperiod': 9}),
    ('MOM_10D', Decimal('0.06000000'), {'n_days': 10}),
    ('MOM_20D', Decimal('0.09000000'), {'n_days': 20}),
    ('MOM_5D', Decimal('0.03000000'), {'n_days': 5}),
    ('OBV', Decimal('1200000.00000000'), {}),
    ('RSI', Decimal('55.00000000'), {'timeperiod': 14}),
    ('RS_SCORE', Decimal('0.70000000'), {}),
    ('SMA', Decimal('10.10000000'), {'timeperiod': 20}),
    ('STOCH', Decimal('65.00000000'), {'fastk_period': 14, 'slowk_period': 3, 'slowd_period': 3}),
)


def read_csv(path):
    with Path(path).open(newline='', encoding='utf-8') as handle:
        return list(csv.DictReader(handle))


class DataQualityValidationCommandTests(TestCase):
    def setUp(self):
        self.d1 = timezone.datetime(2024, 1, 2).date()
        self.d2 = timezone.datetime(2024, 1, 3).date()
        self.d3 = timezone.datetime(2024, 1, 4).date()
        self.d4 = timezone.datetime(2024, 1, 5).date()

        self.market_sse = Market.objects.create(code='SSE', name='Shanghai Stock Exchange')
        self.market_szse = Market.objects.create(code='SZSE', name='Shenzhen Stock Exchange')
        self.asset_old = Asset.objects.create(
            market=self.market_sse,
            symbol='600001',
            ts_code='600001.SH',
            name='Old Asset',
            list_date=timezone.datetime(2000, 1, 1).date(),
            delist_date=self.d3,
        )
        self.asset_new = Asset.objects.create(
            market=self.market_szse,
            symbol='001391',
            ts_code='001391.SZ',
            name='New Asset',
            list_date=timezone.datetime(2024, 1, 3).date(),
        )
        self.asset_timed_suspension = Asset.objects.create(
            market=self.market_szse,
            symbol='002222',
            ts_code='002222.SZ',
            name='Timed Suspension Asset',
            list_date=self.d1,
        )
        self.asset_delisted_missing = Asset.objects.create(
            market=self.market_sse,
            symbol='600777',
            ts_code='600777.SH',
            name='Delisted Missing Asset',
            list_date=timezone.datetime(2000, 1, 1).date(),
            delist_date=self.d3,
        )
        self.asset_missing_list_date = Asset.objects.create(
            market=self.market_szse,
            symbol='300001',
            ts_code='300001.SZ',
            name='Missing List Date Asset',
        )

        for exchange_code in ('SSE', 'SZSE'):
            ExchangeTradingCalendar.objects.bulk_create([
                ExchangeTradingCalendar(exchange_code=exchange_code, trade_date=self.d1),
                ExchangeTradingCalendar(exchange_code=exchange_code, trade_date=self.d2, previous_trade_date=self.d1),
                ExchangeTradingCalendar(exchange_code=exchange_code, trade_date=self.d3, previous_trade_date=self.d2),
                ExchangeTradingCalendar(exchange_code=exchange_code, trade_date=self.d4, previous_trade_date=self.d3),
            ])

        AssetSuspension.objects.create(
            asset=self.asset_old,
            trade_date=self.d2,
            suspend_type='S',
            suspend_timing=None,
            is_full_day=True,
        )
        AssetSuspension.objects.create(
            asset=self.asset_timed_suspension,
            trade_date=self.d2,
            suspend_type='S',
            suspend_timing='09:30-10:00',
            is_full_day=False,
        )

        self._ohlcv(self.asset_old, self.d1, '10')
        self._ohlcv(self.asset_old, self.d3, '10.2')
        self._ohlcv(self.asset_old, self.d4, '10.4')
        self._ohlcv(self.asset_new, self.d2, '20')
        self._ohlcv(self.asset_new, self.d3, '20.2')
        self._ohlcv(self.asset_new, self.d4, '20.4')
        self._ohlcv(self.asset_timed_suspension, self.d1, '15')
        self._ohlcv(self.asset_timed_suspension, self.d3, '15.2')
        self._ohlcv(self.asset_timed_suspension, self.d4, '15.4')
        self._ohlcv(self.asset_delisted_missing, self.d1, '11')
        self._ohlcv(self.asset_delisted_missing, self.d2, '11.1')
        self._ohlcv(self.asset_delisted_missing, self.d3, '11.3')
        self._ohlcv(self.asset_missing_list_date, self.d2, '8')

        OHLCV.objects.filter(asset=self.asset_old, date=self.d1).update(
            high=Decimal('9.0'),
            low=Decimal('10.5'),
        )

        self._complete_related_rows(self.asset_old, self.d1)
        self._complete_related_rows(self.asset_new, self.d2)
        self._complete_related_rows(self.asset_timed_suspension, self.d1)
        self._complete_related_rows(self.asset_timed_suspension, self.d3)
        self._complete_related_rows(self.asset_timed_suspension, self.d4)
        self._complete_related_rows(self.asset_delisted_missing, self.d1)
        self._complete_related_rows(self.asset_delisted_missing, self.d2)
        self._complete_related_rows(self.asset_missing_list_date, self.d2)
        FundamentalFactorSnapshot.objects.filter(asset=self.asset_old, date=self.d1).update(
            metadata={
                'fina_indicator_ann_date': '2024-01-03',
                'fina_indicator_end_date': '2023-12-31',
            },
        )

        IndexMembership.objects.bulk_create([
            IndexMembership(
                asset=self.asset_old,
                index_code='000300.SH',
                index_name='CSI 300',
                trade_date=self.d1,
                weight=Decimal('1.000000'),
            ),
            IndexMembership(
                asset=self.asset_new,
                index_code='000300.SH',
                index_name='CSI 300',
                trade_date=self.d2,
                weight=Decimal('1.000000'),
            ),
            IndexMembership(
                asset=self.asset_new,
                index_code='000300.SH',
                index_name='CSI 300',
                trade_date=self.d4,
                weight=Decimal('1.000000'),
            ),
        ])

        MacroSnapshot.objects.create(
            date=timezone.datetime(2024, 1, 1).date(),
            pmi_manufacturing=Decimal('50.0'),
            pmi_non_manufacturing=Decimal('51.0'),
            cn6m_yield=Decimal('2.1'),
            cn1y_yield=Decimal('2.2'),
            cn3y_yield=Decimal('2.3'),
            cn5y_yield=Decimal('2.4'),
            cn7y_yield=Decimal('2.45'),
            cn10y_yield=Decimal('2.5'),
            cn30y_yield=Decimal('3.0'),
        )
        MarketContext.objects.create(
            context_key='current',
            macro_phase=MarketContext.MacroPhase.RECOVERY,
            starts_at=timezone.datetime(2024, 1, 1).date(),
            ends_at=timezone.datetime(2024, 1, 31).date(),
            is_active=True,
        )

    def _ohlcv(self, asset, trade_date, close):
        close_value = Decimal(close)
        OHLCV.objects.create(
            asset=asset,
            date=trade_date,
            open=close_value,
            high=close_value + Decimal('0.5'),
            low=close_value - Decimal('0.5'),
            close=close_value,
            adj_close=close_value,
            volume=1000000,
            amount=close_value * Decimal('1000000'),
        )

    def _complete_related_rows(self, asset, trade_date, include_rs_score=True):
        timestamp = timezone.make_aware(timezone.datetime.combine(trade_date, timezone.datetime.min.time()))
        for indicator_type, value, parameters in REQUIRED_TECHNICAL_INDICATORS:
            if indicator_type == 'RS_SCORE' and not include_rs_score:
                continue
            TechnicalIndicator.objects.create(
                asset=asset,
                timestamp=timestamp,
                indicator_type=indicator_type,
                value=value,
                parameters=parameters,
            )
        FundamentalFactorSnapshot.objects.create(
            asset=asset,
            date=trade_date,
            pe=Decimal('10'),
            pe_ttm=Decimal('9.5'),
            pb=Decimal('1.5'),
            roe=Decimal('0.100000'),
            roe_qoq=Decimal('0.010000'),
        )
        CapitalFlowSnapshot.objects.create(
            asset=asset,
            date=trade_date,
            main_force_net_5d=Decimal('100000'),
            margin_balance_change_5d=Decimal('200000'),
        )
        FactorScore.objects.create(
            asset=asset,
            date=trade_date,
            mode=FactorScore.FactorMode.COMPOSITE,
            pe_ttm_percentile_score=Decimal('0.400000'),
            pb_percentile_score=Decimal('0.500000'),
            roe_trend_score=Decimal('0.600000'),
            main_force_flow_score=Decimal('0.700000'),
            margin_flow_score=Decimal('0.800000'),
            technical_reversal_score=Decimal('0.300000'),
            sentiment_score=Decimal('0.500000'),
            fundamental_score=Decimal('0.500000'),
            capital_flow_score=Decimal('0.700000'),
            technical_score=Decimal('0.300000'),
            composite_score=Decimal('0.500000'),
            bottom_probability_score=Decimal('0.500000'),
        )
        SentimentScore.objects.create(
            article=None,
            asset=asset,
            date=trade_date,
            score_type=SentimentScore.ScoreType.ASSET_7D,
            positive_score=Decimal('0.100000'),
            neutral_score=Decimal('0.800000'),
            negative_score=Decimal('0.100000'),
            sentiment_score=Decimal('0.000000'),
            sentiment_label=SentimentScore.Label.NEUTRAL,
        )

    def _create_moneyflow_source(self, asset, trade_date):
        AssetMoneyFlowSnapshot.objects.create(
            asset=asset,
            date=trade_date,
            buy_lg_amount=Decimal('1000'),
            sell_lg_amount=Decimal('500'),
            buy_elg_amount=Decimal('2000'),
            sell_elg_amount=Decimal('1000'),
            net_mf_amount=Decimal('1500'),
        )

    def _create_margin_source(self, asset, trade_date, rzrqye):
        AssetMarginDetailSnapshot.objects.create(
            asset=asset,
            date=trade_date,
            rzrqye=None if rzrqye is None else Decimal(rzrqye),
        )

    def test_validate_data_quality_writes_actionable_reports(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            call_command(
                'validate_data_quality',
                start_date='2024-01-02',
                end_date='2024-01-05',
                output_dir=temp_dir,
                cross_section_audit_dates='2024-01-02,2024-01-03',
            )

            output_dir = Path(temp_dir)
            for filename in [
                'summary.csv',
                'missing_by_table.csv',
                'missing_fields.csv',
                'affected_asset_dates.csv',
                'index_membership_history_gaps.csv',
                'index_membership_monthly_blanks.csv',
                'benchmark_index_daily_gaps.csv',
                'pit_benchmark_daily_gaps.csv',
                'ohlcv_continuity_gaps.csv',
                'fundamental_snapshot_continuity_gaps.csv',
                'capital_flow_snapshot_continuity_gaps.csv',
                'ohlcv_excused_gaps.csv',
                'feature_dependency_gaps.csv',
                'ohlcv_price_anomalies.csv',
                'asset_lifecycle_issues.csv',
                'feature_source_asof_issues.csv',
                'effective_universe_daily_coverage.csv',
                'cross_section_metric_audit.csv',
                'cross_section_metric_participants.csv',
                'official_trading_calendar.csv',
                'null_reason_buckets.csv',
                'metadata.json',
            ]:
                self.assertTrue((output_dir / filename).exists(), filename)

            continuity_rows = read_csv(output_dir / 'ohlcv_continuity_gaps.csv')
            old_asset_rows = [row for row in continuity_rows if row['asset_ts_code'] == '600001.SH']
            self.assertEqual(len(old_asset_rows), 0)
            timed_asset_rows = [row for row in continuity_rows if row['asset_ts_code'] == '002222.SZ']
            self.assertEqual(len(timed_asset_rows), 0)
            self.assertTrue(any(row['asset_ts_code'] == '300001.SZ' for row in continuity_rows))
            self.assertTrue(all(row['metric_family'] == 'ohlcv' for row in continuity_rows))

            fundamental_gap_rows = read_csv(output_dir / 'fundamental_snapshot_continuity_gaps.csv')
            self.assertEqual(len(fundamental_gap_rows), 4)
            self.assertEqual({row['asset_ts_code'] for row in fundamental_gap_rows}, {'001391.SZ'})
            self.assertEqual({row['field'] for row in fundamental_gap_rows}, {'pe', 'pb', 'roe', 'roe_qoq'})
            self.assertTrue(all(row['gap_start'] == '2024-01-04' for row in fundamental_gap_rows))
            self.assertTrue(all(row['gap_end'] == '2024-01-05' for row in fundamental_gap_rows))
            self.assertTrue(all(row['expected_count'] == '3' for row in fundamental_gap_rows))
            self.assertTrue(all(row['actual_count'] == '1' for row in fundamental_gap_rows))
            self.assertTrue(all(row['snapshot_row_count'] == '1' for row in fundamental_gap_rows))
            self.assertTrue(all(row['missing_count'] == '2' for row in fundamental_gap_rows))
            self.assertTrue(all(row['metric_family'] == 'feature' for row in fundamental_gap_rows))

            capital_flow_gap_rows = read_csv(output_dir / 'capital_flow_snapshot_continuity_gaps.csv')
            self.assertEqual(len(capital_flow_gap_rows), 2)
            self.assertEqual({row['asset_ts_code'] for row in capital_flow_gap_rows}, {'001391.SZ'})
            self.assertEqual({row['field'] for row in capital_flow_gap_rows}, {'main_force_net_5d', 'margin_balance_change_5d'})
            self.assertTrue(all(row['gap_start'] == '2024-01-04' for row in capital_flow_gap_rows))
            self.assertTrue(all(row['gap_end'] == '2024-01-05' for row in capital_flow_gap_rows))
            self.assertTrue(all(row['expected_count'] == '3' for row in capital_flow_gap_rows))
            self.assertTrue(all(row['actual_count'] == '1' for row in capital_flow_gap_rows))
            self.assertTrue(all(row['snapshot_row_count'] == '1' for row in capital_flow_gap_rows))
            self.assertTrue(all(row['missing_count'] == '2' for row in capital_flow_gap_rows))
            self.assertTrue(all(row['metric_family'] == 'feature' for row in capital_flow_gap_rows))

            excused_rows = read_csv(output_dir / 'ohlcv_excused_gaps.csv')
            self.assertTrue(any(
                row['asset_ts_code'] == '001391.SZ'
                and row['exclusion_cause'] == 'before_list_date'
                and row['window_start'] == '2024-01-02'
                and row['window_end'] == '2024-01-02'
                for row in excused_rows
            ))
            self.assertTrue(any(
                row['asset_ts_code'] == '600777.SH'
                and row['exclusion_cause'] == 'on_or_after_delist_date'
                and row['window_start'] == '2024-01-05'
                and row['window_end'] == '2024-01-05'
                for row in excused_rows
            ))
            self.assertTrue(any(
                row['asset_ts_code'] == '600001.SH'
                and row['exclusion_cause'] == 'suspension'
                and row['full_day_excluded_count'] == '1'
                and row['timed_excluded_count'] == '0'
                for row in excused_rows
            ))
            self.assertTrue(any(
                row['asset_ts_code'] == '002222.SZ'
                and row['exclusion_cause'] == 'suspension'
                and row['full_day_excluded_count'] == '0'
                and row['timed_excluded_count'] == '1'
                for row in excused_rows
            ))
            self.assertTrue(all(row['metric_family'] == 'ohlcv' for row in excused_rows))

            membership_history_rows = read_csv(output_dir / 'index_membership_history_gaps.csv')
            self.assertEqual(membership_history_rows, [])

            monthly_blank_rows = read_csv(output_dir / 'index_membership_monthly_blanks.csv')
            self.assertEqual(monthly_blank_rows, [])

            benchmark_gap_rows = read_csv(output_dir / 'benchmark_index_daily_gaps.csv')
            self.assertTrue(any(row['index_code'] == '000300.SH' for row in benchmark_gap_rows))
            self.assertTrue(all(row['metric_family'] == 'benchmark' for row in benchmark_gap_rows))

            pit_benchmark_rows = read_csv(output_dir / 'pit_benchmark_daily_gaps.csv')
            self.assertTrue(any(row['benchmark_code'] == PIT_UNION_BENCHMARK_CODE for row in pit_benchmark_rows))
            self.assertTrue(all(row['metric_family'] == 'benchmark' for row in pit_benchmark_rows))

            cross_rows = read_csv(output_dir / 'feature_dependency_gaps.csv')
            missing_factor_rows = [row for row in cross_rows if row['issue_type'] == 'missing_factor_score']
            self.assertEqual(len(missing_factor_rows), 2)
            self.assertEqual({row['asset_ts_code'] for row in missing_factor_rows}, {'001391.SZ'})
            missing_fundamental_rows = [row for row in cross_rows if row['issue_type'] == 'missing_fundamental_snapshot']
            self.assertEqual(len(missing_fundamental_rows), 2)
            self.assertEqual({row['asset_ts_code'] for row in missing_fundamental_rows}, {'001391.SZ'})
            self.assertEqual({row['field'] for row in missing_fundamental_rows}, {'snapshot_row'})
            self.assertEqual(
                {row['date'] for row in missing_fundamental_rows},
                {'2024-01-04', '2024-01-05'},
            )
            missing_capital_flow_rows = [row for row in cross_rows if row['issue_type'] == 'missing_capital_flow_snapshot']
            self.assertEqual(len(missing_capital_flow_rows), 2)
            self.assertEqual({row['asset_ts_code'] for row in missing_capital_flow_rows}, {'001391.SZ'})
            self.assertEqual({row['field'] for row in missing_capital_flow_rows}, {'snapshot_row'})
            self.assertEqual(
                {row['date'] for row in missing_capital_flow_rows},
                {'2024-01-04', '2024-01-05'},
            )
            missing_macro_rows = [row for row in cross_rows if row['issue_type'] == 'missing_macro_field']
            self.assertTrue(missing_macro_rows)
            self.assertTrue(any(
                row['required_table'] == 'macro_snapshot'
                and row['field'] == 'dxy'
                and row['date'] == '2024-01-01'
                for row in missing_macro_rows
            ))
            missing_indicator_rows = [row for row in cross_rows if row['issue_type'] == 'missing_technical_indicator']
            self.assertEqual(len(missing_indicator_rows), 26)
            self.assertEqual({row['asset_ts_code'] for row in missing_indicator_rows}, {'001391.SZ'})
            self.assertEqual(
                {row['field'] for row in missing_indicator_rows},
                {indicator_type for indicator_type, _value, _parameters in REQUIRED_TECHNICAL_INDICATORS},
            )
            self.assertEqual(
                {row['date'] for row in missing_indicator_rows},
                {'2024-01-04', '2024-01-05'},
            )
            latest_indicator_rows = [row for row in cross_rows if row['issue_type'] == 'missing_latest_technical_indicator']
            self.assertEqual(latest_indicator_rows, [])
            self.assertTrue(all(row['metric_family'] == 'feature_dependency' for row in cross_rows))

            anomaly_rows = read_csv(output_dir / 'ohlcv_price_anomalies.csv')
            self.assertTrue(any(row['issue_type'] == 'ohlcv_high_below_low' for row in anomaly_rows))
            self.assertTrue(any(row['asset_ts_code'] == '600001.SH' for row in anomaly_rows))
            self.assertTrue(all(row['metric_family'] == 'ohlcv' for row in anomaly_rows))

            listing_rows = read_csv(output_dir / 'asset_lifecycle_issues.csv')
            self.assertTrue(any(row['issue_type'] == 'missing_list_date' for row in listing_rows))
            self.assertTrue(any(row['asset_ts_code'] == '300001.SZ' for row in listing_rows))
            self.assertTrue(any(row['issue_type'] == 'post_delist_ohlcv' and row['asset_ts_code'] == '600001.SH' for row in listing_rows))
            self.assertTrue(all(row['metric_family'] == 'asset_lifecycle' for row in listing_rows))

            asof_rows = read_csv(output_dir / 'feature_source_asof_issues.csv')
            self.assertTrue(any(row['issue_type'] == 'future_financial_announcement_reference' for row in asof_rows))
            self.assertTrue(any(row['asset_ts_code'] == '600001.SH' for row in asof_rows))

            coverage_rows = read_csv(output_dir / 'effective_universe_daily_coverage.csv')
            self.assertTrue(any(row['date'] == '2024-01-02' for row in coverage_rows))
            self.assertTrue(any(row['date'] == '2024-01-03' for row in coverage_rows))
            self.assertTrue(all(row['metric_family'] == 'coverage' for row in coverage_rows))

            cross_section_rows = read_csv(output_dir / 'cross_section_metric_audit.csv')
            self.assertTrue(any(row['date'] == '2024-01-02' and row['feature_name'] == 'RS_SCORE' for row in cross_section_rows))
            participant_rows = read_csv(output_dir / 'cross_section_metric_participants.csv')
            self.assertTrue(any(row['asset_ts_code'] == '600001.SH' and row['feature_name'] == 'RS_SCORE' for row in participant_rows))

            calendar_rows = read_csv(output_dir / 'official_trading_calendar.csv')
            self.assertEqual({row['exchange_code'] for row in calendar_rows}, {'SSE', 'SZSE'})
            self.assertEqual(len(calendar_rows), 8)

            missing_field_rows = read_csv(output_dir / 'missing_fields.csv')
            self.assertTrue(any(
                row['table'] == 'fundamental_factor_snapshot'
                and row['field'] == 'snapshot_row'
                and row['issue_type'] == 'missing_fundamental_snapshot'
                for row in missing_field_rows
            ))
            self.assertTrue(any(
                row['table'] == 'capital_flow_snapshot'
                and row['field'] == 'snapshot_row'
                and row['issue_type'] == 'missing_capital_flow_snapshot'
                for row in missing_field_rows
            ))
            self.assertTrue(any(
                row['table'] == 'fundamental_factor_snapshot'
                and row['field'] == 'total_share'
                and row['issue_type'] == 'field_null'
                for row in missing_field_rows
            ))
            self.assertTrue(any(
                row['table'] == 'macro_snapshot'
                and row['field'] == 'dxy'
                and row['issue_type'] == 'missing_macro_field'
                for row in missing_field_rows
            ))
            neutral_default_rows = [
                row for row in missing_field_rows
                if row['issue_type'] == 'neutral_default_value' and row['table'] == 'factor_score'
            ]
            self.assertTrue(neutral_default_rows)

            with (output_dir / 'metadata.json').open(encoding='utf-8') as handle:
                metadata = json.load(handle)
            started_at = datetime.datetime.fromisoformat(metadata['started_at'])
            completed_at = datetime.datetime.fromisoformat(metadata['completed_at'])
            self.assertEqual(metadata['global_floor_date'], '2010-01-01')
            self.assertEqual(metadata['asset_count'], 5)
            self.assertEqual(metadata['coverage_status'], 'issues_detected')
            self.assertLessEqual(started_at, completed_at)
            self.assertGreaterEqual(metadata['total_elapsed_seconds'], 0)
            self.assertEqual(
                metadata['technical_indicators'],
                [indicator_type for indicator_type, _value, _parameters in REQUIRED_TECHNICAL_INDICATORS],
            )
            self.assertNotIn('latest_snapshot_technical_indicators', metadata)
            self.assertEqual(metadata['trading_calendar_start'], '2024-01-02')
            self.assertEqual(metadata['trading_calendar_end'], '2024-01-05')
            self.assertEqual(metadata['cross_section_audit_dates'], ['2024-01-02', '2024-01-03'])
            self.assertIn('index_membership_history_gaps.csv', metadata['report_descriptions'])
            self.assertIn('index_membership_monthly_blanks.csv', metadata['report_descriptions'])
            self.assertIn('benchmark_index_daily_gaps.csv', metadata['report_descriptions'])
            self.assertIn('pit_benchmark_daily_gaps.csv', metadata['report_descriptions'])
            self.assertIn('ohlcv_continuity_gaps.csv', metadata['report_descriptions'])
            self.assertIn('fundamental_snapshot_continuity_gaps.csv', metadata['report_descriptions'])
            self.assertIn('capital_flow_snapshot_continuity_gaps.csv', metadata['report_descriptions'])

    def test_validate_data_quality_separates_expected_and_suspicious_capital_flow_nulls(self):
        d5 = timezone.datetime(2024, 1, 6).date()
        d6 = timezone.datetime(2024, 1, 7).date()
        for exchange_code in ('SSE', 'SZSE'):
            ExchangeTradingCalendar.objects.create(exchange_code=exchange_code, trade_date=d5, previous_trade_date=self.d4)
            ExchangeTradingCalendar.objects.create(exchange_code=exchange_code, trade_date=d6, previous_trade_date=d5)

        moneyflow_expected_asset = Asset.objects.create(
            market=self.market_sse,
            symbol='600551',
            ts_code='600551.SH',
            name='Moneyflow Expected Null',
            list_date=self.d1,
        )
        moneyflow_suspicious_asset = Asset.objects.create(
            market=self.market_sse,
            symbol='600552',
            ts_code='600552.SH',
            name='Moneyflow Suspicious Null',
            list_date=self.d1,
        )
        margin_missing_source_asset = Asset.objects.create(
            market=self.market_sse,
            symbol='600553',
            ts_code='600553.SH',
            name='Margin Missing Source Null',
            list_date=self.d1,
        )
        margin_warmup_asset = Asset.objects.create(
            market=self.market_sse,
            symbol='600554',
            ts_code='600554.SH',
            name='Margin Warmup Null',
            list_date=self.d1,
        )
        margin_same_day_null_asset = Asset.objects.create(
            market=self.market_sse,
            symbol='600555',
            ts_code='600555.SH',
            name='Margin Same Day Null',
            list_date=self.d1,
        )
        margin_fifth_prior_null_asset = Asset.objects.create(
            market=self.market_sse,
            symbol='600556',
            ts_code='600556.SH',
            name='Margin Fifth Prior Null',
            list_date=self.d1,
        )
        margin_suspicious_asset = Asset.objects.create(
            market=self.market_sse,
            symbol='600557',
            ts_code='600557.SH',
            name='Margin Suspicious Null',
            list_date=self.d1,
        )

        for asset in (moneyflow_expected_asset, moneyflow_suspicious_asset, margin_missing_source_asset):
            self._ohlcv(asset, d6, '12')
            self._complete_related_rows(asset, d6)

        for asset in (margin_warmup_asset, margin_same_day_null_asset, margin_fifth_prior_null_asset, margin_suspicious_asset):
            for trade_date, close in zip((self.d1, self.d2, self.d3, self.d4, d5, d6), ('10', '10.1', '10.2', '10.3', '10.4', '10.5')):
                self._ohlcv(asset, trade_date, close)
                self._complete_related_rows(asset, trade_date)

        CapitalFlowSnapshot.objects.filter(asset=moneyflow_expected_asset, date=d6).update(main_force_net_5d=None)
        CapitalFlowSnapshot.objects.filter(asset=moneyflow_suspicious_asset, date=d6).update(main_force_net_5d=None)
        self._create_moneyflow_source(moneyflow_suspicious_asset, d6)

        CapitalFlowSnapshot.objects.filter(asset=margin_missing_source_asset, date=d6).update(margin_balance_change_5d=None)

        CapitalFlowSnapshot.objects.filter(asset=margin_warmup_asset, date=d5).update(margin_balance_change_5d=None)
        for trade_date, rzrqye in zip((self.d1, self.d2, self.d3, self.d4, d5), ('100', '110', '120', '130', '140')):
            self._create_margin_source(margin_warmup_asset, trade_date, rzrqye)

        CapitalFlowSnapshot.objects.filter(asset=margin_same_day_null_asset, date=d6).update(margin_balance_change_5d=None)
        for trade_date, rzrqye in zip((self.d1, self.d2, self.d3, self.d4, d5, d6), ('200', '210', '220', '230', '240', None)):
            self._create_margin_source(margin_same_day_null_asset, trade_date, rzrqye)

        CapitalFlowSnapshot.objects.filter(asset=margin_fifth_prior_null_asset, date=d6).update(margin_balance_change_5d=None)
        for trade_date, rzrqye in zip((self.d1, self.d2, self.d3, self.d4, d5, d6), (None, '310', '320', '330', '340', '350')):
            self._create_margin_source(margin_fifth_prior_null_asset, trade_date, rzrqye)

        CapitalFlowSnapshot.objects.filter(asset=margin_suspicious_asset, date=d6).update(margin_balance_change_5d=None)
        for trade_date, rzrqye in zip((self.d1, self.d2, self.d3, self.d4, d5, d6), ('400', '410', '420', '430', '440', '450')):
            self._create_margin_source(margin_suspicious_asset, trade_date, rzrqye)

        symbols = ','.join([
            moneyflow_expected_asset.ts_code,
            moneyflow_suspicious_asset.ts_code,
            margin_missing_source_asset.ts_code,
            margin_warmup_asset.ts_code,
            margin_same_day_null_asset.ts_code,
            margin_fifth_prior_null_asset.ts_code,
            margin_suspicious_asset.ts_code,
        ])
        with tempfile.TemporaryDirectory() as temp_dir:
            call_command(
                'validate_data_quality',
                start_date=self.d1.isoformat(),
                end_date=d6.isoformat(),
                output_dir=temp_dir,
                symbols=symbols,
            )

            output_dir = Path(temp_dir)
            missing_field_rows = read_csv(output_dir / 'missing_fields.csv')
            reason_rows = read_csv(output_dir / 'null_reason_buckets.csv')
            affected_rows = read_csv(output_dir / 'affected_asset_dates.csv')
            summary_rows = read_csv(output_dir / 'summary.csv')

            self.assertTrue(any(
                row['table'] == 'capital_flow_snapshot'
                and row['field'] == 'main_force_net_5d'
                and row['issue_type'] == 'expected_field_null'
                and row['count'] == '1'
                for row in missing_field_rows
            ))
            self.assertTrue(any(
                row['table'] == 'capital_flow_snapshot'
                and row['field'] == 'main_force_net_5d'
                and row['issue_type'] == 'suspicious_field_null'
                and row['count'] == '1'
                for row in missing_field_rows
            ))
            self.assertTrue(any(
                row['table'] == 'capital_flow_snapshot'
                and row['field'] == 'margin_balance_change_5d'
                and row['issue_type'] == 'expected_field_null'
                and row['count'] == '4'
                for row in missing_field_rows
            ))
            self.assertTrue(any(
                row['table'] == 'capital_flow_snapshot'
                and row['field'] == 'margin_balance_change_5d'
                and row['issue_type'] == 'suspicious_field_null'
                and row['count'] == '1'
                for row in missing_field_rows
            ))

            self.assertTrue(any(
                row['table'] == 'capital_flow_snapshot'
                and row['field'] == 'main_force_net_5d'
                and row['reason'] == 'missing_moneyflow_source_row'
                and row['count'] == '1'
                for row in reason_rows
            ))
            self.assertTrue(any(
                row['table'] == 'capital_flow_snapshot'
                and row['field'] == 'main_force_net_5d'
                and row['reason'] == 'unexpected_null_with_moneyflow_source_row'
                and row['count'] == '1'
                for row in reason_rows
            ))
            self.assertTrue(any(
                row['table'] == 'capital_flow_snapshot'
                and row['field'] == 'margin_balance_change_5d'
                and row['reason'] == 'missing_margin_detail_source_row'
                and row['count'] == '1'
                for row in reason_rows
            ))
            self.assertTrue(any(
                row['table'] == 'capital_flow_snapshot'
                and row['field'] == 'margin_balance_change_5d'
                and row['reason'] == 'margin_diff_5_warmup_insufficient'
                and row['count'] == '1'
                for row in reason_rows
            ))
            self.assertTrue(any(
                row['table'] == 'capital_flow_snapshot'
                and row['field'] == 'margin_balance_change_5d'
                and row['reason'] == 'same_day_margin_balance_null'
                and row['count'] == '1'
                for row in reason_rows
            ))
            self.assertTrue(any(
                row['table'] == 'capital_flow_snapshot'
                and row['field'] == 'margin_balance_change_5d'
                and row['reason'] == 'fifth_prior_margin_balance_null'
                and row['count'] == '1'
                for row in reason_rows
            ))
            self.assertTrue(any(
                row['table'] == 'capital_flow_snapshot'
                and row['field'] == 'margin_balance_change_5d'
                and row['reason'] == 'unexpected_null_with_margin_source_inputs'
                and row['count'] == '1'
                for row in reason_rows
            ))

            self.assertTrue(any(
                row['asset_ts_code'] == moneyflow_expected_asset.ts_code
                and row['field'] == 'main_force_net_5d'
                and row['issue_type'] == 'expected_field_null'
                for row in affected_rows
            ))
            self.assertTrue(any(
                row['asset_ts_code'] == moneyflow_suspicious_asset.ts_code
                and row['field'] == 'main_force_net_5d'
                and row['issue_type'] == 'suspicious_field_null'
                for row in affected_rows
            ))
            self.assertTrue(any(
                row['asset_ts_code'] == margin_warmup_asset.ts_code
                and row['field'] == 'margin_balance_change_5d'
                and row['issue_type'] == 'expected_field_null'
                and 'diff(5) requires at least 6' in row['details']
                for row in affected_rows
            ))
            self.assertTrue(any(
                row['asset_ts_code'] == margin_suspicious_asset.ts_code
                and row['field'] == 'margin_balance_change_5d'
                and row['issue_type'] == 'suspicious_field_null'
                for row in affected_rows
            ))

            self.assertTrue(any(
                row['issue_type'] == 'capital_flow_snapshot.main_force_net_5d.expected_field_null'
                and row['count'] == '1'
                for row in summary_rows
            ))
            self.assertTrue(any(
                row['issue_type'] == 'capital_flow_snapshot.main_force_net_5d.suspicious_field_null'
                and row['count'] == '1'
                for row in summary_rows
            ))
            self.assertTrue(any(
                row['issue_type'] == 'capital_flow_snapshot.margin_balance_change_5d.expected_field_null'
                and row['count'] == '4'
                for row in summary_rows
            ))
            self.assertTrue(any(
                row['issue_type'] == 'capital_flow_snapshot.margin_balance_change_5d.suspicious_field_null'
                and row['count'] == '1'
                for row in summary_rows
            ))

    def test_validate_data_quality_labels_capital_flow_continuity_gap_reasons(self):
        d5 = timezone.datetime(2024, 1, 6).date()
        for exchange_code in ('SSE', 'SZSE'):
            ExchangeTradingCalendar.objects.create(exchange_code=exchange_code, trade_date=d5, previous_trade_date=self.d4)

        moneyflow_gap_asset = Asset.objects.create(
            market=self.market_sse,
            symbol='600561',
            ts_code='600561.SH',
            name='Moneyflow Gap Asset',
            list_date=self.d1,
        )
        margin_missing_source_asset = Asset.objects.create(
            market=self.market_sse,
            symbol='600562',
            ts_code='600562.SH',
            name='Margin Missing Source Gap Asset',
            list_date=self.d1,
        )
        margin_warmup_asset = Asset.objects.create(
            market=self.market_sse,
            symbol='600563',
            ts_code='600563.SH',
            name='Margin Warmup Gap Asset',
            list_date=self.d1,
        )
        margin_blackout_asset = Asset.objects.create(
            market=self.market_sse,
            symbol='600564',
            ts_code='600564.SH',
            name='Margin Blackout Gap Asset',
            list_date=self.d1,
        )
        margin_trailing_asset = Asset.objects.create(
            market=self.market_sse,
            symbol='600565',
            ts_code='600565.SH',
            name='Margin Trailing Gap Asset',
            list_date=self.d1,
        )

        for asset in (
            moneyflow_gap_asset,
            margin_missing_source_asset,
            margin_warmup_asset,
            margin_blackout_asset,
            margin_trailing_asset,
        ):
            for trade_date, close in zip((self.d1, self.d2, self.d3, self.d4, d5), ('10', '10.1', '10.2', '10.3', '10.4')):
                self._ohlcv(asset, trade_date, close)
                self._complete_related_rows(asset, trade_date)

        CapitalFlowSnapshot.objects.filter(asset=moneyflow_gap_asset, date=self.d3).update(main_force_net_5d=None)
        for trade_date in (self.d1, self.d2, self.d4, d5):
            self._create_moneyflow_source(moneyflow_gap_asset, trade_date)

        CapitalFlowSnapshot.objects.filter(asset=margin_missing_source_asset, date=self.d2).update(margin_balance_change_5d=None)

        CapitalFlowSnapshot.objects.filter(asset=margin_warmup_asset, date=d5).update(margin_balance_change_5d=None)
        for trade_date, rzrqye in zip((self.d1, self.d2, self.d3, self.d4, d5), ('100', '110', '120', '130', '140')):
            self._create_margin_source(margin_warmup_asset, trade_date, rzrqye)

        CapitalFlowSnapshot.objects.filter(asset=margin_blackout_asset, date=self.d3).update(margin_balance_change_5d=None)
        for trade_date, rzrqye in zip((self.d1, self.d2, self.d4, d5), ('200', '210', '240', '250')):
            self._create_margin_source(margin_blackout_asset, trade_date, rzrqye)

        CapitalFlowSnapshot.objects.filter(asset=margin_trailing_asset, date=d5).update(margin_balance_change_5d=None)
        for trade_date, rzrqye in zip((self.d1, self.d2, self.d3, self.d4), ('300', '310', '320', '330')):
            self._create_margin_source(margin_trailing_asset, trade_date, rzrqye)

        symbols = ','.join([
            moneyflow_gap_asset.ts_code,
            margin_missing_source_asset.ts_code,
            margin_warmup_asset.ts_code,
            margin_blackout_asset.ts_code,
            margin_trailing_asset.ts_code,
        ])

        with tempfile.TemporaryDirectory() as temp_dir:
            call_command(
                'validate_data_quality',
                start_date=self.d1.isoformat(),
                end_date=d5.isoformat(),
                output_dir=temp_dir,
                symbols=symbols,
            )

            gap_rows = read_csv(Path(temp_dir) / 'capital_flow_snapshot_continuity_gaps.csv')

            self.assertTrue(any(
                row['asset_ts_code'] == moneyflow_gap_asset.ts_code
                and row['field'] == 'main_force_net_5d'
                and row['gap_start'] == self.d3.isoformat()
                and row['gap_reason'] == 'missing_moneyflow_source_row'
                for row in gap_rows
            ))
            self.assertTrue(any(
                row['asset_ts_code'] == margin_missing_source_asset.ts_code
                and row['field'] == 'margin_balance_change_5d'
                and row['gap_start'] == self.d2.isoformat()
                and row['gap_reason'] == 'missing_margin_detail_source_row'
                for row in gap_rows
            ))
            self.assertTrue(any(
                row['asset_ts_code'] == margin_warmup_asset.ts_code
                and row['field'] == 'margin_balance_change_5d'
                and row['gap_start'] == d5.isoformat()
                and row['gap_reason'] == 'margin_diff_5_warmup_insufficient'
                for row in gap_rows
            ))
            self.assertTrue(any(
                row['asset_ts_code'] == margin_blackout_asset.ts_code
                and row['field'] == 'margin_balance_change_5d'
                and row['gap_start'] == self.d3.isoformat()
                and row['gap_reason'] == 'mid_history_margin_blackout'
                for row in gap_rows
            ))
            self.assertTrue(any(
                row['asset_ts_code'] == margin_trailing_asset.ts_code
                and row['field'] == 'margin_balance_change_5d'
                and row['gap_start'] == d5.isoformat()
                and row['gap_reason'] == 'trailing_margin_source_lag'
                for row in gap_rows
            ))

    def test_technical_indicator_continuity_warnings_appear_in_summary_and_metadata(self):
        asset = Asset.objects.create(
            market=self.market_sse,
            symbol='600588',
            ts_code='600588.SH',
            name='Technical Summary Asset',
            list_date=self.d2,
        )

        for trade_date, close in zip((self.d2, self.d3, self.d4), ('10', '10.1', '10.2')):
            self._ohlcv(asset, trade_date, close)
            self._complete_related_rows(asset, trade_date)

        TechnicalIndicator.objects.filter(
            asset=asset,
            timestamp__date=self.d3,
            indicator_type='RSI',
        ).delete()

        with tempfile.TemporaryDirectory() as temp_dir:
            call_command(
                'validate_data_quality',
                start_date=self.d2.isoformat(),
                end_date=self.d4.isoformat(),
                output_dir=temp_dir,
                symbols=asset.ts_code,
                technical_indicators='RSI',
            )

            output_dir = Path(temp_dir)
            summary_rows = read_csv(output_dir / 'summary.csv')
            missing_by_table_rows = read_csv(output_dir / 'missing_by_table.csv')
            missing_field_rows = read_csv(output_dir / 'missing_fields.csv')

            self.assertTrue(any(
                row['issue_type'] == 'technical_indicator_continuity_gap'
                and row['severity'] == 'warning'
                and row['count'] == '1'
                for row in summary_rows
            ))
            self.assertTrue(any(
                row['table'] == 'technical_indicator'
                and row['severity'] == 'warning'
                and row['issue_type'] == 'technical_indicator_continuity_gap'
                and row['count'] == '1'
                for row in missing_by_table_rows
            ))
            self.assertTrue(any(
                row['table'] == 'technical_indicator'
                and row['field'] == 'RSI[timeperiod=14]'
                and row['issue_type'] == 'technical_indicator_continuity_gap'
                and row['severity'] == 'warning'
                and row['count'] == '1'
                for row in missing_field_rows
            ))

            with (output_dir / 'metadata.json').open(encoding='utf-8') as handle:
                metadata = json.load(handle)

            expected_warning_count = sum(int(row['count']) for row in summary_rows if row['severity'] == 'warning')
            self.assertEqual(metadata['warning_issues'], expected_warning_count)
            self.assertGreaterEqual(metadata['warning_issues'], 1)

    @override_settings(TUSHARE_TOKEN='test-token')
    @patch('apps.core.management.commands.validate_data_quality.ts.pro_api')
    def test_validate_data_quality_can_sample_reconcile_fundamental_snapshots(self, mock_pro_api):
        matched_asset = Asset.objects.create(
            market=self.market_sse,
            symbol='600551',
            ts_code='600551.SH',
            name='Matched Fundamental Asset',
            list_date=self.d1,
        )
        mismatch_asset = Asset.objects.create(
            market=self.market_sse,
            symbol='600552',
            ts_code='600552.SH',
            name='Mismatch Fundamental Asset',
            list_date=self.d1,
        )

        self._ohlcv(matched_asset, self.d3, '13')
        self._ohlcv(mismatch_asset, self.d4, '14')
        self._complete_related_rows(matched_asset, self.d3)
        self._complete_related_rows(mismatch_asset, self.d4)

        FundamentalFactorSnapshot.objects.filter(asset=matched_asset, date=self.d3).update(
            pe=Decimal('8.5'),
            pe_ttm=Decimal('8.8'),
            pb=Decimal('1.1'),
            total_share=Decimal('100.0'),
            float_share=Decimal('60.0'),
            free_share=Decimal('55.0'),
            total_mv=Decimal('1050.0'),
            circ_mv=Decimal('577.5'),
            roe=Decimal('0.1'),
            roe_qoq=Decimal('0.02'),
            metadata={
                'daily_basic_trade_date': self.d3.isoformat(),
                'fina_indicator_ann_date': self.d2.isoformat(),
                'fina_indicator_end_date': '2023-12-31',
            },
        )
        FundamentalFactorSnapshot.objects.filter(asset=mismatch_asset, date=self.d4).update(
            pe=Decimal('9.0'),
            pe_ttm=Decimal('9.4'),
            pb=Decimal('9.9'),
            total_share=Decimal('100.0'),
            float_share=Decimal('62.0'),
            free_share=Decimal('57.0'),
            total_mv=Decimal('1100.0'),
            circ_mv=Decimal('627.0'),
            roe=Decimal('0.1'),
            roe_qoq=Decimal('0.02'),
            metadata={
                'daily_basic_trade_date': self.d4.isoformat(),
                'fina_indicator_ann_date': self.d2.isoformat(),
                'fina_indicator_end_date': '2023-12-31',
            },
        )

        class StubPro:
            def daily_basic(self, **kwargs):
                ts_code = kwargs['ts_code']
                if ts_code == matched_asset.ts_code:
                    return pd.DataFrame([
                        {'trade_date': '20240104', 'pe': 8.5, 'pe_ttm': 8.8, 'pb': 1.1, 'total_share': 100.0, 'float_share': 60.0, 'free_share': 55.0, 'total_mv': 1050.0, 'circ_mv': 577.5},
                    ])
                return pd.DataFrame([
                    {'trade_date': '20240105', 'pe': 9.0, 'pe_ttm': 9.4, 'pb': 1.2, 'total_share': 100.0, 'float_share': 62.0, 'free_share': 57.0, 'total_mv': 1100.0, 'circ_mv': 627.0},
                ])

            def fina_indicator(self, **kwargs):
                return pd.DataFrame([
                    {'ann_date': '20231030', 'end_date': '20230930', 'roe': 8.0},
                    {'ann_date': '20240103', 'end_date': '20231231', 'roe': 10.0},
                ])

        mock_pro_api.return_value = StubPro()

        with tempfile.TemporaryDirectory() as temp_dir:
            call_command(
                'validate_data_quality',
                start_date='2024-01-02',
                end_date='2024-01-05',
                output_dir=temp_dir,
                symbols=f'{matched_asset.ts_code},{mismatch_asset.ts_code}',
                fundamental_reconciliation_sample_size=10,
                fundamental_reconciliation_seed=1,
            )

            output_dir = Path(temp_dir)
            audit_rows = read_csv(output_dir / 'fundamental_reconciliation_audit.csv')
            self.assertEqual(len(audit_rows), 2)
            self.assertTrue(any(
                row['asset_ts_code'] == matched_asset.ts_code
                and row['audit_status'] == 'matched'
                and row['mismatch_fields'] == ''
                for row in audit_rows
            ))
            self.assertTrue(any(
                row['asset_ts_code'] == mismatch_asset.ts_code
                and row['audit_status'] == 'mismatch'
                and row['mismatch_fields'] == 'pb'
                and Decimal(row['stored_pb']) == Decimal('9.9')
                and Decimal(row['recomputed_pb']) == Decimal('1.2')
                for row in audit_rows
            ))

            summary_rows = read_csv(output_dir / 'summary.csv')
            self.assertTrue(any(
                row['issue_type'] == 'fundamental_reconciliation_mismatch'
                and row['severity'] == 'warning'
                and row['count'] == '1'
                for row in summary_rows
            ))

            missing_field_rows = read_csv(output_dir / 'missing_fields.csv')
            self.assertTrue(any(
                row['table'] == 'fundamental_factor_snapshot'
                and row['field'] == 'pb'
                and row['issue_type'] == 'fundamental_reconciliation_mismatch'
                and row['count'] == '1'
                for row in missing_field_rows
            ))

            affected_rows = read_csv(output_dir / 'affected_asset_dates.csv')
            self.assertTrue(any(
                row['asset_ts_code'] == mismatch_asset.ts_code
                and row['field'] == 'pb'
                and row['issue_type'] == 'fundamental_reconciliation_mismatch'
                for row in affected_rows
            ))

            with (output_dir / 'metadata.json').open(encoding='utf-8') as handle:
                metadata = json.load(handle)
            self.assertIn('fundamental_reconciliation_audit.csv', metadata['report_descriptions'])

    def test_validate_data_quality_suppresses_intended_null_rs_score_gaps(self):
        trade_dates = [timezone.datetime(2024, 2, 1).date() + timezone.timedelta(days=offset) for offset in range(21)]
        target_date = trade_dates[-1]
        anchor_date = trade_dates[0]

        for exchange_code in ('SSE', 'SZSE'):
            previous_trade_date = None
            for trade_date in trade_dates:
                ExchangeTradingCalendar.objects.create(
                    exchange_code=exchange_code,
                    trade_date=trade_date,
                    previous_trade_date=previous_trade_date,
                )
                previous_trade_date = trade_date

        MacroSnapshot.objects.create(
            date=timezone.datetime(2024, 2, 1).date(),
            dxy=Decimal('100.0'),
            cny_usd=Decimal('0.1400'),
            cn6m_yield=Decimal('2.1000'),
            cn1y_yield=Decimal('2.1500'),
            cn3y_yield=Decimal('2.3000'),
            cn5y_yield=Decimal('2.4000'),
            cn7y_yield=Decimal('2.4500'),
            cn10y_yield=Decimal('2.5000'),
            cn30y_yield=Decimal('3.1000'),
            pmi_manufacturing=Decimal('50.0'),
            pmi_non_manufacturing=Decimal('51.0'),
            cpi_yoy=Decimal('1.000'),
            ppi_yoy=Decimal('-0.500'),
        )
        MarketContext.objects.create(
            context_key='current',
            macro_phase=MarketContext.MacroPhase.RECOVERY,
            starts_at=timezone.datetime(2024, 2, 1).date(),
            ends_at=timezone.datetime(2024, 2, 29).date(),
            is_active=True,
        )

        pre_listing_asset = Asset.objects.create(
            market=self.market_sse,
            symbol='600888',
            ts_code='600888.SH',
            name='Pre Listing RS Asset',
            list_date=trade_dates[10],
        )
        suspended_anchor_asset = Asset.objects.create(
            market=self.market_sse,
            symbol='600889',
            ts_code='600889.SH',
            name='Suspended Anchor RS Asset',
            list_date=trade_dates[0],
        )
        control_asset = Asset.objects.create(
            market=self.market_sse,
            symbol='600890',
            ts_code='600890.SH',
            name='Control RS Asset',
            list_date=trade_dates[0],
        )

        for asset in (pre_listing_asset, suspended_anchor_asset, control_asset):
            OHLCV.objects.create(
                asset=asset,
                date=target_date,
                open=Decimal('10.0'),
                high=Decimal('10.5'),
                low=Decimal('9.5'),
                close=Decimal('10.2'),
                adj_close=Decimal('10.2'),
                volume=1000000,
                amount=Decimal('10200000'),
            )
            self._complete_related_rows(asset, target_date, include_rs_score=False)

        AssetSuspension.objects.create(
            asset=suspended_anchor_asset,
            trade_date=anchor_date,
            suspend_type='S',
            suspend_timing=None,
            is_full_day=True,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            call_command(
                'validate_data_quality',
                start_date=trade_dates[0].isoformat(),
                end_date=target_date.isoformat(),
                output_dir=temp_dir,
            )

            cross_rows = read_csv(Path(temp_dir) / 'feature_dependency_gaps.csv')
            rs_rows = [
                row for row in cross_rows
                if row['issue_type'] == 'missing_technical_indicator' and row['field'] == 'RS_SCORE'
            ]

            self.assertFalse(any(
                row['asset_ts_code'] == pre_listing_asset.ts_code and row['date'] == target_date.isoformat()
                for row in rs_rows
            ))
            self.assertFalse(any(
                row['asset_ts_code'] == suspended_anchor_asset.ts_code and row['date'] == target_date.isoformat()
                for row in rs_rows
            ))
            self.assertTrue(any(
                row['asset_ts_code'] == control_asset.ts_code and row['date'] == target_date.isoformat()
                for row in rs_rows
            ))

    def test_validate_data_quality_reports_partial_month_membership_blanks(self):
        jan2010_d1 = timezone.datetime(2010, 1, 4).date()
        jan2010_d2 = timezone.datetime(2010, 1, 5).date()
        sep2024_d1 = timezone.datetime(2024, 9, 23).date()
        sep2024_d2 = timezone.datetime(2024, 9, 24).date()

        for exchange_code in ('SSE', 'SZSE'):
            ExchangeTradingCalendar.objects.bulk_create([
                ExchangeTradingCalendar(exchange_code=exchange_code, trade_date=jan2010_d1),
                ExchangeTradingCalendar(exchange_code=exchange_code, trade_date=jan2010_d2, previous_trade_date=jan2010_d1),
                ExchangeTradingCalendar(exchange_code=exchange_code, trade_date=sep2024_d1),
                ExchangeTradingCalendar(exchange_code=exchange_code, trade_date=sep2024_d2, previous_trade_date=sep2024_d1),
            ])

        IndexMembership.objects.create(
            asset=self.asset_old,
            index_code='000300.SH',
            index_name='CSI 300',
            trade_date=sep2024_d1,
            weight=Decimal('1.000000'),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            call_command(
                'validate_data_quality',
                start_date='2010-01-04',
                end_date='2024-09-24',
                output_dir=temp_dir,
            )

            output_dir = Path(temp_dir)
            membership_history_rows = read_csv(output_dir / 'index_membership_history_gaps.csv')
            self.assertTrue(any(
                row['index_code'] == '000300.SH'
                and row['gap_start'] == '2010-01-04'
                and row['gap_end'] == '2010-01-05'
                for row in membership_history_rows
            ))
            self.assertTrue(any(
                row['index_code'] == '000510.CSI'
                and row['gap_start'] == '2024-09-23'
                and row['gap_end'] == '2024-09-24'
                for row in membership_history_rows
            ))

            monthly_blank_rows = read_csv(output_dir / 'index_membership_monthly_blanks.csv')
            self.assertTrue(any(
                row['index_code'] == '000300.SH'
                and row['calendar_month'] == '2010-01'
                and row['first_expected_trade_date'] == '2010-01-04'
                and row['last_expected_trade_date'] == '2010-01-05'
                and row['actual_snapshot_count'] == '0'
                for row in monthly_blank_rows
            ))
            self.assertTrue(any(
                row['index_code'] == '000510.CSI'
                and row['calendar_month'] == '2024-09'
                and row['first_expected_trade_date'] == '2024-09-23'
                and row['last_expected_trade_date'] == '2024-09-24'
                and row['actual_snapshot_count'] == '0'
                for row in monthly_blank_rows
            ))
            self.assertFalse(any(
                row['index_code'] == '000300.SH'
                and row['calendar_month'] == '2024-09'
                for row in monthly_blank_rows
            ))

            benchmark_gap_rows = read_csv(output_dir / 'benchmark_index_daily_gaps.csv')
            self.assertTrue(any(
                row['index_code'] == '000300.SH'
                and row['gap_start'] == '2010-01-04'
                for row in benchmark_gap_rows
            ))

            pit_benchmark_rows = read_csv(output_dir / 'pit_benchmark_daily_gaps.csv')
            self.assertTrue(any(
                row['benchmark_code'] == PIT_UNION_BENCHMARK_CODE
                and row['gap_start'] == '2010-01-04'
                for row in pit_benchmark_rows
            ))

    def test_validate_data_quality_can_write_only_selected_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            call_command(
                'validate_data_quality',
                start_date='2024-01-02',
                end_date='2024-01-05',
                output_dir=temp_dir,
                only_report='ohlcv_continuity_gaps.csv',
            )

            output_dir = Path(temp_dir)
            self.assertTrue((output_dir / 'ohlcv_continuity_gaps.csv').exists())
            self.assertTrue((output_dir / 'metadata.json').exists())
            self.assertFalse((output_dir / 'summary.csv').exists())
            self.assertFalse((output_dir / 'feature_dependency_gaps.csv').exists())
            self.assertFalse((output_dir / 'ohlcv_excused_gaps.csv').exists())

            continuity_rows = read_csv(output_dir / 'ohlcv_continuity_gaps.csv')
            self.assertTrue(any(row['asset_ts_code'] == '300001.SZ' for row in continuity_rows))

            with (output_dir / 'metadata.json').open(encoding='utf-8') as handle:
                metadata = json.load(handle)
            self.assertEqual(
                metadata['report_descriptions'],
                {
                    'metadata.json': 'Run metadata, limitations, and report descriptions.',
                    'ohlcv_continuity_gaps.csv': 'Missing OHLCV on official exchange open days after listing and before delisting, excluding suspend_d-covered dates.',
                },
            )

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        DEFAULT_FROM_EMAIL='alerts@example.com',
        DATA_QUALITY_ALERT_EMAILS=['owner@example.com'],
    )
    def test_validate_data_quality_can_send_alert(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            call_command(
                'validate_data_quality',
                start_date='2024-01-02',
                end_date='2024-01-05',
                output_dir=temp_dir,
                alert=True,
            )

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['owner@example.com'])
        self.assertIn('Data quality validation found', mail.outbox[0].body)

    def test_validate_data_quality_can_fail_on_critical(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(CommandError):
                call_command(
                    'validate_data_quality',
                    start_date='2024-01-02',
                    end_date='2024-01-05',
                    output_dir=temp_dir,
                    fail_on_critical=True,
                )

    def test_validate_data_quality_can_scope_to_effective_universe(self):
        IndexMembership.objects.bulk_create([
            IndexMembership(
                asset=self.asset_old,
                index_code='000300.SH',
                index_name='CSI 300',
                trade_date=self.d3,
                weight=Decimal('1.000000'),
            ),
            IndexMembership(
                asset=self.asset_new,
                index_code='000300.SH',
                index_name='CSI 300',
                trade_date=self.d3,
                weight=Decimal('1.000000'),
            ),
        ])

        with tempfile.TemporaryDirectory() as temp_dir:
            call_command(
                'validate_data_quality',
                start_date='2024-01-02',
                end_date='2024-01-05',
                output_dir=temp_dir,
                effective_universe_only=True,
            )

            output_dir = Path(temp_dir)
            with (output_dir / 'metadata.json').open(encoding='utf-8') as handle:
                metadata = json.load(handle)
            self.assertEqual(metadata['asset_count'], 2)
            self.assertTrue(metadata['effective_universe_only'])

            summary_rows = read_csv(output_dir / 'summary.csv')
            self.assertFalse(any(row['issue_type'] == 'missing_ohlcv' for row in summary_rows))


class FundamentalReconciliationAuditRegressionTests(TestCase):
    def _create_calendar(self, market_code, trade_dates):
        previous_trade_date = None
        for trade_date in trade_dates:
            ExchangeTradingCalendar.objects.create(
                exchange_code=market_code,
                trade_date=trade_date,
                previous_trade_date=previous_trade_date,
            )
            previous_trade_date = trade_date

    def _create_ohlcv(self, asset, trade_date, close='10'):
        close_value = Decimal(close)
        OHLCV.objects.create(
            asset=asset,
            date=trade_date,
            open=close_value,
            high=close_value + Decimal('0.5'),
            low=close_value - Decimal('0.5'),
            close=close_value,
            adj_close=close_value,
            volume=1000000,
            amount=close_value * Decimal('1000000'),
        )

    @patch('apps.core.management.commands.validate_data_quality.ts.pro_api')
    def test_fundamental_reconciliation_looks_back_for_prior_disclosures(self, mock_pro_api):
        market = Market.objects.create(code='SSE', name='Shanghai Stock Exchange')
        asset = Asset.objects.create(
            market=market,
            symbol='600528',
            ts_code='600528.SH',
            name='Lookback Audit Asset',
            list_date=timezone.datetime(2000, 1, 1).date(),
        )
        trade_dates = [
            timezone.datetime(2010, 1, 4).date(),
            timezone.datetime(2010, 1, 5).date(),
            timezone.datetime(2010, 1, 6).date(),
            timezone.datetime(2010, 1, 7).date(),
        ]
        self._create_calendar('SSE', trade_dates)
        self._create_ohlcv(asset, trade_dates[-1])

        FundamentalFactorSnapshot.objects.create(
            asset=asset,
            date=trade_dates[-1],
            pe=Decimal('39.7972'),
            pe_ttm=Decimal('38.1200'),
            pb=Decimal('4.6495'),
            total_share=Decimal('145920.0'),
            float_share=Decimal('129280.0'),
            free_share=Decimal('70530.4651'),
            total_mv=Decimal('1834214.4'),
            circ_mv=Decimal('1625049.6'),
            roe=Decimal('0.112604'),
            roe_qoq=Decimal('0.039634'),
            metadata={
                'daily_basic_trade_date': '2010-01-07',
                'fina_indicator_ann_date': '2009-10-28',
                'fina_indicator_end_date': '2009-09-30',
            },
        )

        class StubPro:
            def daily_basic(self, **kwargs):
                frame = pd.DataFrame([
                    {
                        'trade_date': '20100107', 'pe': 39.7972, 'pe_ttm': 38.1200, 'pb': 4.6495,
                        'total_share': 145920.0, 'float_share': 129280.0, 'free_share': 70530.4651,
                        'total_mv': 1834214.4, 'circ_mv': 1625049.6,
                    },
                ])
                return frame[
                    (frame['trade_date'] >= kwargs['start_date']) &
                    (frame['trade_date'] <= kwargs['end_date'])
                ].reset_index(drop=True)

            def fina_indicator(self, **kwargs):
                frame = pd.DataFrame([
                    {'ann_date': '20090812', 'end_date': '20090630', 'roe': 7.2970},
                    {'ann_date': '20091028', 'end_date': '20090930', 'roe': 11.2604},
                    {'ann_date': '20100317', 'end_date': '20091231', 'roe': 17.0571},
                ])
                return frame[
                    (frame['end_date'] >= kwargs['start_date']) &
                    (frame['end_date'] <= kwargs['end_date'])
                ].reset_index(drop=True)

        mock_pro_api.return_value = StubPro()

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            'apps.core.management.commands.validate_data_quality.settings.TUSHARE_TOKEN',
            'test-token',
        ):
            call_command(
                'validate_data_quality',
                start_date='2010-01-04',
                end_date='2010-01-07',
                output_dir=temp_dir,
                symbols=asset.ts_code,
                only_report='fundamental_reconciliation_audit.csv',
                fundamental_reconciliation_sample_size=10,
                fundamental_reconciliation_seed=1,
            )

            audit_rows = read_csv(Path(temp_dir) / 'fundamental_reconciliation_audit.csv')
            self.assertEqual(len(audit_rows), 1)
            row = audit_rows[0]
            self.assertEqual(row['audit_status'], 'matched')
            self.assertEqual(Decimal(row['recomputed_pe_ttm']), Decimal('38.1200'))
            self.assertEqual(row['recomputed_fina_indicator_ann_date'], '2009-10-28')
            self.assertEqual(row['recomputed_fina_indicator_end_date'], '2009-09-30')
            self.assertEqual(Decimal(row['recomputed_roe']), Decimal('0.112604'))
            self.assertEqual(Decimal(row['recomputed_roe_qoq']), Decimal('0.039634'))

    @patch('apps.core.management.commands.validate_data_quality.ts.pro_api')
    def test_fundamental_reconciliation_prefers_latest_report_end_for_same_ann_date(self, mock_pro_api):
        market = Market.objects.create(code='SSE', name='Shanghai Stock Exchange')
        asset = Asset.objects.create(
            market=market,
            symbol='601006',
            ts_code='601006.SH',
            name='Same Announcement Audit Asset',
            list_date=timezone.datetime(2006, 1, 1).date(),
        )
        trade_date = timezone.datetime(2024, 7, 1).date()
        self._create_calendar('SSE', [trade_date])
        self._create_ohlcv(asset, trade_date)

        FundamentalFactorSnapshot.objects.create(
            asset=asset,
            date=trade_date,
            pe=Decimal('10.6016'),
            pe_ttm=Decimal('10.1024'),
            pb=Decimal('0.8408'),
            total_share=Decimal('1756621.5836'),
            float_share=Decimal('1756621.5836'),
            free_share=Decimal('824545.4316'),
            total_mv=Decimal('12647675.4019'),
            circ_mv=Decimal('12647675.4019'),
            roe=Decimal('0.021017'),
            roe_qoq=Decimal('-0.068667'),
            metadata={
                'daily_basic_trade_date': '2024-07-01',
                'fina_indicator_ann_date': '2024-04-27',
                'fina_indicator_end_date': '2024-03-31',
            },
        )

        class StubPro:
            def daily_basic(self, **kwargs):
                frame = pd.DataFrame([
                    {
                        'trade_date': '20240701', 'pe': 10.6016, 'pe_ttm': 10.1024, 'pb': 0.8408,
                        'total_share': 1756621.5836, 'float_share': 1756621.5836, 'free_share': 824545.4316,
                        'total_mv': 12647675.4019, 'circ_mv': 12647675.4019,
                    },
                ])
                return frame[
                    (frame['trade_date'] >= kwargs['start_date']) &
                    (frame['trade_date'] <= kwargs['end_date'])
                ].reset_index(drop=True)

            def fina_indicator(self, **kwargs):
                frame = pd.DataFrame([
                    {'ann_date': '20240427', 'end_date': '20231231', 'roe': 8.9684},
                    {'ann_date': '20240427', 'end_date': '20240331', 'roe': 2.1017},
                ])
                return frame[
                    (frame['end_date'] >= kwargs['start_date']) &
                    (frame['end_date'] <= kwargs['end_date'])
                ].reset_index(drop=True)

        mock_pro_api.return_value = StubPro()

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            'apps.core.management.commands.validate_data_quality.settings.TUSHARE_TOKEN',
            'test-token',
        ):
            call_command(
                'validate_data_quality',
                start_date='2024-07-01',
                end_date='2024-07-01',
                output_dir=temp_dir,
                symbols=asset.ts_code,
                only_report='fundamental_reconciliation_audit.csv',
                fundamental_reconciliation_sample_size=10,
                fundamental_reconciliation_seed=1,
            )

            audit_rows = read_csv(Path(temp_dir) / 'fundamental_reconciliation_audit.csv')
            self.assertEqual(len(audit_rows), 1)
            row = audit_rows[0]
            self.assertEqual(row['audit_status'], 'matched')
            self.assertEqual(Decimal(row['recomputed_pe_ttm']), Decimal('10.1024'))
            self.assertEqual(row['recomputed_fina_indicator_ann_date'], '2024-04-27')
            self.assertEqual(row['recomputed_fina_indicator_end_date'], '2024-03-31')
            self.assertEqual(Decimal(row['recomputed_roe']), Decimal('0.021017'))
            self.assertEqual(Decimal(row['recomputed_roe_qoq']), Decimal('-0.068667'))

    @patch('apps.core.management.commands.validate_data_quality.ts.pro_api')
    def test_fundamental_reconciliation_rounds_recomputed_values_to_storage_precision(self, mock_pro_api):
        market = Market.objects.create(code='SSE', name='Shanghai Stock Exchange')
        asset = Asset.objects.create(
            market=market,
            symbol='600299',
            ts_code='600299.SH',
            name='Rounded Audit Asset',
            list_date=timezone.datetime(2004, 1, 1).date(),
        )
        trade_date = timezone.datetime(2015, 11, 13).date()
        self._create_calendar('SSE', [trade_date])
        self._create_ohlcv(asset, trade_date)

        FundamentalFactorSnapshot.objects.create(
            asset=asset,
            date=trade_date,
            pe=Decimal('222.3770'),
            pe_ttm=Decimal('180.1234'),
            pb=Decimal('2.9196'),
            total_share=Decimal('268190.1273'),
            float_share=Decimal('52270.7560'),
            free_share=Decimal('24066.2262'),
            total_mv=Decimal('5414758.6702'),
            circ_mv=Decimal('1055346.5636'),
            roe=Decimal('0.239210'),
            roe_qoq=Decimal('0.250852'),
            metadata={
                'daily_basic_trade_date': '2015-11-13',
                'fina_indicator_ann_date': '2015-10-24',
                'fina_indicator_end_date': '2015-09-30',
            },
        )

        class StubPro:
            def daily_basic(self, **kwargs):
                frame = pd.DataFrame([
                    {
                        'trade_date': '20151113', 'pe': 222.3770, 'pe_ttm': 180.1234321, 'pb': 2.9196,
                        'total_share': 268190.1273, 'float_share': 52270.7560, 'free_share': 24066.2262,
                        'total_mv': 5414758.6702, 'circ_mv': 1055346.5636,
                    },
                ])
                return frame[
                    (frame['trade_date'] >= kwargs['start_date']) &
                    (frame['trade_date'] <= kwargs['end_date'])
                ].reset_index(drop=True)

            def fina_indicator(self, **kwargs):
                frame = pd.DataFrame([
                    {'ann_date': '20150827', 'end_date': '20150630', 'roe': -116.4214},
                    {'ann_date': '20151024', 'end_date': '20150930', 'roe': 23.9210},
                ])
                return frame[
                    (frame['end_date'] >= kwargs['start_date']) &
                    (frame['end_date'] <= kwargs['end_date'])
                ].reset_index(drop=True)

        mock_pro_api.return_value = StubPro()

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            'apps.core.management.commands.validate_data_quality.settings.TUSHARE_TOKEN',
            'test-token',
        ):
            call_command(
                'validate_data_quality',
                start_date='2015-11-13',
                end_date='2015-11-13',
                output_dir=temp_dir,
                symbols=asset.ts_code,
                only_report='fundamental_reconciliation_audit.csv',
                fundamental_reconciliation_sample_size=10,
                fundamental_reconciliation_seed=1,
            )

            audit_rows = read_csv(Path(temp_dir) / 'fundamental_reconciliation_audit.csv')
            self.assertEqual(len(audit_rows), 1)
            row = audit_rows[0]
            self.assertEqual(row['audit_status'], 'matched')
            self.assertEqual(Decimal(row['recomputed_roe']), Decimal('0.239210'))
            self.assertEqual(Decimal(row['recomputed_roe_qoq']), Decimal('0.250852'))


class TechnicalIndicatorValidationRegressionTests(TestCase):
    def _create_calendar(self, market_code, trade_dates):
        previous_trade_date = None
        for trade_date in trade_dates:
            ExchangeTradingCalendar.objects.create(
                exchange_code=market_code,
                trade_date=trade_date,
                previous_trade_date=previous_trade_date,
            )
            previous_trade_date = trade_date

    def _create_ohlcv_series(self, asset, trade_dates, start_close='10'):
        base_close = Decimal(start_close)
        for index, trade_date in enumerate(trade_dates):
            close_value = base_close + (Decimal(index) * Decimal('0.2'))
            OHLCV.objects.create(
                asset=asset,
                date=trade_date,
                open=close_value,
                high=close_value + Decimal('0.5'),
                low=close_value - Decimal('0.5'),
                close=close_value,
                adj_close=close_value,
                volume=1000000 + index,
                amount=close_value * Decimal('1000000'),
            )

    def _timestamp(self, trade_date):
        return timezone.make_aware(timezone.datetime.combine(trade_date, timezone.datetime.min.time()))

    def _build_indicator_rows(self, asset, start_date, end_date, indicator_types):
        command = TechnicalIndicatorBackfillCommand()
        df = command._load_ohlcv_df(asset.id, end_date)
        return command._build_rows(asset, df, start_date, end_date, indicator_types)

    def test_technical_indicator_continuity_report_flags_missing_rsi_rows(self):
        market = Market.objects.create(code='SSE', name='Shanghai Stock Exchange')
        asset = Asset.objects.create(
            market=market,
            symbol='600188',
            ts_code='600188.SH',
            name='Technical Continuity Asset',
            list_date=timezone.datetime(2024, 1, 2).date(),
        )
        trade_dates = [timezone.datetime(2024, 1, day).date() for day in (2, 3, 4)]
        self._create_calendar('SSE', trade_dates)
        self._create_ohlcv_series(asset, trade_dates)

        for trade_date in (trade_dates[0], trade_dates[2]):
            TechnicalIndicator.objects.create(
                asset=asset,
                timestamp=self._timestamp(trade_date),
                indicator_type='RSI',
                value=Decimal('55.00000000'),
                parameters={'timeperiod': 14},
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            call_command(
                'validate_data_quality',
                start_date=trade_dates[0].isoformat(),
                end_date=trade_dates[-1].isoformat(),
                output_dir=temp_dir,
                symbols=asset.ts_code,
                technical_indicators='RSI',
                only_report='technical_indicator_snapshot_continuity_gaps.csv',
            )

            rows = read_csv(Path(temp_dir) / 'technical_indicator_snapshot_continuity_gaps.csv')
            continuity_rows = [row for row in rows if row['issue_type'] == 'continuity_gap']
            self.assertEqual(len(continuity_rows), 1)
            row = continuity_rows[0]
            self.assertEqual(row['field'], 'RSI[timeperiod=14]')
            self.assertEqual(row['gap_start'], trade_dates[1].isoformat())
            self.assertEqual(row['gap_end'], trade_dates[1].isoformat())
            self.assertEqual(row['gap_missing_count'], '1')
            self.assertEqual(row['missing_count'], '1')
            self.assertEqual(row['snapshot_row_count'], '2')

    def test_technical_indicator_continuity_report_flags_out_of_range_rsi(self):
        market = Market.objects.create(code='SSE', name='Shanghai Stock Exchange')
        trade_date = timezone.datetime(2024, 2, 5).date()
        asset = Asset.objects.create(
            market=market,
            symbol='600199',
            ts_code='600199.SH',
            name='Technical Anomaly Asset',
            list_date=trade_date,
        )
        self._create_calendar('SSE', [trade_date])
        self._create_ohlcv_series(asset, [trade_date])
        TechnicalIndicator.objects.create(
            asset=asset,
            timestamp=self._timestamp(trade_date),
            indicator_type='RSI',
            value=Decimal('120.00000000'),
            parameters={'timeperiod': 14},
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            call_command(
                'validate_data_quality',
                start_date=trade_date.isoformat(),
                end_date=trade_date.isoformat(),
                output_dir=temp_dir,
                symbols=asset.ts_code,
                technical_indicators='RSI',
                only_report='technical_indicator_snapshot_continuity_gaps.csv',
            )

            rows = read_csv(Path(temp_dir) / 'technical_indicator_snapshot_continuity_gaps.csv')
            anomaly_rows = [row for row in rows if row['issue_type'] == 'value_out_of_range']
            self.assertEqual(len(anomaly_rows), 1)
            row = anomaly_rows[0]
            self.assertEqual(row['field'], 'RSI[timeperiod=14]')
            self.assertEqual(row['date'], trade_date.isoformat())
            self.assertEqual(Decimal(row['value']), Decimal('120.00000000'))
            self.assertEqual(Decimal(row['expected_min_value']), Decimal('0'))
            self.assertEqual(Decimal(row['expected_max_value']), Decimal('100'))

    def test_technical_indicator_reconciliation_matches_recomputed_rsi(self):
        market = Market.objects.create(code='SSE', name='Shanghai Stock Exchange')
        asset = Asset.objects.create(
            market=market,
            symbol='600211',
            ts_code='600211.SH',
            name='Technical Match Asset',
            list_date=timezone.datetime(2024, 1, 2).date(),
        )
        trade_dates = [timezone.datetime(2024, 1, 2).date() + datetime.timedelta(days=index) for index in range(30)]
        self._create_calendar('SSE', trade_dates)
        self._create_ohlcv_series(asset, trade_dates)

        rsi_rows = self._build_indicator_rows(asset, trade_dates[0], trade_dates[-1], ('RSI',))
        stored_row = next(row for row in reversed(rsi_rows) if row.timestamp.date() == trade_dates[-1])
        TechnicalIndicator.objects.create(
            asset=asset,
            timestamp=stored_row.timestamp,
            indicator_type=stored_row.indicator_type,
            value=stored_row.value,
            parameters=stored_row.parameters,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            call_command(
                'validate_data_quality',
                start_date=trade_dates[-1].isoformat(),
                end_date=trade_dates[-1].isoformat(),
                output_dir=temp_dir,
                symbols=asset.ts_code,
                technical_indicators='RSI',
                only_report='technical_indicator_reconciliation_audit.csv',
                technical_indicator_reconciliation_sample_size=10,
                technical_indicator_reconciliation_seed=1,
            )

            rows = read_csv(Path(temp_dir) / 'technical_indicator_reconciliation_audit.csv')
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row['audit_status'], 'matched')
            self.assertEqual(row['indicator_type'], 'RSI')
            self.assertEqual(Decimal(row['stored_value']), stored_row.value)
            self.assertEqual(Decimal(row['recomputed_value']), stored_row.value)

    def test_technical_indicator_reconciliation_reports_rsi_mismatch(self):
        market = Market.objects.create(code='SSE', name='Shanghai Stock Exchange')
        asset = Asset.objects.create(
            market=market,
            symbol='600233',
            ts_code='600233.SH',
            name='Technical Mismatch Asset',
            list_date=timezone.datetime(2024, 1, 2).date(),
        )
        trade_dates = [timezone.datetime(2024, 1, 2).date() + datetime.timedelta(days=index) for index in range(30)]
        self._create_calendar('SSE', trade_dates)
        self._create_ohlcv_series(asset, trade_dates)

        rsi_rows = self._build_indicator_rows(asset, trade_dates[0], trade_dates[-1], ('RSI',))
        stored_row = next(row for row in reversed(rsi_rows) if row.timestamp.date() == trade_dates[-1])
        TechnicalIndicator.objects.create(
            asset=asset,
            timestamp=stored_row.timestamp,
            indicator_type=stored_row.indicator_type,
            value=stored_row.value + Decimal('1.00000000'),
            parameters=stored_row.parameters,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            call_command(
                'validate_data_quality',
                start_date=trade_dates[-1].isoformat(),
                end_date=trade_dates[-1].isoformat(),
                output_dir=temp_dir,
                symbols=asset.ts_code,
                technical_indicators='RSI',
                only_report='technical_indicator_reconciliation_audit.csv',
                technical_indicator_reconciliation_sample_size=10,
                technical_indicator_reconciliation_seed=1,
            )

            rows = read_csv(Path(temp_dir) / 'technical_indicator_reconciliation_audit.csv')
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row['audit_status'], 'mismatch')
            self.assertEqual(row['indicator_type'], 'RSI')
            self.assertEqual(Decimal(row['recomputed_value']), stored_row.value)
            self.assertNotEqual(Decimal(row['stored_value']), Decimal(row['recomputed_value']))


class PurgePreFloorHistoricalDataCommandTests(TestCase):
    def setUp(self):
        self.market = Market.objects.create(code='PURGE', name='Purge Test Market')
        self.asset = Asset.objects.create(
            market=self.market,
            symbol='600999',
            ts_code='600999.SH',
            name='Purge Asset',
            list_date=timezone.datetime(2008, 1, 1).date(),
        )
        self.old_date = timezone.datetime(2009, 12, 31).date()
        self.new_date = timezone.datetime(2010, 1, 4).date()
        self.old_timestamp = timezone.make_aware(timezone.datetime.combine(self.old_date, timezone.datetime.min.time()))
        self.new_timestamp = timezone.make_aware(timezone.datetime.combine(self.new_date, timezone.datetime.min.time()))

        self._seed_market_rows(self.old_date, self.new_date)
        self._seed_analytics_rows(self.old_timestamp, self.new_timestamp)
        self._seed_factor_rows(self.old_date, self.new_date)
        self._seed_prediction_rows(self.old_date, self.new_date)
        self._seed_backtest_rows(self.old_date, self.new_date)
        self._seed_macro_rows(self.old_date, self.new_date)
        self._seed_sentiment_rows(self.old_date, self.new_date)

    def _seed_market_rows(self, old_date, new_date):
        for trade_date, close_value in ((old_date, Decimal('9.5')), (new_date, Decimal('10.5'))):
            OHLCV.objects.create(
                asset=self.asset,
                date=trade_date,
                open=close_value,
                high=close_value + Decimal('0.2'),
                low=close_value - Decimal('0.2'),
                close=close_value,
                adj_close=close_value,
                volume=100000,
                amount=close_value * Decimal('100000'),
            )
            IndexMembership.objects.create(
                asset=self.asset,
                index_code='000300.SH',
                index_name='CSI 300',
                trade_date=trade_date,
                weight=Decimal('0.010000'),
            )
            BenchmarkIndexDaily.objects.create(
                index_code='000300.SH',
                index_name='CSI 300',
                trade_date=trade_date,
                open=Decimal('4000.0'),
                high=Decimal('4010.0'),
                low=Decimal('3990.0'),
                close=Decimal('4005.0'),
            )

    def _seed_analytics_rows(self, old_timestamp, new_timestamp):
        for timestamp in (old_timestamp, new_timestamp):
            TechnicalIndicator.objects.create(
                asset=self.asset,
                timestamp=timestamp,
                indicator_type='RS_SCORE',
                value=Decimal('0.70000000'),
                parameters={},
            )
            SignalEvent.objects.create(
                asset=self.asset,
                timestamp=timestamp,
                signal_type='HIGH_RS_SCORE',
                description='purge-test',
                metadata={},
            )

    def _seed_factor_rows(self, old_date, new_date):
        for trade_date in (old_date, new_date):
            FundamentalFactorSnapshot.objects.create(
                asset=self.asset,
                date=trade_date,
                pe=Decimal('10'),
                pe_ttm=Decimal('9.8'),
                pb=Decimal('1.5'),
                roe=Decimal('0.1'),
                roe_qoq=Decimal('0.01'),
            )
            AssetMoneyFlowSnapshot.objects.create(
                asset=self.asset,
                date=trade_date,
                net_mf_amount=Decimal('1000.0'),
            )
            AssetMarginDetailSnapshot.objects.create(
                asset=self.asset,
                date=trade_date,
                rzye=Decimal('5000.0'),
            )
            CapitalFlowSnapshot.objects.create(
                asset=self.asset,
                date=trade_date,
                main_force_net_5d=Decimal('100000'),
                margin_balance_change_5d=Decimal('200000'),
            )
            FactorScore.objects.create(
                asset=self.asset,
                date=trade_date,
                mode=FactorScore.FactorMode.COMPOSITE,
                technical_reversal_score=Decimal('0.3'),
                sentiment_score=Decimal('0.5'),
                fundamental_score=Decimal('0.5'),
                capital_flow_score=Decimal('0.5'),
                technical_score=Decimal('0.3'),
                composite_score=Decimal('0.5'),
                bottom_probability_score=Decimal('0.5'),
            )

    def _seed_prediction_rows(self, old_date, new_date):
        for trade_date in (old_date, new_date):
            PredictionResult.objects.create(
                asset=self.asset,
                date=trade_date,
                horizon_days=3,
                up_probability=Decimal('0.4'),
                flat_probability=Decimal('0.3'),
                down_probability=Decimal('0.3'),
                confidence=Decimal('0.4'),
                predicted_label=PredictionResult.Label.UP,
            )
            LightGBMPrediction.objects.create(
                asset=self.asset,
                date=trade_date,
                horizon_days=3,
                up_probability=Decimal('0.4'),
                flat_probability=Decimal('0.3'),
                down_probability=Decimal('0.3'),
                predicted_label=LightGBMPrediction.Label.UP,
                confidence=Decimal('0.4'),
            )
            EnsembleWeightSnapshot.objects.create(
                date=trade_date,
                lightgbm_weight=Decimal('0.4'),
                lstm_weight=Decimal('0.3'),
                heuristic_weight=Decimal('0.3'),
            )

    def _seed_backtest_rows(self, old_date, new_date):
        old_run = BacktestRun.objects.create(
            name='old-run',
            start_date=old_date,
            end_date=old_date,
        )
        new_run = BacktestRun.objects.create(
            name='new-run',
            start_date=new_date,
            end_date=new_date,
        )
        BacktestTrade.objects.create(
            backtest_run=old_run,
            asset=self.asset,
            trade_date=old_date,
            side=BacktestTrade.Side.BUY,
            quantity=Decimal('100'),
            price=Decimal('9.5'),
            amount=Decimal('950'),
        )
        BacktestTrade.objects.create(
            backtest_run=new_run,
            asset=self.asset,
            trade_date=new_date,
            side=BacktestTrade.Side.BUY,
            quantity=Decimal('100'),
            price=Decimal('10.5'),
            amount=Decimal('1050'),
        )

    def _seed_macro_rows(self, old_date, new_date):
        MacroSnapshot.objects.create(date=old_date, pmi_manufacturing=Decimal('50.0'))
        MacroSnapshot.objects.create(date=new_date, pmi_manufacturing=Decimal('51.0'))
        MarketContext.objects.create(
            context_key='current',
            macro_phase=MarketContext.MacroPhase.RECOVERY,
            starts_at=old_date,
            ends_at=old_date,
            is_active=True,
        )
        MarketContext.objects.create(
            context_key='current',
            macro_phase=MarketContext.MacroPhase.RECOVERY,
            starts_at=new_date,
            ends_at=new_date,
            is_active=True,
        )
        EventImpactStat.objects.create(
            event_tag='old-policy',
            sector='bank',
            horizon_days=20,
            avg_return=Decimal('0.01'),
            excess_return=Decimal('0.00'),
            sample_size=1,
            observations_start=old_date,
            observations_end=old_date,
        )
        EventImpactStat.objects.create(
            event_tag='new-policy',
            sector='bank',
            horizon_days=20,
            avg_return=Decimal('0.02'),
            excess_return=Decimal('0.01'),
            sample_size=1,
            observations_start=new_date,
            observations_end=new_date,
        )

    def _seed_sentiment_rows(self, old_date, new_date):
        old_article = NewsArticle.objects.create(
            source=NewsArticle.Source.OTHER,
            title='Old article',
            url='https://example.com/old-article',
            published_at=self.old_timestamp,
        )
        new_article = NewsArticle.objects.create(
            source=NewsArticle.Source.OTHER,
            title='New article',
            url='https://example.com/new-article',
            published_at=self.new_timestamp,
        )
        for article, trade_date in ((old_article, old_date), (new_article, new_date)):
            SentimentScore.objects.create(
                article=article,
                asset=self.asset,
                date=trade_date,
                score_type=SentimentScore.ScoreType.ARTICLE,
                positive_score=Decimal('0.2'),
                neutral_score=Decimal('0.6'),
                negative_score=Decimal('0.2'),
                sentiment_score=Decimal('0.0'),
                sentiment_label=SentimentScore.Label.NEUTRAL,
            )
            ConceptHeat.objects.create(
                concept_name=f'concept-{trade_date.isoformat()}',
                date=trade_date,
                heat_score=Decimal('1.0'),
            )

    def test_purge_pre_floor_historical_data_reports_dry_run_counts(self):
        output = StringIO()
        call_command('purge_pre_floor_historical_data', stdout=output)

        payload = json.loads(output.getvalue())
        by_label = {row['label']: row['candidate_rows'] for row in payload['results']}

        self.assertFalse(payload['execute'])
        self.assertEqual(payload['before_date'], '2010-01-01')
        self.assertEqual(payload['configured_floor_date'], '2010-01-01')
        self.assertEqual(by_label['ohlcv'], 1)
        self.assertEqual(by_label['technical_indicators'], 1)
        self.assertEqual(by_label['factor_scores'], 1)
        self.assertEqual(by_label['prediction_results'], 1)
        self.assertEqual(by_label['backtest_runs'], 1)
        self.assertEqual(by_label['macro_snapshots'], 1)
        self.assertEqual(by_label['news_articles'], 1)

    def test_purge_pre_floor_historical_data_deletes_only_pre_floor_rows(self):
        output = StringIO()
        call_command('purge_pre_floor_historical_data', execute=True, stdout=output)

        payload = json.loads(output.getvalue())
        self.assertTrue(payload['execute'])
        self.assertGreater(payload['total_deleted_rows'], 0)

        self.assertFalse(OHLCV.objects.filter(date=self.old_date).exists())
        self.assertTrue(OHLCV.objects.filter(date=self.new_date).exists())
        self.assertFalse(TechnicalIndicator.objects.filter(timestamp=self.old_timestamp).exists())
        self.assertTrue(TechnicalIndicator.objects.filter(timestamp=self.new_timestamp).exists())
        self.assertFalse(FactorScore.objects.filter(date=self.old_date).exists())
        self.assertTrue(FactorScore.objects.filter(date=self.new_date).exists())
        self.assertFalse(PredictionResult.objects.filter(date=self.old_date).exists())
        self.assertTrue(PredictionResult.objects.filter(date=self.new_date).exists())
        self.assertFalse(LightGBMPrediction.objects.filter(date=self.old_date).exists())
        self.assertTrue(LightGBMPrediction.objects.filter(date=self.new_date).exists())
        self.assertFalse(EnsembleWeightSnapshot.objects.filter(date=self.old_date).exists())
        self.assertTrue(EnsembleWeightSnapshot.objects.filter(date=self.new_date).exists())
        self.assertFalse(BacktestRun.objects.filter(start_date=self.old_date).exists())
        self.assertTrue(BacktestRun.objects.filter(start_date=self.new_date).exists())
        self.assertFalse(BacktestTrade.objects.filter(trade_date=self.old_date).exists())
        self.assertTrue(BacktestTrade.objects.filter(trade_date=self.new_date).exists())
        self.assertFalse(MacroSnapshot.objects.filter(date=self.old_date).exists())
        self.assertTrue(MacroSnapshot.objects.filter(date=self.new_date).exists())
        self.assertFalse(MarketContext.objects.filter(starts_at=self.old_date).exists())
        self.assertTrue(MarketContext.objects.filter(starts_at=self.new_date).exists())
        self.assertFalse(EventImpactStat.objects.filter(event_tag='old-policy').exists())
        self.assertTrue(EventImpactStat.objects.filter(event_tag='new-policy').exists())
        self.assertFalse(IndexMembership.objects.filter(trade_date=self.old_date).exists())
        self.assertTrue(IndexMembership.objects.filter(trade_date=self.new_date).exists())
        self.assertFalse(BenchmarkIndexDaily.objects.filter(trade_date=self.old_date).exists())
        self.assertTrue(BenchmarkIndexDaily.objects.filter(trade_date=self.new_date).exists())
        self.assertFalse(NewsArticle.objects.filter(published_at=self.old_timestamp).exists())
        self.assertTrue(NewsArticle.objects.filter(published_at=self.new_timestamp).exists())
        self.assertFalse(SentimentScore.objects.filter(date=self.old_date).exists())
        self.assertTrue(SentimentScore.objects.filter(date=self.new_date).exists())
        self.assertFalse(ConceptHeat.objects.filter(date=self.old_date).exists())
        self.assertTrue(ConceptHeat.objects.filter(date=self.new_date).exists())