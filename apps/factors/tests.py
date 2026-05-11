from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import pandas as pd
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.analytics.models import SignalEvent, TechnicalIndicator
from apps.markets.benchmarking import PITMembershipCoverageError
from apps.markets.models import Market, Asset, IndexMembership, OHLCV
from apps.prediction.models import ModelVersion, PredictionResult
from .fundamental_materialization import materialize_fundamental_snapshot_rows
from .models import (
    AssetMarginDetailSnapshot,
    AssetMoneyFlowSnapshot,
    CapitalFlowSnapshot,
    FactorScore,
    FundamentalFactorSnapshot,
)
from .tasks import calculate_factor_scores_for_date, sync_daily_capital_flow_snapshots


class Phase11FactorTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='phase11_user',
            email='phase11@example.com',
            password='Passw0rd!123',
        )
        self.market = Market.objects.create(code='P11', name='Phase 11 Market')
        self.asset1 = Asset.objects.create(
            market=self.market, symbol='600111', ts_code='600111.SH', name='Asset 1'
        )
        self.asset2 = Asset.objects.create(
            market=self.market, symbol='600222', ts_code='600222.SH', name='Asset 2'
        )

        base_date = timezone.now().date()
        for i in range(30):
            d = base_date - timedelta(days=i)
            OHLCV.objects.create(
                asset=self.asset1,
                date=d,
                open=Decimal('10'),
                high=Decimal('10.5'),
                low=Decimal('9.8'),
                close=Decimal('10.1'),
                adj_close=Decimal('10.1'),
                volume=1000000,
                amount=Decimal('10100000'),
            )
            OHLCV.objects.create(
                asset=self.asset2,
                date=d,
                open=Decimal('20'),
                high=Decimal('20.5'),
                low=Decimal('19.5'),
                close=Decimal('20.1'),
                adj_close=Decimal('20.1'),
                volume=800000,
                amount=Decimal('16080000'),
            )

        FundamentalFactorSnapshot.objects.create(
            asset=self.asset1,
            date=base_date,
            pe=Decimal('8'),
            pe_ttm=Decimal('7.5'),
            pb=Decimal('1.2'),
            roe=Decimal('0.12'),
            roe_qoq=Decimal('0.05'),
        )
        FundamentalFactorSnapshot.objects.create(
            asset=self.asset2,
            date=base_date,
            pe=Decimal('20'),
            pe_ttm=Decimal('18'),
            pb=Decimal('3.5'),
            roe=Decimal('0.09'),
            roe_qoq=Decimal('-0.03'),
        )

        CapitalFlowSnapshot.objects.create(
            asset=self.asset1,
            date=base_date,
            main_force_net_5d=Decimal('900000'),
            margin_balance_change_5d=Decimal('300000'),
        )
        CapitalFlowSnapshot.objects.create(
            asset=self.asset2,
            date=base_date,
            main_force_net_5d=Decimal('-300000'),
            margin_balance_change_5d=Decimal('-150000'),
        )

        TechnicalIndicator.objects.create(
            asset=self.asset1,
            timestamp=timezone.now(),
            indicator_type='RSI',
            value=Decimal('28'),
            parameters={'timeperiod': 14},
        )
        TechnicalIndicator.objects.create(
            asset=self.asset1,
            timestamp=timezone.now(),
            indicator_type='BBANDS',
            value=Decimal('10.1'),
            parameters={'lower': 10.2, 'middle': 10.4, 'upper': 10.6},
        )
        SignalEvent.objects.create(
            asset=self.asset1,
            signal_type=SignalEvent.SignalType.OVERSOLD_COMBINATION,
            timestamp=timezone.now(),
            description='oversold',
            metadata={},
        )

    def _seed_required_pit_membership(self, target_date, asset1_codes=('000300.SH',), asset2_codes=('000510.CSI',)):
        index_names = {
            '000300.SH': 'CSI 300',
            '000510.CSI': 'CSI A500',
        }
        for asset, index_codes in ((self.asset1, asset1_codes), (self.asset2, asset2_codes)):
            for index_code in index_codes:
                IndexMembership.objects.create(
                    asset=asset,
                    index_code=index_code,
                    index_name=index_names[index_code],
                    trade_date=target_date,
                    weight=Decimal('4.2'),
                )

    def test_calculate_factor_scores_creates_composite_scores(self):
        target_date = timezone.now().date()
        self._seed_required_pit_membership(target_date)

        calculate_factor_scores_for_date(target_date=str(target_date))
        self.assertEqual(FactorScore.objects.count(), 2)
        top = FactorScore.objects.order_by('-bottom_probability_score').first()
        asset1_score = FactorScore.objects.get(asset=self.asset1, date=target_date, mode=FactorScore.FactorMode.COMPOSITE)
        asset2_score = FactorScore.objects.get(asset=self.asset2, date=target_date, mode=FactorScore.FactorMode.COMPOSITE)
        self.assertEqual(top.asset, self.asset1)
        self.assertEqual(top.capital_flow_score, Decimal('1'))
        self.assertGreater(asset1_score.pe_ttm_percentile_score, asset2_score.pe_ttm_percentile_score)

    def test_calculate_factor_scores_ignores_future_ohlcv_rows(self):
        target_date = timezone.now().date()
        self._seed_required_pit_membership(target_date)
        calculate_factor_scores_for_date(target_date=str(target_date))
        baseline = FactorScore.objects.get(asset=self.asset1, date=target_date, mode=FactorScore.FactorMode.COMPOSITE)

        OHLCV.objects.create(
            asset=self.asset1,
            date=target_date + timedelta(days=1),
            open=Decimal('40'),
            high=Decimal('42'),
            low=Decimal('39'),
            close=Decimal('41'),
            adj_close=Decimal('41'),
            volume=5000000,
            amount=Decimal('205000000'),
        )

        calculate_factor_scores_for_date(target_date=str(target_date))
        refreshed = FactorScore.objects.get(asset=self.asset1, date=target_date, mode=FactorScore.FactorMode.COMPOSITE)

        self.assertEqual(refreshed.technical_score, baseline.technical_score)
        self.assertEqual(refreshed.bottom_probability_score, baseline.bottom_probability_score)

    def test_calculate_factor_scores_refreshes_existing_rows_without_duplicates(self):
        target_date = timezone.now().date()
        self._seed_required_pit_membership(target_date)

        calculate_factor_scores_for_date(target_date=str(target_date))
        self.assertEqual(FactorScore.objects.count(), 2)

        flow = CapitalFlowSnapshot.objects.get(asset=self.asset1, date=target_date)
        flow.main_force_net_5d = Decimal('-900000')
        flow.margin_balance_change_5d = Decimal('-400000')
        flow.save(update_fields=['main_force_net_5d', 'margin_balance_change_5d'])

        calculate_factor_scores_for_date(target_date=str(target_date))

        refreshed = FactorScore.objects.get(asset=self.asset1, date=target_date, mode=FactorScore.FactorMode.COMPOSITE)
        self.assertEqual(FactorScore.objects.count(), 2)
        self.assertEqual(refreshed.capital_flow_score, Decimal('0.5'))

    def test_calculate_factor_scores_filters_to_point_in_time_union_when_membership_exists(self):
        target_date = timezone.now().date()
        self._seed_required_pit_membership(target_date, asset1_codes=('000300.SH', '000510.CSI'), asset2_codes=())

        calculate_factor_scores_for_date(target_date=str(target_date))

        self.assertTrue(
            FactorScore.objects.filter(asset=self.asset1, date=target_date, mode=FactorScore.FactorMode.COMPOSITE).exists()
        )
        self.assertFalse(
            FactorScore.objects.filter(asset=self.asset2, date=target_date, mode=FactorScore.FactorMode.COMPOSITE).exists()
        )

    def test_bottom_candidates_requires_auth(self):
        response = self.client.get('/api/v1/screener/bottom-candidates/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_bottom_candidates_returns_ranked_results(self):
        target_date = timezone.now().date()
        self._seed_required_pit_membership(target_date)
        calculate_factor_scores_for_date(target_date=str(target_date))
        self.client.force_authenticate(user=self.user)
        response = self.client.get('/api/v1/screener/bottom-candidates/?top_n=1')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['asset_symbol'], '600111')

    def test_bottom_candidates_can_sort_by_trade_score(self):
        current_date = timezone.now().date()
        self._seed_required_pit_membership(current_date)
        calculate_factor_scores_for_date(target_date=str(current_date))
        version = ModelVersion.objects.create(
            model_type=ModelVersion.ModelType.ENSEMBLE,
            version='ensemble-test',
            status=ModelVersion.Status.READY,
            is_active=True,
        )
        PredictionResult.objects.create(
            asset=self.asset1,
            date=current_date,
            horizon_days=7,
            up_probability=Decimal('0.55'),
            flat_probability=Decimal('0.25'),
            down_probability=Decimal('0.20'),
            confidence=Decimal('0.55'),
            predicted_label=PredictionResult.Label.UP,
            trade_score=Decimal('1.800000'),
            risk_reward_ratio=Decimal('2.100000'),
            target_price=Decimal('11.0000'),
            stop_loss_price=Decimal('9.8000'),
            suggested=True,
            model_version=version,
        )
        PredictionResult.objects.create(
            asset=self.asset2,
            date=current_date,
            horizon_days=7,
            up_probability=Decimal('0.45'),
            flat_probability=Decimal('0.30'),
            down_probability=Decimal('0.25'),
            confidence=Decimal('0.45'),
            predicted_label=PredictionResult.Label.FLAT,
            trade_score=Decimal('0.700000'),
            risk_reward_ratio=Decimal('1.100000'),
            target_price=Decimal('20.8000'),
            stop_loss_price=Decimal('19.6000'),
            suggested=False,
            model_version=version,
        )
        self.client.force_authenticate(user=self.user)
        response = self.client.get('/api/v1/screener/bottom-candidates/?top_n=2&sort_by=trade_score&prediction_horizon=7')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['results'][0]['asset_symbol'], '600111')
        self.assertEqual(response.data['results'][0]['suggested'], True)
        self.assertEqual(response.data['results'][0]['trade_score'], 1.8)

    def test_calculate_factor_scores_raises_when_required_pit_membership_is_missing(self):
        target_date = timezone.now().date()

        with self.assertRaisesMessage(
            PITMembershipCoverageError,
            'missing point-in-time membership coverage',
        ):
            calculate_factor_scores_for_date(target_date=str(target_date))

    @patch('apps.factors.views.calculate_factor_scores_for_date.delay')
    def test_recalculate_endpoint_queues_task(self, mock_delay):
        self.client.force_authenticate(user=self.user)
        payload = {
            'financial_weight': 0.5,
            'flow_weight': 0.2,
            'technical_weight': 0.3,
        }
        response = self.client.post('/api/v1/screener/bottom-candidates/recalculate/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_delay.assert_called_once()


class FundamentalSnapshotBackfillCommandTests(TestCase):
    def setUp(self):
        self.market = Market.objects.create(code='FND', name='Fundamental Market')
        self.asset = Asset.objects.create(
            market=self.market,
            symbol='600333',
            ts_code='600333.SH',
            name='Fundamental Asset',
        )
        for trade_date in [
            timezone.datetime(2024, 4, 15).date(),
            timezone.datetime(2024, 4, 30).date(),
            timezone.datetime(2024, 5, 15).date(),
        ]:
            OHLCV.objects.create(
                asset=self.asset,
                date=trade_date,
                open=Decimal('10'),
                high=Decimal('11'),
                low=Decimal('9'),
                close=Decimal('10.5'),
                adj_close=Decimal('10.5'),
                volume=1000000,
                amount=Decimal('10500000'),
            )

    @patch('apps.factors.management.commands.backfill_fundamental_snapshots.ts.pro_api')
    def test_backfill_fundamental_snapshots_materializes_daily_pe_pb_and_latest_disclosed_roe(self, mock_pro_api):
        class StubPro:
            def daily_basic(self, **kwargs):
                return pd.DataFrame([
                    {'trade_date': '20240415', 'pe': 8.5, 'pe_ttm': 8.8, 'pb': 1.1, 'total_share': 100.0, 'float_share': 60.0, 'free_share': 55.0, 'total_mv': 1050.0, 'circ_mv': 577.5},
                    {'trade_date': '20240430', 'pe': 9.0, 'pe_ttm': 9.3, 'pb': 1.2, 'total_share': 100.0, 'float_share': 62.0, 'free_share': 57.0, 'total_mv': 1100.0, 'circ_mv': 627.0},
                    {'trade_date': '20240515', 'pe': 9.4, 'pe_ttm': 9.7, 'pb': 1.3, 'total_share': 100.0, 'float_share': 64.0, 'free_share': 58.0, 'total_mv': 1150.0, 'circ_mv': 667.0},
                ])

            def fina_indicator(self, **kwargs):
                return pd.DataFrame([
                    {'ann_date': '20231030', 'end_date': '20230930', 'roe': 8.0},
                    {'ann_date': '20240320', 'end_date': '20231231', 'roe': 10.0},
                    {'ann_date': '20240430', 'end_date': '20240331', 'roe': 12.0},
                ])

        mock_pro_api.return_value = StubPro()

        output = StringIO()
        call_command(
            'backfill_fundamental_snapshots',
            start_date='2024-04-01',
            end_date='2024-05-31',
            symbols='600333',
            stdout=output,
        )

        rows = list(FundamentalFactorSnapshot.objects.filter(asset=self.asset).order_by('date'))
        self.assertEqual(len(rows), 3)

        self.assertEqual(rows[0].date.isoformat(), '2024-04-15')
        self.assertEqual(rows[0].pe, Decimal('8.5'))
        self.assertEqual(rows[0].pe_ttm, Decimal('8.8'))
        self.assertEqual(rows[0].pb, Decimal('1.1'))
        self.assertEqual(rows[0].free_share, Decimal('55.0'))
        self.assertEqual(rows[0].circ_mv, Decimal('577.5'))
        self.assertEqual(rows[0].roe, Decimal('0.1'))
        self.assertEqual(rows[0].roe_qoq, Decimal('0.02'))
        self.assertEqual(rows[0].metadata['fina_indicator_end_date'], '2023-12-31')

        self.assertEqual(rows[1].date.isoformat(), '2024-04-30')
        self.assertEqual(rows[1].pe_ttm, Decimal('9.3'))
        self.assertEqual(rows[1].roe, Decimal('0.12'))
        self.assertEqual(rows[1].roe_qoq, Decimal('0.02'))
        self.assertEqual(rows[1].metadata['fina_indicator_end_date'], '2024-03-31')

        self.assertEqual(rows[2].date.isoformat(), '2024-05-15')
        self.assertEqual(rows[2].pe, Decimal('9.4'))
        self.assertEqual(rows[2].pe_ttm, Decimal('9.7'))
        self.assertEqual(rows[2].pb, Decimal('1.3'))
        self.assertEqual(rows[2].free_share, Decimal('58.0'))
        self.assertEqual(rows[2].circ_mv, Decimal('667.0'))
        self.assertEqual(rows[2].roe, Decimal('0.12'))
        self.assertEqual(rows[2].roe_qoq, Decimal('0.02'))
        self.assertIn('processed_assets=1', output.getvalue())

    @patch('apps.factors.management.commands.backfill_fundamental_snapshots.ts.pro_api')
    def test_backfill_fundamental_snapshots_reprocesses_existing_rows_with_missing_roe(self, mock_pro_api):
        class StubPro:
            def daily_basic(self, **kwargs):
                return pd.DataFrame([
                    {'trade_date': '20240415', 'pe': 8.5, 'pe_ttm': 8.8, 'pb': 1.1, 'total_share': 100.0, 'float_share': 60.0, 'free_share': 55.0, 'total_mv': 1050.0, 'circ_mv': 577.5},
                    {'trade_date': '20240430', 'pe': 9.0, 'pe_ttm': 9.3, 'pb': 1.2, 'total_share': 100.0, 'float_share': 62.0, 'free_share': 57.0, 'total_mv': 1100.0, 'circ_mv': 627.0},
                    {'trade_date': '20240515', 'pe': 9.4, 'pe_ttm': 9.7, 'pb': 1.3, 'total_share': 100.0, 'float_share': 64.0, 'free_share': 58.0, 'total_mv': 1150.0, 'circ_mv': 667.0},
                ])

            def fina_indicator(self, **kwargs):
                return pd.DataFrame([
                    {'ann_date': '20231030', 'end_date': '20230930', 'roe': 8.0},
                    {'ann_date': '20240320', 'end_date': '20231231', 'roe': 10.0},
                    {'ann_date': '20240430', 'end_date': '20240331', 'roe': 12.0},
                ])

        mock_pro_api.return_value = StubPro()

        FundamentalFactorSnapshot.objects.bulk_create([
            FundamentalFactorSnapshot(
                asset=self.asset,
                date=timezone.datetime(2024, 4, 15).date(),
                pe=Decimal('8.5'),
                pe_ttm=None,
                pb=Decimal('1.1'),
                roe=None,
                roe_qoq=None,
                metadata={'source': 'stale'},
            ),
            FundamentalFactorSnapshot(
                asset=self.asset,
                date=timezone.datetime(2024, 4, 30).date(),
                pe=Decimal('9.0'),
                pe_ttm=None,
                pb=Decimal('1.2'),
                roe=None,
                roe_qoq=None,
                metadata={'source': 'stale'},
            ),
            FundamentalFactorSnapshot(
                asset=self.asset,
                date=timezone.datetime(2024, 5, 15).date(),
                pe=Decimal('9.4'),
                pe_ttm=None,
                pb=Decimal('1.3'),
                roe=None,
                roe_qoq=None,
                metadata={'source': 'stale'},
            ),
        ])

        output = StringIO()
        call_command(
            'backfill_fundamental_snapshots',
            start_date='2024-04-01',
            end_date='2024-05-31',
            symbols='600333',
            stdout=output,
        )

        rows = list(FundamentalFactorSnapshot.objects.filter(asset=self.asset).order_by('date'))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].pe_ttm, Decimal('8.8'))
        self.assertEqual(rows[0].roe, Decimal('0.1'))
        self.assertEqual(rows[0].roe_qoq, Decimal('0.02'))
        self.assertEqual(rows[1].pe_ttm, Decimal('9.3'))
        self.assertEqual(rows[1].roe, Decimal('0.12'))
        self.assertEqual(rows[1].roe_qoq, Decimal('0.02'))
        self.assertEqual(rows[2].pe_ttm, Decimal('9.7'))
        self.assertEqual(rows[2].roe, Decimal('0.12'))
        self.assertEqual(rows[2].roe_qoq, Decimal('0.02'))
        self.assertNotIn('already complete, skipped', output.getvalue())

    @override_settings(TUSHARE_TOKEN='test-token')
    @patch('apps.factors.management.commands.backfill_fundamental_snapshots.ts.pro_api')
    def test_backfill_fundamental_snapshots_reprocesses_existing_rows_with_missing_pe_ttm(self, mock_pro_api):
        class StubPro:
            def daily_basic(self, **kwargs):
                return pd.DataFrame([
                    {'trade_date': '20240415', 'pe': 8.5, 'pe_ttm': 8.8, 'pb': 1.1, 'total_share': 100.0, 'float_share': 60.0, 'free_share': 55.0, 'total_mv': 1050.0, 'circ_mv': 577.5},
                    {'trade_date': '20240430', 'pe': 9.0, 'pe_ttm': 9.3, 'pb': 1.2, 'total_share': 100.0, 'float_share': 62.0, 'free_share': 57.0, 'total_mv': 1100.0, 'circ_mv': 627.0},
                    {'trade_date': '20240515', 'pe': 9.4, 'pe_ttm': 9.7, 'pb': 1.3, 'total_share': 100.0, 'float_share': 64.0, 'free_share': 58.0, 'total_mv': 1150.0, 'circ_mv': 667.0},
                ])

            def fina_indicator(self, **kwargs):
                return pd.DataFrame([
                    {'ann_date': '20240320', 'end_date': '20231231', 'roe': 10.0},
                ])

        mock_pro_api.return_value = StubPro()

        FundamentalFactorSnapshot.objects.bulk_create([
            FundamentalFactorSnapshot(
                asset=self.asset,
                date=timezone.datetime(2024, 4, 15).date(),
                pe=Decimal('8.5'),
                pe_ttm=None,
                pb=Decimal('1.1'),
                roe=Decimal('0.1'),
                roe_qoq=Decimal('0.0'),
                free_share=Decimal('55.0'),
                circ_mv=Decimal('577.5'),
                metadata={'source': 'stale'},
            ),
            FundamentalFactorSnapshot(
                asset=self.asset,
                date=timezone.datetime(2024, 4, 30).date(),
                pe=Decimal('9.0'),
                pe_ttm=None,
                pb=Decimal('1.2'),
                roe=Decimal('0.1'),
                roe_qoq=Decimal('0.0'),
                free_share=Decimal('57.0'),
                circ_mv=Decimal('627.0'),
                metadata={'source': 'stale'},
            ),
            FundamentalFactorSnapshot(
                asset=self.asset,
                date=timezone.datetime(2024, 5, 15).date(),
                pe=Decimal('9.4'),
                pe_ttm=None,
                pb=Decimal('1.3'),
                roe=Decimal('0.1'),
                roe_qoq=Decimal('0.0'),
                free_share=Decimal('58.0'),
                circ_mv=Decimal('667.0'),
                metadata={'source': 'stale'},
            ),
        ])

        output = StringIO()
        call_command(
            'backfill_fundamental_snapshots',
            start_date='2024-04-01',
            end_date='2024-05-31',
            symbols='600333',
            stdout=output,
        )

        rows = list(FundamentalFactorSnapshot.objects.filter(asset=self.asset).order_by('date'))
        self.assertEqual(rows[0].pe_ttm, Decimal('8.8'))
        self.assertEqual(rows[1].pe_ttm, Decimal('9.3'))
        self.assertEqual(rows[2].pe_ttm, Decimal('9.7'))
        self.assertNotIn('already complete, skipped', output.getvalue())

    @patch('apps.factors.management.commands.backfill_fundamental_snapshots.ts.pro_api')
    def test_backfill_fundamental_snapshots_looks_back_for_prior_disclosures_at_window_start(self, mock_pro_api):
        call_log = []

        class StubPro:
            def daily_basic(self, **kwargs):
                return pd.DataFrame([
                    {'trade_date': '20240415', 'pe': 8.5, 'pb': 1.1, 'total_share': 100.0, 'float_share': 60.0, 'free_share': 55.0, 'total_mv': 1050.0, 'circ_mv': 577.5},
                    {'trade_date': '20240430', 'pe': 9.0, 'pb': 1.2, 'total_share': 100.0, 'float_share': 62.0, 'free_share': 57.0, 'total_mv': 1100.0, 'circ_mv': 627.0},
                    {'trade_date': '20240515', 'pe': 9.4, 'pb': 1.3, 'total_share': 100.0, 'float_share': 64.0, 'free_share': 58.0, 'total_mv': 1150.0, 'circ_mv': 667.0},
                ])

            def fina_indicator(self, **kwargs):
                call_log.append(kwargs)
                frame = pd.DataFrame([
                    {'ann_date': '20231030', 'end_date': '20230930', 'roe': 8.0},
                    {'ann_date': '20240320', 'end_date': '20231231', 'roe': 10.0},
                    {'ann_date': '20240430', 'end_date': '20240331', 'roe': 12.0},
                ])
                return frame[
                    (frame['ann_date'] >= kwargs['start_date']) &
                    (frame['ann_date'] <= kwargs['end_date'])
                ].reset_index(drop=True)

        mock_pro_api.return_value = StubPro()

        call_command(
            'backfill_fundamental_snapshots',
            start_date='2024-04-01',
            end_date='2024-05-31',
            symbols='600333',
        )

        rows = list(FundamentalFactorSnapshot.objects.filter(asset=self.asset).order_by('date'))
        self.assertEqual(rows[0].roe, Decimal('0.1'))
        self.assertEqual(rows[0].roe_qoq, Decimal('0.02'))
        self.assertEqual(rows[0].metadata['fina_indicator_end_date'], '2023-12-31')
        self.assertLess(call_log[0]['start_date'], '20240320')

    def test_materialize_fundamental_snapshot_rows_normalizes_basis_point_style_roe(self):
        payload = materialize_fundamental_snapshot_rows(
            [timezone.datetime(2010, 11, 17).date()],
            None,
            pd.DataFrame([
                {'ann_date': '20100812', 'end_date': '20100630', 'roe': 352.0922},
                {'ann_date': '20101027', 'end_date': '20100930', 'roe': 344.5581},
            ]),
        )[0]

        self.assertEqual(payload['fina_indicator_ann_date'].isoformat(), '2010-10-27')
        self.assertEqual(payload['fina_indicator_end_date'].isoformat(), '2010-09-30')
        self.assertEqual(payload['roe'], Decimal('0.03445581'))
        self.assertEqual(payload['roe_qoq'], Decimal('-0.00075341'))

    def test_materialize_fundamental_snapshot_rows_uses_basis_point_prior_roe_for_qoq(self):
        payload = materialize_fundamental_snapshot_rows(
            [timezone.datetime(2015, 11, 13).date()],
            None,
            pd.DataFrame([
                {'ann_date': '20150827', 'end_date': '20150630', 'roe': -116.4214},
                {'ann_date': '20151024', 'end_date': '20150930', 'roe': 23.9210},
            ]),
        )[0]

        self.assertEqual(payload['fina_indicator_ann_date'].isoformat(), '2015-10-24')
        self.assertEqual(payload['fina_indicator_end_date'].isoformat(), '2015-09-30')
        self.assertEqual(payload['roe'], Decimal('0.23921'))
        self.assertEqual(payload['roe_qoq'], Decimal('0.25085214'))

    @patch('apps.factors.management.commands.backfill_fundamental_snapshots.ts.pro_api')
    def test_backfill_fundamental_snapshots_repairs_same_announcement_date_roe_rows(self, mock_pro_api):
        class StubPro:
            def daily_basic(self, **kwargs):
                return pd.DataFrame([
                    {'trade_date': '20240415', 'pe': 8.5, 'pb': 1.1, 'total_share': 100.0, 'float_share': 60.0, 'free_share': 55.0, 'total_mv': 1050.0, 'circ_mv': 577.5},
                    {'trade_date': '20240430', 'pe': 9.0, 'pb': 1.2, 'total_share': 100.0, 'float_share': 62.0, 'free_share': 57.0, 'total_mv': 1100.0, 'circ_mv': 627.0},
                    {'trade_date': '20240515', 'pe': 9.4, 'pb': 1.3, 'total_share': 100.0, 'float_share': 64.0, 'free_share': 58.0, 'total_mv': 1150.0, 'circ_mv': 667.0},
                ])

            def fina_indicator(self, **kwargs):
                return pd.DataFrame([
                    {'ann_date': '20231030', 'end_date': '20230930', 'roe': 9.0},
                    {'ann_date': '20240320', 'end_date': '20231231', 'roe': 11.0},
                    {'ann_date': '20240430', 'end_date': '20231231', 'roe': 11.0},
                    {'ann_date': '20240430', 'end_date': '20240331', 'roe': 12.0},
                ])

        mock_pro_api.return_value = StubPro()

        FundamentalFactorSnapshot.objects.bulk_create([
            FundamentalFactorSnapshot(
                asset=self.asset,
                date=timezone.datetime(2024, 4, 15).date(),
                pe=Decimal('8.5'),
                pe_ttm=Decimal('8.8'),
                pb=Decimal('1.1'),
                total_share=Decimal('100.0'),
                float_share=Decimal('60.0'),
                free_share=Decimal('55.0'),
                total_mv=Decimal('1050.0'),
                circ_mv=Decimal('577.5'),
                roe=Decimal('0.09'),
                roe_qoq=None,
                metadata={
                    'source': 'stale',
                    'daily_basic_trade_date': '2024-04-15',
                    'fina_indicator_ann_date': '2023-10-30',
                    'fina_indicator_end_date': '2023-09-30',
                },
            ),
            FundamentalFactorSnapshot(
                asset=self.asset,
                date=timezone.datetime(2024, 4, 30).date(),
                pe=Decimal('9.0'),
                pe_ttm=Decimal('9.3'),
                pb=Decimal('1.2'),
                total_share=Decimal('100.0'),
                float_share=Decimal('62.0'),
                free_share=Decimal('57.0'),
                total_mv=Decimal('1100.0'),
                circ_mv=Decimal('627.0'),
                roe=Decimal('0.11'),
                roe_qoq=Decimal('0.02'),
                metadata={
                    'source': 'stale',
                    'daily_basic_trade_date': '2024-04-30',
                    'fina_indicator_ann_date': '2024-04-30',
                    'fina_indicator_end_date': '2023-12-31',
                },
            ),
            FundamentalFactorSnapshot(
                asset=self.asset,
                date=timezone.datetime(2024, 5, 15).date(),
                pe=Decimal('9.4'),
                pe_ttm=Decimal('9.7'),
                pb=Decimal('1.3'),
                total_share=Decimal('100.0'),
                float_share=Decimal('64.0'),
                free_share=Decimal('58.0'),
                total_mv=Decimal('1150.0'),
                circ_mv=Decimal('667.0'),
                roe=Decimal('0.11'),
                roe_qoq=Decimal('0.02'),
                metadata={
                    'source': 'stale',
                    'daily_basic_trade_date': '2024-05-15',
                    'fina_indicator_ann_date': '2024-04-30',
                    'fina_indicator_end_date': '2023-12-31',
                },
            ),
        ])

        output = StringIO()
        call_command(
            'backfill_fundamental_snapshots',
            start_date='2024-04-01',
            end_date='2024-05-31',
            symbols='600333',
            repair_same_announcement_roe=True,
            stdout=output,
        )

        rows = list(FundamentalFactorSnapshot.objects.filter(asset=self.asset).order_by('date'))
        self.assertEqual(rows[0].metadata['fina_indicator_end_date'], '2023-09-30')
        self.assertEqual(rows[0].roe, Decimal('0.09'))
        self.assertIsNone(rows[0].roe_qoq)
        self.assertEqual(rows[1].metadata['fina_indicator_end_date'], '2024-03-31')
        self.assertEqual(rows[2].metadata['fina_indicator_end_date'], '2024-03-31')
        self.assertEqual(rows[1].roe, Decimal('0.12'))
        self.assertEqual(rows[1].roe_qoq, Decimal('0.01'))
        self.assertEqual(rows[2].roe, Decimal('0.12'))
        self.assertEqual(rows[2].roe_qoq, Decimal('0.01'))
        self.assertIn('stale same-announcement roe rows detected, reprocessing', output.getvalue())
        self.assertNotIn('already complete, skipped', output.getvalue())


