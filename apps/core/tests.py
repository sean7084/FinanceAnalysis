import csv
import json
import tempfile
from decimal import Decimal
from pathlib import Path

from django.core import mail
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.analytics.models import TechnicalIndicator
from apps.factors.models import CapitalFlowSnapshot, FactorScore, FundamentalFactorSnapshot
from apps.macro.models import MacroSnapshot, MarketContext
from apps.markets.models import Asset, Market, OHLCV
from apps.sentiment.models import SentimentScore


def read_csv(path):
    with Path(path).open(newline='', encoding='utf-8') as handle:
        return list(csv.DictReader(handle))


class DataQualityValidationCommandTests(TestCase):
    def setUp(self):
        self.market = Market.objects.create(code='DQV', name='Data Quality Validation Market')
        self.asset_old = Asset.objects.create(
            market=self.market,
            symbol='600001',
            ts_code='600001.SH',
            name='Old Asset',
            list_date=timezone.datetime(2000, 1, 1).date(),
        )
        self.asset_new = Asset.objects.create(
            market=self.market,
            symbol='001391',
            ts_code='001391.SZ',
            name='New Asset',
            list_date=timezone.datetime(2024, 1, 3).date(),
        )
        self.d1 = timezone.datetime(2024, 1, 2).date()
        self.d2 = timezone.datetime(2024, 1, 3).date()
        self.d3 = timezone.datetime(2024, 1, 4).date()

        self._ohlcv(self.asset_old, self.d1, '10')
        self._ohlcv(self.asset_old, self.d3, '10.2')
        self._ohlcv(self.asset_new, self.d2, '20')
        self._ohlcv(self.asset_new, self.d3, '20.2')

        self._complete_related_rows(self.asset_old, self.d1)
        self._complete_related_rows(self.asset_new, self.d2)

        MacroSnapshot.objects.create(
            date=timezone.datetime(2024, 1, 1).date(),
            pmi_manufacturing=Decimal('50.0'),
            pmi_non_manufacturing=Decimal('51.0'),
            cn10y_yield=Decimal('2.5'),
            cn2y_yield=Decimal('2.0'),
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

    def _complete_related_rows(self, asset, trade_date):
        timestamp = timezone.make_aware(timezone.datetime.combine(trade_date, timezone.datetime.min.time()))
        TechnicalIndicator.objects.create(
            asset=asset,
            timestamp=timestamp,
            indicator_type='RS_SCORE',
            value=Decimal('0.70000000'),
            parameters={},
        )
        FundamentalFactorSnapshot.objects.create(
            asset=asset,
            date=trade_date,
            pe=Decimal('10'),
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
            pe_percentile_score=Decimal('0.400000'),
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

    def test_validate_data_quality_writes_actionable_reports(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            call_command(
                'validate_data_quality',
                start_date='2024-01-02',
                end_date='2024-01-04',
                output_dir=temp_dir,
            )

            output_dir = Path(temp_dir)
            for filename in [
                'summary.csv',
                'missing_by_table.csv',
                'missing_fields.csv',
                'affected_asset_dates.csv',
                'continuity_gaps.csv',
                'cross_table_gaps.csv',
                'null_reason_buckets.csv',
                'metadata.json',
            ]:
                self.assertTrue((output_dir / filename).exists(), filename)

            continuity_rows = read_csv(output_dir / 'continuity_gaps.csv')
            self.assertEqual(len(continuity_rows), 1)
            self.assertEqual(continuity_rows[0]['asset_ts_code'], '600001.SH')
            self.assertEqual(continuity_rows[0]['gap_start'], '2024-01-03')
            self.assertEqual(continuity_rows[0]['gap_missing_count'], '1')

            cross_rows = read_csv(output_dir / 'cross_table_gaps.csv')
            missing_factor_rows = [row for row in cross_rows if row['issue_type'] == 'missing_factor_score']
            self.assertEqual(len(missing_factor_rows), 2)
            self.assertEqual({row['asset_ts_code'] for row in missing_factor_rows}, {'600001.SH', '001391.SZ'})
            missing_indicator_rows = [row for row in cross_rows if row['issue_type'] == 'missing_technical_indicator']
            self.assertEqual(len(missing_indicator_rows), 2)

            missing_field_rows = read_csv(output_dir / 'missing_fields.csv')
            neutral_default_rows = [
                row for row in missing_field_rows
                if row['issue_type'] == 'neutral_default_value' and row['table'] == 'factor_score'
            ]
            self.assertTrue(neutral_default_rows)

            with (output_dir / 'metadata.json').open(encoding='utf-8') as handle:
                metadata = json.load(handle)
            self.assertEqual(metadata['global_floor_date'], '2001-01-01')
            self.assertEqual(metadata['asset_count'], 2)
            self.assertEqual(metadata['technical_indicators'], ['RS_SCORE'])
            self.assertGreater(metadata['critical_issues'], 0)

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
                end_date='2024-01-04',
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
                    end_date='2024-01-04',
                    output_dir=temp_dir,
                    fail_on_critical=True,
                )