from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.analytics.models import SignalEvent, TechnicalIndicator
from apps.markets.models import Market, Asset, OHLCV
from apps.prediction.models import ModelVersion, PredictionResult
from .models import FundamentalFactorSnapshot, CapitalFlowSnapshot, FactorScore
from .tasks import calculate_factor_scores_for_date


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
            pb=Decimal('1.2'),
            roe=Decimal('0.12'),
            roe_qoq=Decimal('0.05'),
        )
        FundamentalFactorSnapshot.objects.create(
            asset=self.asset2,
            date=base_date,
            pe=Decimal('20'),
            pb=Decimal('3.5'),
            roe=Decimal('0.09'),
            roe_qoq=Decimal('-0.03'),
        )

        CapitalFlowSnapshot.objects.create(
            asset=self.asset1,
            date=base_date,
            northbound_net_5d=Decimal('1200000'),
            main_force_net_5d=Decimal('900000'),
            margin_balance_change_5d=Decimal('300000'),
        )
        CapitalFlowSnapshot.objects.create(
            asset=self.asset2,
            date=base_date,
            northbound_net_5d=Decimal('-500000'),
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

    def test_calculate_factor_scores_creates_composite_scores(self):
        calculate_factor_scores_for_date(target_date=str(timezone.now().date()))
        self.assertEqual(FactorScore.objects.count(), 2)
        top = FactorScore.objects.order_by('-bottom_probability_score').first()
        self.assertEqual(top.asset, self.asset1)

    def test_calculate_factor_scores_ignores_future_ohlcv_rows(self):
        target_date = timezone.now().date()
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

    def test_bottom_candidates_requires_auth(self):
        response = self.client.get('/api/v1/screener/bottom-candidates/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_bottom_candidates_returns_ranked_results(self):
        calculate_factor_scores_for_date(target_date=str(timezone.now().date()))
        self.client.force_authenticate(user=self.user)
        response = self.client.get('/api/v1/screener/bottom-candidates/?top_n=1')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['asset_symbol'], '600111')

    def test_bottom_candidates_can_sort_by_trade_score(self):
        current_date = timezone.now().date()
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
