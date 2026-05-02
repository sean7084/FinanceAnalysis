import csv
import json
import tempfile
from io import StringIO
from decimal import Decimal
from pathlib import Path

from django.core import mail
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.analytics.models import SignalEvent, TechnicalIndicator
from apps.backtest.models import BacktestRun, BacktestTrade
from apps.factors.models import AssetMarginDetailSnapshot, AssetMoneyFlowSnapshot, CapitalFlowSnapshot, FactorScore, FundamentalFactorSnapshot
from apps.macro.models import EventImpactStat, MacroSnapshot, MarketContext
from apps.markets.models import Asset, BenchmarkIndexDaily, IndexMembership, Market, OHLCV
from apps.prediction.models import PredictionResult
from apps.prediction.models_lightgbm import EnsembleWeightSnapshot, LightGBMPrediction
from apps.sentiment.models import ConceptHeat, NewsArticle, SentimentScore


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
            self.assertEqual(metadata['global_floor_date'], '2010-01-01')
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