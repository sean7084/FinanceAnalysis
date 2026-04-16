from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

import datetime
from apps.markets.models import Market, Asset, OHLCV
from .models import AlertRule, AlertEvent, TechnicalIndicator, SignalEvent
from apps.factors.models import FactorScore
from apps.prediction.models import ModelVersion, PredictionResult
from apps.prediction.models_lightgbm import LightGBMModelArtifact, LightGBMPrediction
from apps.sentiment.models import SentimentScore
from .tasks import (
    check_alert_rules,
    calculate_fibonacci_retracement_for_asset,
    calculate_ma_signals_for_asset,
    calculate_bollinger_signals_for_asset,
    calculate_volume_signals_for_asset,
    calculate_momentum_signals_for_asset,
)


class Phase9AlertTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='phase9_user',
            email='phase9@example.com',
            password='Passw0rd!123',
        )
        self.market = Market.objects.create(code='SSE', name='Shanghai Stock Exchange')
        self.asset = Asset.objects.create(
            market=self.market,
            symbol='600000',
            ts_code='600000.SH',
            name='PF Bank',
        )
        OHLCV.objects.create(
            asset=self.asset,
            date=timezone.now().date(),
            open=Decimal('10.00'),
            high=Decimal('10.50'),
            low=Decimal('9.80'),
            close=Decimal('10.20'),
            adj_close=Decimal('10.20'),
            volume=1000000,
            amount=Decimal('10200000.00'),
        )

    @patch('apps.analytics.tasks.send_alert_notifications.delay')
    def test_check_alert_rules_triggers_price_above(self, mock_delay):
        rule = AlertRule.objects.create(
            owner=self.user,
            asset=self.asset,
            name='Price breakout',
            condition_type=AlertRule.ConditionType.PRICE_ABOVE,
            threshold=Decimal('10.00'),
            channels=['websocket'],
            is_active=True,
        )

        result = check_alert_rules()

        self.assertIn('Triggered 1 alert events', result)
        self.assertEqual(AlertEvent.objects.count(), 1)
        event = AlertEvent.objects.get()
        self.assertEqual(event.alert_rule, rule)
        self.assertEqual(event.asset, self.asset)
        self.assertEqual(event.status, AlertEvent.Status.TRIGGERED)
        mock_delay.assert_called_once_with(event.id)

    @patch('apps.analytics.tasks.send_alert_notifications.delay')
    def test_check_alert_rules_respects_cooldown(self, mock_delay):
        AlertRule.objects.create(
            owner=self.user,
            asset=self.asset,
            name='Recent alert',
            condition_type=AlertRule.ConditionType.PRICE_ABOVE,
            threshold=Decimal('9.00'),
            channels=['websocket'],
            cooldown_minutes=60,
            last_triggered_at=timezone.now(),
            is_active=True,
        )

        result = check_alert_rules()

        self.assertIn('Triggered 0 alert events', result)
        self.assertEqual(AlertEvent.objects.count(), 0)
        mock_delay.assert_not_called()