class CapitalFlowSnapshotBackfillCommandTests(TestCase):
    def setUp(self):
        self.market = Market.objects.create(code='CAP', name='Capital Flow Market')
        self.asset = Asset.objects.create(
            market=self.market,
            symbol='600555',
            ts_code='600555.SH',
            name='Capital Flow Asset',
        )
        self.trading_dates = [
            timezone.datetime(2024, 4, 1).date(),
            timezone.datetime(2024, 4, 2).date(),
            timezone.datetime(2024, 4, 3).date(),
            timezone.datetime(2024, 4, 4).date(),
            timezone.datetime(2024, 4, 5).date(),
            timezone.datetime(2024, 4, 8).date(),
        ]
        for trade_date in self.trading_dates:
            OHLCV.objects.create(
                asset=self.asset,
                date=trade_date,
                open=Decimal('10'),
                high=Decimal('11'),
                low=Decimal('9'),
                close=Decimal('10.5'),
                adj_close=Decimal('10.5'),
                volume=1000000,
                amount=Decimal('10500000'),
            )

    @patch('apps.factors.management.commands.backfill_capital_flow_snapshots.ts.pro_api')
    def test_backfill_capital_flow_snapshots_materializes_raw_and_derived_rows(self, mock_pro_api):
        class StubPro:
            def moneyflow(self, **kwargs):
                return pd.DataFrame([
                    {
                        'trade_date': '20240401', 'buy_sm_amount': 5.0, 'sell_sm_amount': 4.0,
                        'buy_md_amount': 8.0, 'sell_md_amount': 7.0,
                        'buy_lg_amount': 100.0, 'sell_lg_amount': 60.0,
                        'buy_elg_amount': 80.0, 'sell_elg_amount': 20.0,
                        'net_mf_amount': 101.0,
                    },
                    {
                        'trade_date': '20240402', 'buy_sm_amount': 4.0, 'sell_sm_amount': 3.0,
                        'buy_md_amount': 7.0, 'sell_md_amount': 6.0,
                        'buy_lg_amount': 70.0, 'sell_lg_amount': 40.0,
                        'buy_elg_amount': 30.0, 'sell_elg_amount': 10.0,
                        'net_mf_amount': 52.0,
                    },
                    {
                        'trade_date': '20240403', 'buy_sm_amount': 3.0, 'sell_sm_amount': 3.5,
                        'buy_md_amount': 6.0, 'sell_md_amount': 6.5,
                        'buy_lg_amount': 20.0, 'sell_lg_amount': 30.0,
                        'buy_elg_amount': 10.0, 'sell_elg_amount': 20.0,
                        'net_mf_amount': -18.0,
                    },
                    {
                        'trade_date': '20240404', 'buy_sm_amount': 4.0, 'sell_sm_amount': 2.0,
                        'buy_md_amount': 9.0, 'sell_md_amount': 5.0,
                        'buy_lg_amount': 90.0, 'sell_lg_amount': 35.0,
                        'buy_elg_amount': 40.0, 'sell_elg_amount': 15.0,
                        'net_mf_amount': 83.0,
                    },
                    {
                        'trade_date': '20240405', 'buy_sm_amount': 2.0, 'sell_sm_amount': 2.0,
                        'buy_md_amount': 5.0, 'sell_md_amount': 5.0,
                        'buy_lg_amount': 25.0, 'sell_lg_amount': 30.0,
                        'buy_elg_amount': 5.0, 'sell_elg_amount': 10.0,
                        'net_mf_amount': -11.0,
                    },
                    {
                        'trade_date': '20240408', 'buy_sm_amount': 6.0, 'sell_sm_amount': 4.0,
                        'buy_md_amount': 10.0, 'sell_md_amount': 7.0,
                        'buy_lg_amount': 80.0, 'sell_lg_amount': 35.0,
                        'buy_elg_amount': 30.0, 'sell_elg_amount': 15.0,
                        'net_mf_amount': 61.0,
                    },
                ])

            def margin_detail(self, **kwargs):
                return pd.DataFrame([
                    {'trade_date': '20240401', 'rzye': 900.0, 'rqye': 100.0, 'rzmre': 50.0, 'rzche': 40.0, 'rqyl': 20.0, 'rqchl': 5.0, 'rqmcl': 4.0, 'rzrqye': 1000.0},
                    {'trade_date': '20240402', 'rzye': 980.0, 'rqye': 120.0, 'rzmre': 60.0, 'rzche': 45.0, 'rqyl': 21.0, 'rqchl': 5.0, 'rqmcl': 4.0, 'rzrqye': 1100.0},
                    {'trade_date': '20240403', 'rzye': 960.0, 'rqye': 120.0, 'rzmre': 55.0, 'rzche': 48.0, 'rqyl': 20.0, 'rqchl': 4.0, 'rqmcl': 3.0, 'rzrqye': 1080.0},
                    {'trade_date': '20240404', 'rzye': 1060.0, 'rqye': 140.0, 'rzmre': 62.0, 'rzche': 44.0, 'rqyl': 22.0, 'rqchl': 4.0, 'rqmcl': 4.0, 'rzrqye': 1200.0},
                    {'trade_date': '20240405', 'rzye': 1040.0, 'rqye': 150.0, 'rzmre': 63.0, 'rzche': 47.0, 'rqyl': 23.0, 'rqchl': 4.0, 'rqmcl': 5.0, 'rzrqye': 1190.0},
                    {'trade_date': '20240408', 'rzye': 1140.0, 'rqye': 160.0, 'rzmre': 66.0, 'rzche': 49.0, 'rqyl': 24.0, 'rqchl': 4.0, 'rqmcl': 6.0, 'rzrqye': 1300.0},
                ])

        mock_pro_api.return_value = StubPro()

        output = StringIO()
        with patch('apps.factors.management.commands.backfill_capital_flow_snapshots.settings.TUSHARE_TOKEN', 'test-token'):
            call_command(
                'backfill_capital_flow_snapshots',
                start_date='2024-04-01',
                end_date='2024-04-08',
                symbols='600555',
                stdout=output,
            )

        self.assertEqual(AssetMoneyFlowSnapshot.objects.filter(asset=self.asset).count(), 6)
        self.assertEqual(AssetMarginDetailSnapshot.objects.filter(asset=self.asset).count(), 6)

        rows = list(CapitalFlowSnapshot.objects.filter(asset=self.asset).order_by('date'))
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0].main_force_net_5d, Decimal('100.0'))
        self.assertEqual(rows[1].main_force_net_5d, Decimal('150.0'))
        self.assertEqual(rows[4].main_force_net_5d, Decimal('200.0'))
        self.assertEqual(rows[5].main_force_net_5d, Decimal('160.0'))
        self.assertIsNone(rows[4].margin_balance_change_5d)
        self.assertEqual(rows[5].margin_balance_change_5d, Decimal('300.0'))
        self.assertEqual(rows[5].metadata['source'], 'tushare_moneyflow_margin_detail')
        self.assertIn('processed_assets=1', output.getvalue())
        self.assertIn('capital_rows=6', output.getvalue())

    @patch('apps.factors.management.commands.backfill_capital_flow_snapshots.ts.pro_api')
    def test_backfill_capital_flow_snapshots_uses_existing_raw_lookback_for_single_day_reruns(self, mock_pro_api):
        historical_moneyflow = [
            ('20240401', Decimal('100.0')),
            ('20240402', Decimal('50.0')),
            ('20240403', Decimal('-20.0')),
            ('20240404', Decimal('80.0')),
            ('20240405', Decimal('-10.0')),
        ]
        historical_margin = [
            ('20240401', Decimal('1000.0')),
            ('20240402', Decimal('1100.0')),
            ('20240403', Decimal('1080.0')),
            ('20240404', Decimal('1200.0')),
            ('20240405', Decimal('1190.0')),
        ]

        for trade_date, main_force_daily in historical_moneyflow:
            buy_lg = main_force_daily if main_force_daily > 0 else Decimal('0')
            sell_lg = -main_force_daily if main_force_daily < 0 else Decimal('0')
            AssetMoneyFlowSnapshot.objects.create(
                asset=self.asset,
                date=timezone.datetime.strptime(trade_date, '%Y%m%d').date(),
                buy_sm_amount=Decimal('0'),
                sell_sm_amount=Decimal('0'),
                buy_md_amount=Decimal('0'),
                sell_md_amount=Decimal('0'),
                buy_lg_amount=buy_lg,
                sell_lg_amount=sell_lg,
                buy_elg_amount=Decimal('0'),
                sell_elg_amount=Decimal('0'),
                net_mf_amount=main_force_daily,
                metadata={'source': 'seed'},
            )

        for trade_date, rzrqye in historical_margin:
            AssetMarginDetailSnapshot.objects.create(
                asset=self.asset,
                date=timezone.datetime.strptime(trade_date, '%Y%m%d').date(),
                rzye=rzrqye,
                rqye=Decimal('0'),
                rzmre=Decimal('0'),
                rzche=Decimal('0'),
                rqyl=Decimal('0'),
                rqchl=Decimal('0'),
                rqmcl=Decimal('0'),
                rzrqye=rzrqye,
                metadata={'source': 'seed'},
            )

        class StubPro:
            def moneyflow(self, **kwargs):
                return pd.DataFrame([
                    {
                        'trade_date': '20240408', 'buy_sm_amount': 0.0, 'sell_sm_amount': 0.0,
                        'buy_md_amount': 0.0, 'sell_md_amount': 0.0,
                        'buy_lg_amount': 60.0, 'sell_lg_amount': 0.0,
                        'buy_elg_amount': 0.0, 'sell_elg_amount': 0.0,
                        'net_mf_amount': 60.0,
                    },
                ])

            def margin_detail(self, **kwargs):
                return pd.DataFrame([
                    {
                        'trade_date': '20240408', 'rzye': 1300.0, 'rqye': 0.0, 'rzmre': 0.0,
                        'rzche': 0.0, 'rqyl': 0.0, 'rqchl': 0.0, 'rqmcl': 0.0, 'rzrqye': 1300.0,
                    },
                ])

        mock_pro_api.return_value = StubPro()

        output = StringIO()
        with patch('apps.factors.management.commands.backfill_capital_flow_snapshots.settings.TUSHARE_TOKEN', 'test-token'):
            call_command(
                'backfill_capital_flow_snapshots',
                start_date='2024-04-08',
                end_date='2024-04-08',
                symbols='600555',
                stdout=output,
            )

        row = CapitalFlowSnapshot.objects.get(asset=self.asset, date=timezone.datetime(2024, 4, 8).date())
        self.assertEqual(row.main_force_net_5d, Decimal('160.0'))
        self.assertEqual(row.margin_balance_change_5d, Decimal('300.0'))
        self.assertEqual(AssetMarginDetailSnapshot.objects.filter(asset=self.asset).count(), 6)
        self.assertIn('capital_rows=1', output.getvalue())


