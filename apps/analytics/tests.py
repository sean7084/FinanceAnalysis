from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.markets.models import Market, Asset, OHLCV
from .models import AlertRule, AlertEvent, TechnicalIndicator
from .tasks import check_alert_rules, calculate_fibonacci_retracement_for_asset


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