class Phase9ApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='api_user',
            email='api_user@example.com',
            password='Passw0rd!123',
        )
        self.other_user = User.objects.create_user(
            username='other_user',
            email='other_user@example.com',
            password='Passw0rd!123',
        )
        self.market = Market.objects.create(code='SZSE', name='Shenzhen Stock Exchange')
        self.asset = Asset.objects.create(
            market=self.market,
            symbol='000001',
            ts_code='000001.SZ',
            name='PF Asset',
        )

    def test_alert_rule_create_sets_owner(self):
        self.client.force_authenticate(user=self.user)

        payload = {
            'asset': self.asset.id,
            'name': 'API price alert',
            'condition_type': AlertRule.ConditionType.PRICE_ABOVE,
            'threshold': '10.50',
            'channels': ['websocket'],
            'cooldown_minutes': 30,
            'is_active': True,
        }
        response = self.client.post('/api/v1/alerts/', payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        alert = AlertRule.objects.get(id=response.data['id'])
        self.assertEqual(alert.owner, self.user)

    def test_alert_events_are_user_scoped(self):
        owner_rule = AlertRule.objects.create(
            owner=self.user,
            asset=self.asset,
            name='Owner rule',
            condition_type=AlertRule.ConditionType.PRICE_ABOVE,
            threshold=Decimal('10.00'),
            channels=['websocket'],
        )
        other_rule = AlertRule.objects.create(
            owner=self.other_user,
            asset=self.asset,
            name='Other rule',
            condition_type=AlertRule.ConditionType.PRICE_ABOVE,
            threshold=Decimal('10.00'),
            channels=['websocket'],
        )

        AlertEvent.objects.create(
            alert_rule=owner_rule,
            asset=self.asset,
            message='Owner event',
            trigger_value=Decimal('10.20'),
        )
        AlertEvent.objects.create(
            alert_rule=other_rule,
            asset=self.asset,
            message='Other event',
            trigger_value=Decimal('10.30'),
        )

        self.client.force_authenticate(user=self.user)
        response = self.client.get('/api/v1/alert-events/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['message'], 'Owner event')

    def test_screener_prebuilt_requires_authentication(self):
        response = self.client.get('/api/v1/screeners/prebuilt/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        self.client.force_authenticate(user=self.user)
        response = self.client.get('/api/v1/screeners/prebuilt/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('screeners', response.data)


class Phase17DashboardStockApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='dashboard_user',
            email='dashboard_user@example.com',
            password='Passw0rd!123',
        )
        self.market = Market.objects.create(code='DASH', name='Dashboard Market')
        self.asset1 = Asset.objects.create(market=self.market, symbol='600111', ts_code='600111.SH', name='Alpha Corp')
        self.asset2 = Asset.objects.create(market=self.market, symbol='600222', ts_code='600222.SH', name='Beta Corp')
        self.as_of = timezone.now().date()

        for asset, base_price, composite_score, bottom_prob in [
            (self.asset1, Decimal('10.2'), Decimal('0.810000'), Decimal('0.760000')),
            (self.asset2, Decimal('20.4'), Decimal('0.610000'), Decimal('0.520000')),
        ]:
            FactorScore.objects.create(
                asset=asset,
                date=self.as_of,
                mode=FactorScore.FactorMode.COMPOSITE,
                pe_percentile_score=Decimal('0.300000'),
                pb_percentile_score=Decimal('0.400000'),
                roe_trend_score=Decimal('0.600000'),
                northbound_flow_score=Decimal('0.500000'),
                main_force_flow_score=Decimal('0.550000'),
                margin_flow_score=Decimal('0.450000'),
                technical_reversal_score=Decimal('0.700000'),
                sentiment_score=Decimal('0.350000'),
                fundamental_score=Decimal('0.400000'),
                capital_flow_score=Decimal('0.500000'),
                technical_score=Decimal('0.650000'),
                composite_score=composite_score,
                bottom_probability_score=bottom_prob,
            )
            OHLCV.objects.create(
                asset=asset,
                date=self.as_of,
                open=base_price,
                high=base_price + Decimal('0.5'),
                low=base_price - Decimal('0.4'),
                close=base_price,
                adj_close=base_price,
                volume=1000000,
                amount=base_price * Decimal('1000000'),
            )
            SentimentScore.objects.create(
                article=None,
                asset=asset,
                date=self.as_of,
                score_type=SentimentScore.ScoreType.ASSET_7D,
                positive_score=Decimal('0.55'),
                neutral_score=Decimal('0.25'),
                negative_score=Decimal('0.20'),
                sentiment_score=Decimal('0.350000'),
                sentiment_label=SentimentScore.Label.POSITIVE,
            )
            for indicator_type, value, parameters in [
                ('RSI', Decimal('62.50000000'), {}),
                ('MACD', Decimal('0.82000000'), {}),
                ('BBANDS', Decimal('10.20000000'), {'upper': 11.0, 'lower': 9.4, 'timeperiod': 20}),
                ('SMA', Decimal('9.90000000'), {'timeperiod': 60}),
            ]:
                TechnicalIndicator.objects.create(
                    asset=asset,
                    indicator_type=indicator_type,
                    value=value,
                    parameters=parameters,
                    timestamp=timezone.make_aware(timezone.datetime.combine(self.as_of, timezone.datetime.min.time())),
                )

        heuristic_version = ModelVersion.objects.create(
            model_type=ModelVersion.ModelType.ENSEMBLE,
            version='dashboard-heuristic-v1',
            status=ModelVersion.Status.READY,
            is_active=True,
        )
        lightgbm_artifact = LightGBMModelArtifact.objects.create(
            horizon_days=7,
            version='dashboard-lgbm-v1',
            status=LightGBMModelArtifact.Status.READY,
            is_active=True,
        )

        PredictionResult.objects.create(
            asset=self.asset1,
            date=self.as_of,
            horizon_days=7,
            up_probability=Decimal('0.610000'),
            flat_probability=Decimal('0.210000'),
            down_probability=Decimal('0.180000'),
            confidence=Decimal('0.610000'),
            predicted_label=PredictionResult.Label.UP,
            risk_reward_ratio=Decimal('2.400000'),
            trade_score=Decimal('1.850000'),
            suggested=True,
            model_version=heuristic_version,
        )
        PredictionResult.objects.create(
            asset=self.asset2,
            date=self.as_of,
            horizon_days=7,
            up_probability=Decimal('0.410000'),
            flat_probability=Decimal('0.290000'),
            down_probability=Decimal('0.300000'),
            confidence=Decimal('0.410000'),
            predicted_label=PredictionResult.Label.FLAT,
            risk_reward_ratio=Decimal('1.100000'),
            trade_score=Decimal('0.720000'),
            suggested=False,
            model_version=heuristic_version,
        )
        LightGBMPrediction.objects.create(
            asset=self.asset1,
            date=self.as_of,
            horizon_days=7,
            up_probability=Decimal('0.650000'),
            flat_probability=Decimal('0.180000'),
            down_probability=Decimal('0.170000'),
            confidence=Decimal('0.650000'),
            predicted_label=LightGBMPrediction.Label.UP,
            risk_reward_ratio=Decimal('2.800000'),
            trade_score=Decimal('2.120000'),
            suggested=True,
            model_artifact=lightgbm_artifact,
        )
        LightGBMPrediction.objects.create(
            asset=self.asset2,
            date=self.as_of,
            horizon_days=7,
            up_probability=Decimal('0.380000'),
            flat_probability=Decimal('0.330000'),
            down_probability=Decimal('0.290000'),
            confidence=Decimal('0.380000'),
            predicted_label=LightGBMPrediction.Label.FLAT,
            risk_reward_ratio=Decimal('1.050000'),
            trade_score=Decimal('0.540000'),
            suggested=False,
            model_artifact=lightgbm_artifact,
        )

    def test_dashboard_stocks_requires_authentication(self):
        response = self.client.get('/api/v1/dashboard/stocks/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_dashboard_stocks_returns_composite_rows(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get('/api/v1/dashboard/stocks/?prediction_horizon=7')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 2)
        first = response.data['results'][0]
        self.assertEqual(first['asset_symbol'], '600111')
        self.assertEqual(first['heuristic_label'], 'UP')
        self.assertEqual(first['lightgbm_label'], 'UP')
        self.assertEqual(first['rsi'], '62.50000000')
        self.assertEqual(first['bb_upper'], '11.00000000')

    def test_dashboard_stocks_filters_and_orders_rows(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get('/api/v1/dashboard/stocks/?prediction_horizon=7&model_family=lightgbm&suggested_only=true&ordering=-lightgbm_trade_score&search=alpha')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['asset_symbol'], '600111')


class Phase8IndicatorTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='phase8_user',
            email='phase8@example.com',
            password='Passw0rd!123',
        )
        self.market = Market.objects.create(code='BSE', name='Beijing Stock Exchange')
        self.asset = Asset.objects.create(
            market=self.market,
            symbol='830001',
            ts_code='830001.BJ',
            name='Phase8 Asset',
        )
        today = timezone.now().date()
        for i, close in enumerate([10.0, 10.8, 11.2, 10.6, 11.5, 11.0], start=1):
            day = today - timedelta(days=i)
            OHLCV.objects.create(
                asset=self.asset,
                date=day,
                open=Decimal(str(close - 0.2)),
                high=Decimal(str(close + 0.4)),
                low=Decimal(str(close - 0.5)),
                close=Decimal(str(close)),
                adj_close=Decimal(str(close)),
                volume=900000 + i * 1000,
                amount=Decimal(str((900000 + i * 1000) * close)),
            )

    def test_calculate_fibonacci_retracement_creates_indicator(self):
        calculate_fibonacci_retracement_for_asset(self.asset.id, lookback_days=5)

        fib = TechnicalIndicator.objects.filter(asset=self.asset, indicator_type='FIB_RET').first()
        self.assertIsNotNone(fib)
        self.assertIn('levels', fib.parameters)
        self.assertIn('0.618', fib.parameters['levels'])

    @patch('apps.analytics.views.calculate_fibonacci_retracement_for_asset.delay')
    def test_recalculate_endpoint_queues_with_custom_params(self, mock_delay):
        self.client.force_authenticate(user=self.user)
        payload = {
            'asset_id': self.asset.id,
            'indicator_type': 'FIB_RET',
            'params': {'lookback_days': 30},
        }

        response = self.client.post('/api/v1/indicators/recalculate/', payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_delay.assert_called_once_with(asset_id=self.asset.id, lookback_days=30)


def _make_ohlcv_sequence(asset, prices, base_date=None, volume=1000000):
    """Helper: create OHLCV rows from a list of (close) prices, most-recent last."""
    if base_date is None:
        base_date = timezone.now().date()
    n = len(prices)
    for i, close in enumerate(prices):
        day = base_date - datetime.timedelta(days=(n - 1 - i))
        OHLCV.objects.get_or_create(
            asset=asset,
            date=day,
            defaults=dict(
                open=Decimal(str(close)),
                high=Decimal(str(close * 1.01)),
                low=Decimal(str(close * 0.99)),
                close=Decimal(str(close)),
                adj_close=Decimal(str(close)),
                volume=volume,
                amount=Decimal(str(close * volume)),
            ),
        )


class Phase10SignalTests(TestCase):
    """Tests for Phase 10 technical signal detection tasks and the signals API."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='p10_user', email='p10@example.com', password='Passw0rd!123',
        )
        self.market = Market.objects.create(code='P10MKT', name='Phase10 Market')
        self.asset = Asset.objects.create(
            market=self.market, symbol='P10001', ts_code='P10001.SH', name='Phase10 Asset',
        )

    def _auth(self):
        self.client.force_authenticate(user=self.user)

    # ------------------------------------------------------------------
    # MA signals
    # ------------------------------------------------------------------
    def test_golden_cross_creates_signal(self):
        """65 days of flat price then a spike on the last day triggers a golden cross."""
        prices = [10.0] * 64 + [15.0]
        _make_ohlcv_sequence(self.asset, prices)
        calculate_ma_signals_for_asset(self.asset.id)
        self.assertTrue(
            SignalEvent.objects.filter(asset=self.asset, signal_type='GOLDEN_CROSS').exists()
        )

    def test_death_cross_creates_signal(self):
        """65 days of high price then a sharp drop on the last day triggers a death cross."""
        prices = [15.0] * 64 + [10.0]
        _make_ohlcv_sequence(self.asset, prices)
        calculate_ma_signals_for_asset(self.asset.id)
        self.assertTrue(
            SignalEvent.objects.filter(asset=self.asset, signal_type='DEATH_CROSS').exists()
        )

    # ------------------------------------------------------------------
    # Bollinger Band signals
    # ------------------------------------------------------------------
    def test_bb_squeeze_creates_signal(self):
        """Very narrow price range → bandwidth < 5% → BB_SQUEEZE."""
        # 40 days of price ≈ 10.0 with tiny variation (std dev ≈ 0)
        prices = [10.0 + (i % 2) * 0.001 for i in range(40)]
        _make_ohlcv_sequence(self.asset, prices)
        calculate_bollinger_signals_for_asset(self.asset.id)
        self.assertTrue(
            SignalEvent.objects.filter(asset=self.asset, signal_type='BB_SQUEEZE').exists()
        )

    def test_bb_breakout_up_creates_signal(self):
        """Close above upper Bollinger Band → BB_BREAKOUT_UP."""
        # 35 days near 10, then a spike to 20 (far above upper band)
        prices = [10.0] * 34 + [20.0]
        _make_ohlcv_sequence(self.asset, prices)
        calculate_bollinger_signals_for_asset(self.asset.id)
        self.assertTrue(
            SignalEvent.objects.filter(asset=self.asset, signal_type='BB_BREAKOUT_UP').exists()
        )

    # ------------------------------------------------------------------
    # Volume signals
    # ------------------------------------------------------------------
    def test_volume_spike_creates_signal(self):
        """Last-day volume 3x the 20-day average → VOLUME_SPIKE."""
        prices = [10.0] * 25
        _make_ohlcv_sequence(self.asset, prices, volume=1000)
        # Override the last row's volume to create a spike
        latest = OHLCV.objects.filter(asset=self.asset).order_by('-date').first()
        latest.volume = 3000
        latest.save()
        calculate_volume_signals_for_asset(self.asset.id)
        self.assertTrue(
            SignalEvent.objects.filter(asset=self.asset, signal_type='VOLUME_SPIKE').exists()
        )

    # ------------------------------------------------------------------
    # Momentum signals
    # ------------------------------------------------------------------
    def test_momentum_up_5d_creates_signal(self):
        """Price rises > 5% in 5 days → MOMENTUM_UP_5D."""
        # 21 days at 10, then 5 days at 11 (10% rise)
        prices = [10.0] * 21 + [11.0] * 5
        _make_ohlcv_sequence(self.asset, prices)
        calculate_momentum_signals_for_asset(self.asset.id)
        self.assertTrue(
            SignalEvent.objects.filter(asset=self.asset, signal_type='MOMENTUM_UP_5D').exists()
        )
        # MOM_5D TechnicalIndicator should also be stored
        self.assertTrue(
            TechnicalIndicator.objects.filter(asset=self.asset, indicator_type='MOM_5D').exists()
        )

    def test_momentum_down_5d_creates_signal(self):
        """Price falls > 5% in 5 days → MOMENTUM_DOWN_5D."""
        prices = [11.0] * 21 + [10.0] * 5
        _make_ohlcv_sequence(self.asset, prices)
        calculate_momentum_signals_for_asset(self.asset.id)
        self.assertTrue(
            SignalEvent.objects.filter(asset=self.asset, signal_type='MOMENTUM_DOWN_5D').exists()
        )

    # ------------------------------------------------------------------
    # API endpoint tests
    # ------------------------------------------------------------------
    def test_signals_list_requires_authentication(self):
        response = self.client.get('/api/v1/signals/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_signals_list_returns_results(self):
        self._auth()
        ts = timezone.now()
        SignalEvent.objects.create(
            asset=self.asset,
            signal_type='GOLDEN_CROSS',
            timestamp=ts,
            description='Test signal',
        )
        response = self.client.get('/api/v1/signals/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(response.data['count'], 1)

    def test_signals_recent_endpoint(self):
        self._auth()
        ts = timezone.now() - timedelta(days=2)
        old_ts = timezone.now() - timedelta(days=30)
        SignalEvent.objects.create(
            asset=self.asset, signal_type='GOLDEN_CROSS', timestamp=ts, description='Recent',
        )
        SignalEvent.objects.create(
            asset=self.asset, signal_type='DEATH_CROSS', timestamp=old_ts, description='Old',
        )
        response = self.client.get('/api/v1/signals/recent/?days=7')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        symbols = [r['signal_type'] for r in response.data['results']]
        self.assertIn('GOLDEN_CROSS', symbols)
        self.assertNotIn('DEATH_CROSS', symbols)

    def test_signals_filter_by_signal_type(self):
        self._auth()
        ts = timezone.now()
        SignalEvent.objects.create(
            asset=self.asset, signal_type='GOLDEN_CROSS', timestamp=ts,
        )
        SignalEvent.objects.create(
            asset=self.asset, signal_type='BB_SQUEEZE',
            timestamp=ts - timedelta(minutes=1),
        )
        response = self.client.get('/api/v1/signals/?signal_type=GOLDEN_CROSS')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for item in response.data['results']:
            self.assertEqual(item['signal_type'], 'GOLDEN_CROSS')

    @patch('apps.analytics.views.calculate_signals_for_all_assets.delay')
    def test_signals_recalculate_endpoint(self, mock_delay):
        self._auth()
        response = self.client.post('/api/v1/signals/recalculate/')
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_delay.assert_called_once()