class FundamentalSnapshotMarketCapBackfillCommandTests(TestCase):
    def setUp(self):
        self.market = Market.objects.create(code='FMC', name='Fundamental Market Cap')
        self.asset = Asset.objects.create(
            market=self.market,
            symbol='600333',
            ts_code='600333.SH',
            name='Fundamental Asset',
        )
        for trade_date in [
            timezone.datetime(2024, 4, 15).date(),
            timezone.datetime(2024, 4, 30).date(),
            timezone.datetime(2024, 5, 15).date(),
        ]:
            OHLCV.objects.create(
                asset=self.asset,
                date=trade_date,
                open=Decimal('10'),
                high=Decimal('11'),
                low=Decimal('9'),
                close=Decimal('10.5'),
                adj_close=Decimal('10.5'),
                volume=1000000,
                amount=Decimal('10500000'),
            )

    @patch('apps.factors.management.commands.backfill_fundamental_snapshots.ts.pro_api')
    def test_backfill_fundamental_snapshots_reprocesses_existing_rows_with_missing_market_cap_fields(self, mock_pro_api):
        class StubPro:
            def daily_basic(self, **kwargs):
                return pd.DataFrame([
                    {'trade_date': '20240415', 'pe': 8.5, 'pb': 1.1, 'total_share': 100.0, 'float_share': 60.0, 'free_share': 55.0, 'total_mv': 1050.0, 'circ_mv': 577.5},
                    {'trade_date': '20240430', 'pe': 9.0, 'pb': 1.2, 'total_share': 100.0, 'float_share': 62.0, 'free_share': 57.0, 'total_mv': 1100.0, 'circ_mv': 627.0},
                    {'trade_date': '20240515', 'pe': 9.4, 'pb': 1.3, 'total_share': 100.0, 'float_share': 64.0, 'free_share': 58.0, 'total_mv': 1150.0, 'circ_mv': 667.0},
                ])

            def fina_indicator(self, **kwargs):
                return pd.DataFrame([
                    {'ann_date': '20240320', 'end_date': '20231231', 'roe': 10.0},
                ])

        mock_pro_api.return_value = StubPro()

        FundamentalFactorSnapshot.objects.bulk_create([
            FundamentalFactorSnapshot(
                asset=self.asset,
                date=timezone.datetime(2024, 4, 15).date(),
                pe=Decimal('8.5'),
                pe_ttm=Decimal('8.8'),
                pb=Decimal('1.1'),
                roe=Decimal('0.1'),
                roe_qoq=Decimal('0.0'),
                free_share=None,
                circ_mv=None,
                metadata={'source': 'stale'},
            ),
            FundamentalFactorSnapshot(
                asset=self.asset,
                date=timezone.datetime(2024, 4, 30).date(),
                pe=Decimal('9.0'),
                pe_ttm=Decimal('9.3'),
                pb=Decimal('1.2'),
                roe=Decimal('0.1'),
                roe_qoq=Decimal('0.0'),
                free_share=None,
                circ_mv=None,
                metadata={'source': 'stale'},
            ),
            FundamentalFactorSnapshot(
                asset=self.asset,
                date=timezone.datetime(2024, 5, 15).date(),
                pe=Decimal('9.4'),
                pe_ttm=Decimal('9.7'),
                pb=Decimal('1.3'),
                roe=Decimal('0.1'),
                roe_qoq=Decimal('0.0'),
                free_share=None,
                circ_mv=None,
                metadata={'source': 'stale'},
            ),
        ])

        call_command(
            'backfill_fundamental_snapshots',
            start_date='2024-04-01',
            end_date='2024-05-31',
            symbols='600333',
        )

        rows = list(FundamentalFactorSnapshot.objects.filter(asset=self.asset).order_by('date'))
        self.assertEqual(rows[0].free_share, Decimal('55.0'))
        self.assertEqual(rows[1].circ_mv, Decimal('627.0'))
        self.assertEqual(rows[2].total_mv, Decimal('1150.0'))


class CapitalFlowDailySyncTaskTests(TestCase):
    @patch('apps.factors.tasks.call_command')
    @patch('apps.factors.tasks.settings.CAPITAL_FLOW_DAILY_SYNC_LOOKBACK_DAYS', 20)
    def test_sync_daily_capital_flow_snapshots_calls_backfill_over_recent_window(self, mock_call_command):
        result = sync_daily_capital_flow_snapshots(target_date='2024-04-24')

        mock_call_command.assert_called_once()
        self.assertEqual(mock_call_command.call_args.args[0], 'backfill_capital_flow_snapshots')
        self.assertEqual(mock_call_command.call_args.kwargs['start_date'], '2024-04-04')
        self.assertEqual(mock_call_command.call_args.kwargs['end_date'], '2024-04-24')
        self.assertIn('Capital flow sync completed', result)
