from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.factors.models import FactorScore
from apps.markets.models import Market, Asset
from .models import MacroSnapshot, MarketContext, EventImpactStat
from .tasks import refresh_current_market_context


class Phase12MacroTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='phase12_user',
            email='phase12@example.com',
            password='Passw0rd!123',
        )
        self.market = Market.objects.create(code='P12', name='Phase 12 Market')
        self.asset1 = Asset.objects.create(
            market=self.market,
            symbol='601001',
            ts_code='601001.SH',
            name='Macro Asset 1',
        )
        self.asset2 = Asset.objects.create(
            market=self.market,
            symbol='601002',
            ts_code='601002.SH',
            name='Macro Asset 2',
        )

    def _auth(self):
        self.client.force_authenticate(user=self.user)

    def test_macro_snapshots_requires_auth(self):
        response = self.client.get('/api/v1/macro/snapshots/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_refresh_context_infers_recession(self):
        snap = MacroSnapshot.objects.create(
            date=timezone.now().date(),
            pmi_manufacturing=Decimal('48.2'),
            pmi_non_manufacturing=Decimal('49.0'),
            cn10y_yield=Decimal('2.10'),
            cn2y_yield=Decimal('2.35'),
            cpi_yoy=Decimal('1.1'),
        )
        refresh_current_market_context(snapshot_id=snap.id, event_tag='trade_war')
        current = MarketContext.objects.filter(context_key='current', is_active=True).first()
        self.assertIsNotNone(current)
        self.assertEqual(current.macro_phase, MarketContext.MacroPhase.RECESSION)
        self.assertEqual(current.event_tag, 'trade_war')

    def test_market_context_current_endpoint(self):
        self._auth()
        MarketContext.objects.create(
            context_key='current',
            macro_phase=MarketContext.MacroPhase.RECOVERY,
            event_tag='rate_cut_cycle',
            is_active=True,
            starts_at=timezone.now().date() - timedelta(days=1),
        )
        response = self.client.get('/api/v1/macro/contexts/current/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['macro_phase'], MarketContext.MacroPhase.RECOVERY)

    def test_event_impact_list_filter(self):
        self._auth()
        EventImpactStat.objects.create(
            event_tag='trade_war',
            sector='technology',
            horizon_days=20,
            avg_return=Decimal('-0.021'),
            excess_return=Decimal('-0.012'),
            sample_size=15,
        )
        EventImpactStat.objects.create(
            event_tag='rate_cut_cycle',
            sector='financials',
            horizon_days=20,
            avg_return=Decimal('0.031'),
            excess_return=Decimal('0.014'),
            sample_size=9,
        )
        response = self.client.get('/api/v1/macro/event-impacts/?event_tag=trade_war')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['event_tag'], 'trade_war')

    @patch('apps.factors.views.calculate_factor_scores_for_date.delay')
    def test_bottom_candidates_recalculate_accepts_macro_context(self, mock_delay):
        self._auth()
        payload = {
            'macro_context': 'RECESSION',
            'event_tag': 'trade_war',
            'financial_weight': 0.4,
            'flow_weight': 0.3,
            'technical_weight': 0.3,
        }
        response = self.client.post('/api/v1/screener/bottom-candidates/recalculate/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data['macro_context'], 'RECESSION')
        self.assertEqual(response.data['event_tag'], 'trade_war')
        mock_delay.assert_called_once()

    def test_bottom_candidates_list_with_context_exposes_adjusted_score(self):
        self._auth()
        d = timezone.now().date()
        FactorScore.objects.create(
            asset=self.asset1,
            date=d,
            mode=FactorScore.FactorMode.COMPOSITE,
            fundamental_score=Decimal('0.90'),
            capital_flow_score=Decimal('0.20'),
            technical_score=Decimal('0.50'),
            composite_score=Decimal('0.57'),
            bottom_probability_score=Decimal('0.57'),
        )
        FactorScore.objects.create(
            asset=self.asset2,
            date=d,
            mode=FactorScore.FactorMode.COMPOSITE,
            fundamental_score=Decimal('0.30'),
            capital_flow_score=Decimal('0.70'),
            technical_score=Decimal('0.50'),
            composite_score=Decimal('0.49'),
            bottom_probability_score=Decimal('0.49'),
        )

        response = self.client.get('/api/v1/screener/bottom-candidates/?macro_context=RECESSION&event_tag=trade_war')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('adjusted_bottom_probability_score', response.data['results'][0])
        self.assertIn('context_applied', response.data['results'][0])
