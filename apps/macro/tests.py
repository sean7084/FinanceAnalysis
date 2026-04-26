from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import pandas as pd
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.factors.models import FactorScore
from apps.markets.models import Market, Asset
from . import providers
from .models import MacroSnapshot, MarketContext, EventImpactStat
from .tasks import refresh_current_market_context, sync_market_context_for_snapshot


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

    def test_refresh_context_preserves_history_and_reuses_same_month(self):
        january = MacroSnapshot.objects.create(
            date=date(2024, 1, 1),
            pmi_manufacturing=Decimal('48.2'),
            pmi_non_manufacturing=Decimal('49.0'),
            cn10y_yield=Decimal('2.10'),
            cn2y_yield=Decimal('2.35'),
            cpi_yoy=Decimal('1.1'),
        )
        february = MacroSnapshot.objects.create(
            date=date(2024, 2, 1),
            pmi_manufacturing=Decimal('52.4'),
            pmi_non_manufacturing=Decimal('53.1'),
            cn10y_yield=Decimal('2.40'),
            cn2y_yield=Decimal('2.10'),
            cpi_yoy=Decimal('1.0'),
        )

        refresh_current_market_context(snapshot_id=january.id)
        refresh_current_market_context(snapshot_id=february.id)
        refresh_current_market_context(snapshot_id=february.id, event_tag='rate_cut_cycle')

        contexts = list(MarketContext.objects.filter(context_key='current').order_by('starts_at', 'updated_at'))
        self.assertEqual(len(contexts), 2)
        self.assertTrue(all(context.is_active for context in contexts))
        self.assertEqual(contexts[0].starts_at, date(2024, 1, 1))
        self.assertEqual(contexts[0].ends_at, date(2024, 1, 31))
        self.assertEqual(contexts[0].macro_phase, MarketContext.MacroPhase.RECESSION)
        self.assertEqual(contexts[1].starts_at, date(2024, 2, 1))
        self.assertIsNone(contexts[1].ends_at)
        self.assertEqual(contexts[1].macro_phase, MarketContext.MacroPhase.OVERHEAT)
        self.assertEqual(contexts[1].event_tag, 'rate_cut_cycle')

    def test_sync_market_context_prefers_active_duplicate_for_same_month(self):
        snapshot = MacroSnapshot.objects.create(
            date=date(2024, 4, 1),
            pmi_manufacturing=Decimal('48.2'),
            pmi_non_manufacturing=Decimal('49.0'),
            cn10y_yield=Decimal('2.10'),
            cn2y_yield=Decimal('2.35'),
            cpi_yoy=Decimal('1.1'),
        )
        active_context = MarketContext.objects.create(
            context_key='current',
            macro_phase=MarketContext.MacroPhase.RECOVERY,
            starts_at=snapshot.date,
            is_active=True,
        )
        inactive_duplicate = MarketContext.objects.create(
            context_key='current',
            macro_phase=MarketContext.MacroPhase.OVERHEAT,
            starts_at=snapshot.date,
            is_active=False,
        )

        context, created = sync_market_context_for_snapshot(snapshot)
        active_context.refresh_from_db()
        inactive_duplicate.refresh_from_db()

        self.assertFalse(created)
        self.assertEqual(context.id, active_context.id)
        self.assertTrue(active_context.is_active)
        self.assertFalse(inactive_duplicate.is_active)
        self.assertEqual(MarketContext.objects.filter(context_key='current', starts_at=snapshot.date, is_active=True).count(), 1)

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

    def test_backfill_market_context_builds_history_from_macro_snapshots(self):
        MacroSnapshot.objects.create(
            date=date(2004, 12, 1),
            cpi_yoy=Decimal('1.1'),
        )
        MacroSnapshot.objects.create(
            date=date(2005, 1, 1),
            pmi_manufacturing=Decimal('54.7'),
            pmi_non_manufacturing=Decimal('54.0'),
            cn10y_yield=Decimal('2.40'),
            cn2y_yield=Decimal('2.10'),
            cpi_yoy=Decimal('1.0'),
        )
        MacroSnapshot.objects.create(
            date=date(2005, 2, 1),
            pmi_manufacturing=Decimal('49.0'),
            pmi_non_manufacturing=Decimal('50.0'),
            cpi_yoy=Decimal('3.1'),
        )
        MacroSnapshot.objects.create(
            date=date(2005, 3, 1),
            pmi_manufacturing=Decimal('48.0'),
            pmi_non_manufacturing=Decimal('49.0'),
            cn10y_yield=Decimal('2.10'),
            cn2y_yield=Decimal('2.30'),
            cpi_yoy=Decimal('1.5'),
        )
        MacroSnapshot.objects.create(
            date=date(2005, 4, 1),
            pmi_manufacturing=Decimal('50.5'),
            pmi_non_manufacturing=Decimal('50.2'),
            cpi_yoy=Decimal('1.2'),
        )

        output = StringIO()
        call_command(
            'backfill_market_context',
            start_date='2004-12-01',
            end_date='2005-04-30',
            stdout=output,
        )

        contexts = list(MarketContext.objects.filter(context_key='current').order_by('starts_at'))
        self.assertEqual([context.starts_at for context in contexts], [
            date(2005, 1, 1),
            date(2005, 2, 1),
            date(2005, 3, 1),
            date(2005, 4, 1),
        ])
        self.assertTrue(all(context.is_active for context in contexts))
        self.assertEqual([context.macro_phase for context in contexts], [
            MarketContext.MacroPhase.OVERHEAT,
            MarketContext.MacroPhase.STAGFLATION,
            MarketContext.MacroPhase.RECESSION,
            MarketContext.MacroPhase.RECOVERY,
        ])
        self.assertEqual(contexts[0].ends_at, date(2005, 1, 31))
        self.assertEqual(contexts[1].ends_at, date(2005, 2, 28))
        self.assertEqual(contexts[2].ends_at, date(2005, 3, 31))
        self.assertIsNone(contexts[3].ends_at)
        self.assertIn('MarketContext backfill completed: created=4, updated=0', output.getvalue())

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


class MacroProviderAndBackfillTests(TestCase):
    @patch('apps.macro.providers._sleep_if_needed')
    @patch('apps.macro.providers._safe_tushare_client')
    def test_fetch_macro_snapshot_from_tushare_reads_uppercase_pmi_month(self, mock_client, _mock_sleep):
        class StubClient:
            def cn_cpi(self, **kwargs):
                return pd.DataFrame([
                    {'month': '202604', 'nt_yoy': 0.8},
                ])

            def cn_ppi(self, **kwargs):
                return pd.DataFrame([
                    {'month': '202604', 'ppi_yoy': -1.1},
                ])

            def cn_pmi(self, **kwargs):
                return pd.DataFrame([
                    {'MONTH': '202603', 'CREATE_TIME': '2026-04-15 13:31:13', 'PMI010000': 50.4, 'PMI020100': 50.1},
                ])

            def yc_cb(self, curve_term, **kwargs):
                return pd.DataFrame([
                    {'trade_date': '20260331', 'yield': 2.5 if curve_term == 10 else 2.1},
                ])

            def fx_daily(self, ts_code, **kwargs):
                return pd.DataFrame([])

        mock_client.return_value = StubClient()

        payload = providers.fetch_macro_snapshot_from_tushare(snapshot_date=timezone.datetime(2026, 4, 1).date())

        self.assertEqual(payload['date'], timezone.datetime(2026, 3, 1).date())
        self.assertEqual(payload['pmi_manufacturing'], Decimal('50.4'))
        self.assertEqual(payload['pmi_non_manufacturing'], Decimal('50.1'))

    @patch('apps.macro.providers._sleep_if_needed')
    @patch('apps.macro.providers._safe_tushare_client')
    def test_fetch_macro_snapshot_from_tushare_reads_bid_ask_fx_quotes(self, mock_client, _mock_sleep):
        class StubClient:
            def cn_cpi(self, **kwargs):
                return pd.DataFrame([
                    {'month': '202604', 'nt_yoy': 0.8},
                ])

            def cn_ppi(self, **kwargs):
                return pd.DataFrame([
                    {'month': '202604', 'ppi_yoy': -1.1},
                ])

            def cn_pmi(self, **kwargs):
                return pd.DataFrame([
                    {'month': '202604', 'PMI010000': 50.2, 'PMI020100': 51.0},
                ])

            def yc_cb(self, curve_term, **kwargs):
                return pd.DataFrame([
                    {'trade_date': '20260423', 'yield': 2.5 if curve_term == 10 else 2.1},
                ])

            def fx_daily(self, ts_code, **kwargs):
                if ts_code == 'USDOLLAR.FXCM':
                    return pd.DataFrame([])
                if ts_code == 'USDOLLAR':
                    return pd.DataFrame([
                        {'trade_date': '20260423', 'bid_close': 12642.0, 'ask_close': 12644.0},
                    ])
                if ts_code == 'USDCNH.FXCM':
                    return pd.DataFrame([
                        {'trade_date': '20260423', 'bid_close': 6.83638, 'ask_close': 6.83693},
                    ])
                raise AssertionError(f'unexpected ts_code: {ts_code}')

        mock_client.return_value = StubClient()

        payload = providers.fetch_macro_snapshot_from_tushare(snapshot_date=timezone.datetime(2026, 4, 1).date())

        self.assertEqual(payload['dxy'], Decimal('126.4300'))
        self.assertEqual(payload['cny_usd'], Decimal('0.1463'))
        self.assertEqual(payload['metadata']['field_sources']['dxy'], 'tushare')
        self.assertEqual(payload['metadata']['fx_quote_sources']['cny_usd'], 'USDCNH.FXCM')

    @patch('apps.macro.providers._sleep_if_needed')
    @patch('apps.macro.providers._safe_tushare_client')
    def test_fetch_macro_snapshot_from_tushare_prefers_primary_curve_type_for_yields(self, mock_client, _mock_sleep):
        class StubClient:
            def cn_cpi(self, **kwargs):
                return pd.DataFrame([
                    {'month': '202604', 'nt_yoy': 0.8},
                ])

            def cn_ppi(self, **kwargs):
                return pd.DataFrame([
                    {'month': '202604', 'ppi_yoy': -1.1},
                ])

            def cn_pmi(self, **kwargs):
                return pd.DataFrame([
                    {'month': '202604', 'PMI010000': 50.2, 'PMI020100': 51.0},
                ])

            def yc_cb(self, curve_term, **kwargs):
                if curve_term == 10:
                    return pd.DataFrame([
                        {'trade_date': '20260423', 'curve_type': '1', 'yield': 2.7},
                        {'trade_date': '20260423', 'curve_type': '0', 'yield': 2.5},
                    ])
                return pd.DataFrame([
                    {'trade_date': '20260423', 'curve_type': '1', 'yield': 2.2},
                    {'trade_date': '20260423', 'curve_type': '0', 'yield': 2.1},
                ])

            def fx_daily(self, ts_code, **kwargs):
                if ts_code == 'USDOLLAR.FXCM':
                    return pd.DataFrame([])
                if ts_code == 'USDOLLAR':
                    return pd.DataFrame([
                        {'trade_date': '20260423', 'bid_close': 12642.0, 'ask_close': 12644.0},
                    ])
                if ts_code == 'USDCNH.FXCM':
                    return pd.DataFrame([
                        {'trade_date': '20260423', 'bid_close': 6.83638, 'ask_close': 6.83693},
                    ])
                raise AssertionError(f'unexpected ts_code: {ts_code}')

        mock_client.return_value = StubClient()

        payload = providers.fetch_macro_snapshot_from_tushare(snapshot_date=timezone.datetime(2026, 4, 1).date())

        self.assertEqual(payload['cn10y_yield'], Decimal('2.5'))
        self.assertEqual(payload['cn2y_yield'], Decimal('2.1'))
        self.assertEqual(payload['metadata']['yield_sources']['cn10y_yield']['curve_type'], '0')

    @patch('apps.macro.providers._sleep_if_needed')
    @patch('apps.macro.providers._safe_tushare_client')
    def test_fetch_macro_snapshot_from_tushare_uses_legacy_dxy_code_for_older_history(self, mock_client, _mock_sleep):
        class StubClient:
            def cn_cpi(self, **kwargs):
                return pd.DataFrame([
                    {'month': '201101', 'nt_yoy': 5.1},
                ])

            def cn_ppi(self, **kwargs):
                return pd.DataFrame([
                    {'month': '201101', 'ppi_yoy': 6.8},
                ])

            def cn_pmi(self, **kwargs):
                return pd.DataFrame([
                    {'month': '201101', 'PMI010000': 52.1, 'PMI020100': None},
                ])

            def yc_cb(self, curve_term, **kwargs):
                return pd.DataFrame([
                    {'trade_date': '20110131', 'yield': 4.1 if curve_term == 10 else 3.0},
                ])

            def fx_daily(self, ts_code, **kwargs):
                if ts_code == 'USDOLLAR.FXCM':
                    return pd.DataFrame([
                        {'trade_date': '20110103', 'bid_close': 8010.0, 'ask_close': 8012.0},
                    ])
                if ts_code == 'USDOLLAR':
                    return pd.DataFrame([])
                if ts_code == 'USDCNH.FXCM':
                    return pd.DataFrame([])
                raise AssertionError(f'unexpected ts_code: {ts_code}')

        mock_client.return_value = StubClient()

        payload = providers.fetch_macro_snapshot_from_tushare(snapshot_date=timezone.datetime(2011, 1, 1).date())

        self.assertEqual(payload['dxy'], Decimal('80.1100'))
        self.assertEqual(payload['metadata']['fx_quote_sources']['dxy'], 'USDOLLAR.FXCM')

    @patch('apps.macro.providers.fetch_macro_snapshot_from_akshare')
    @patch('apps.macro.providers.fetch_macro_snapshot_from_tushare')
    def test_fetch_macro_snapshot_with_fallback_backfills_missing_fx_fields(self, mock_tushare, mock_akshare):
        mock_tushare.return_value = {
            'date': timezone.now().date().replace(day=1),
            'dxy': None,
            'cny_usd': None,
            'cn10y_yield': Decimal('2.30'),
            'cn2y_yield': Decimal('2.10'),
            'pmi_manufacturing': Decimal('50.2'),
            'pmi_non_manufacturing': Decimal('51.3'),
            'cpi_yoy': Decimal('0.8'),
            'ppi_yoy': Decimal('-1.1'),
            'metadata': {'source': 'tushare', 'source_used': 'tushare'},
        }
        mock_akshare.return_value = {
            'date': timezone.now().date().replace(day=1),
            'dxy': Decimal('104.2'),
            'cny_usd': Decimal('0.1389'),
            'cn10y_yield': None,
            'cn2y_yield': None,
            'pmi_manufacturing': None,
            'pmi_non_manufacturing': None,
            'cpi_yoy': None,
            'ppi_yoy': None,
            'metadata': {'source': 'akshare', 'source_used': 'akshare'},
        }

        payload = providers.fetch_macro_snapshot_with_fallback()

        self.assertEqual(payload['dxy'], Decimal('104.2'))
        self.assertEqual(payload['cny_usd'], Decimal('0.1389'))
        self.assertEqual(payload['cn10y_yield'], Decimal('2.30'))
        self.assertEqual(payload['metadata']['source_used'], 'tushare+akshare')
        self.assertEqual(payload['metadata']['fallback_fields'], ['dxy', 'cny_usd'])
        self.assertEqual(payload['metadata']['field_sources']['cn10y_yield'], 'tushare')
        self.assertEqual(payload['metadata']['field_sources']['cny_usd'], 'akshare')

    @patch('apps.macro.management.commands.backfill_macro_snapshots.refresh_current_market_context')
    @patch('apps.macro.management.commands.backfill_macro_snapshots.ts.pro_api')
    @patch('apps.macro.management.commands.backfill_macro_snapshots.fetch_macro_snapshot_from_akshare')
    @patch('apps.macro.management.commands.backfill_macro_snapshots._sleep_if_needed')
    @patch('apps.macro.management.commands.backfill_macro_snapshots._yield_sleep_if_needed')
    @patch('apps.macro.providers.sleep')
    @patch('apps.macro.management.commands.backfill_macro_snapshots.settings.TUSHARE_TOKEN', 'test-token')
    def test_backfill_macro_snapshots_reads_uppercase_pmi_month(self, mock_provider_sleep, mock_yield_sleep, mock_command_sleep, mock_fallback, mock_pro_api, mock_refresh):
        class StubPro:
            def cn_cpi(self, **kwargs):
                return pd.DataFrame([])

            def cn_ppi(self, **kwargs):
                return pd.DataFrame([])

            def cn_pmi(self, **kwargs):
                return pd.DataFrame([
                    {'MONTH': '202603', 'CREATE_TIME': '2026-04-15 13:31:13', 'PMI010000': 50.4, 'PMI020100': 50.1},
                    {'MONTH': '202602', 'CREATE_TIME': '2026-04-15 13:31:13', 'PMI010000': 49.0, 'PMI020100': 49.5},
                ])

            def yc_cb(self, curve_term, **kwargs):
                return pd.DataFrame([])

            def fx_daily(self, ts_code, **kwargs):
                return pd.DataFrame([])

        mock_pro_api.return_value = StubPro()
        mock_fallback.return_value = {
            'date': timezone.now().date().replace(day=1),
            'dxy': None,
            'cny_usd': None,
            'cn10y_yield': None,
            'cn2y_yield': None,
            'pmi_manufacturing': None,
            'pmi_non_manufacturing': None,
            'cpi_yoy': None,
            'ppi_yoy': None,
            'metadata': {'source': 'akshare', 'source_used': 'akshare'},
        }

        call_command(
            'backfill_macro_snapshots',
            start_date='2026-02-01',
            end_date='2026-03-31',
        )

        feb_snapshot = MacroSnapshot.objects.get(date='2026-02-01')
        mar_snapshot = MacroSnapshot.objects.get(date='2026-03-01')
        self.assertEqual(feb_snapshot.pmi_manufacturing, Decimal('49.0'))
        self.assertEqual(feb_snapshot.pmi_non_manufacturing, Decimal('49.5'))
        self.assertEqual(mar_snapshot.pmi_manufacturing, Decimal('50.4'))
        self.assertEqual(mar_snapshot.pmi_non_manufacturing, Decimal('50.1'))
        mock_refresh.assert_called_once()

    @patch('apps.macro.management.commands.backfill_macro_snapshots.refresh_current_market_context')
    @patch('apps.macro.management.commands.backfill_macro_snapshots.ts.pro_api')
    @patch('apps.macro.management.commands.backfill_macro_snapshots.fetch_macro_snapshot_from_akshare')
    @patch('apps.macro.management.commands.backfill_macro_snapshots._sleep_if_needed')
    @patch('apps.macro.management.commands.backfill_macro_snapshots._yield_sleep_if_needed')
    @patch('apps.macro.providers.sleep')
    @patch('apps.macro.management.commands.backfill_macro_snapshots.settings.TUSHARE_TOKEN', 'test-token')
    def test_backfill_macro_snapshots_retries_yields_and_fills_missing_fx_per_field(
        self,
        mock_provider_sleep,
        mock_yield_sleep,
        mock_command_sleep,
        mock_fallback,
        mock_pro_api,
        mock_refresh,
    ):
        class StubPro:
            def __init__(self):
                self.yield_10y_calls = 0

            def cn_cpi(self, **kwargs):
                return pd.DataFrame([
                    {'month': '202401', 'nt_yoy': 0.8},
                ])

            def cn_ppi(self, **kwargs):
                return pd.DataFrame([
                    {'month': '202401', 'ppi_yoy': -1.1},
                ])

            def cn_pmi(self, **kwargs):
                return pd.DataFrame([
                    {'month': '202401', 'PMI010000': 50.2, 'PMI020100': 50.7},
                ])

            def yc_cb(self, curve_term, **kwargs):
                if curve_term == 10:
                    self.yield_10y_calls += 1
                    if self.yield_10y_calls == 1:
                        raise Exception('抱歉，您每分钟最多访问该接口2次')
                    return pd.DataFrame([
                        {'trade_date': '20240131', 'curve_type': '1', 'yield': 2.7},
                        {'trade_date': '20240131', 'curve_type': '0', 'yield': 2.5},
                    ])
                return pd.DataFrame([
                    {'trade_date': '20240131', 'curve_type': '1', 'yield': 2.2},
                    {'trade_date': '20240131', 'curve_type': '0', 'yield': 2.1},
                ])

            def fx_daily(self, ts_code, **kwargs):
                if ts_code == 'USDOLLAR.FXCM':
                    return pd.DataFrame([])
                if ts_code == 'USDOLLAR':
                    return pd.DataFrame([
                        {'trade_date': '20240131', 'bid_close': 10423.0, 'ask_close': 10425.0},
                    ])
                if ts_code == 'USDCNH.FXCM':
                    return pd.DataFrame([])
                raise AssertionError(f'unexpected ts_code: {ts_code}')

        mock_pro_api.return_value = StubPro()
        mock_fallback.return_value = {
            'date': timezone.now().date().replace(day=1),
            'dxy': Decimal('104.2'),
            'cny_usd': Decimal('0.1389'),
            'cn10y_yield': None,
            'cn2y_yield': None,
            'pmi_manufacturing': None,
            'pmi_non_manufacturing': None,
            'cpi_yoy': None,
            'ppi_yoy': None,
            'metadata': {'source': 'akshare', 'source_used': 'akshare'},
        }

        output = StringIO()
        call_command(
            'backfill_macro_snapshots',
            start_date='2024-01-01',
            end_date='2024-01-31',
            stdout=output,
        )

        snapshot = MacroSnapshot.objects.get(date='2024-01-01')
        self.assertEqual(snapshot.cn10y_yield, Decimal('2.5'))
        self.assertEqual(snapshot.cn2y_yield, Decimal('2.1'))
        self.assertEqual(snapshot.dxy, Decimal('104.2400'))
        self.assertEqual(snapshot.cny_usd, Decimal('0.1389'))
        self.assertEqual(snapshot.metadata['retries']['yield_10y'], 1)
        self.assertEqual(snapshot.metadata['yield_sources']['cn10y_yield']['curve_type'], '0')
        self.assertEqual(snapshot.metadata['fallback_fields'], ['cny_usd'])
        self.assertIn('yield_10y', snapshot.metadata['retry_errors'])
        self.assertIn('fallback_used=1', output.getvalue())
        mock_refresh.assert_called_once()

    @patch('apps.macro.management.commands.backfill_macro_snapshots.refresh_current_market_context')
    @patch('apps.macro.management.commands.backfill_macro_snapshots.ts.pro_api')
    @patch('apps.macro.management.commands.backfill_macro_snapshots.fetch_macro_snapshot_from_akshare')
    @patch('apps.macro.management.commands.backfill_macro_snapshots._sleep_if_needed')
    @patch('apps.macro.management.commands.backfill_macro_snapshots._yield_sleep_if_needed')
    @patch('apps.macro.providers.sleep')
    @patch('apps.macro.management.commands.backfill_macro_snapshots.settings.TUSHARE_TOKEN', 'test-token')
    def test_backfill_macro_snapshots_resume_yields_skips_completed_months(
        self,
        mock_provider_sleep,
        mock_yield_sleep,
        mock_command_sleep,
        mock_fallback,
        mock_pro_api,
        mock_refresh,
    ):
        class StubPro:
            def __init__(self):
                self.yc_cb_calls = []

            def cn_cpi(self, **kwargs):
                return pd.DataFrame([
                    {'month': '202401', 'nt_yoy': 0.8},
                    {'month': '202402', 'nt_yoy': 0.9},
                ])

            def cn_ppi(self, **kwargs):
                return pd.DataFrame([
                    {'month': '202401', 'ppi_yoy': -1.1},
                    {'month': '202402', 'ppi_yoy': -0.8},
                ])

            def cn_pmi(self, **kwargs):
                return pd.DataFrame([
                    {'month': '202401', 'PMI010000': 50.2, 'PMI020100': 50.7},
                    {'month': '202402', 'PMI010000': 50.5, 'PMI020100': 51.2},
                ])

            def yc_cb(self, curve_term, **kwargs):
                self.yc_cb_calls.append((curve_term, kwargs['start_date'], kwargs['end_date']))
                return pd.DataFrame([
                    {'trade_date': '20240229', 'curve_type': '0', 'yield': 2.5 if curve_term == 10 else 2.1},
                ])

            def fx_daily(self, ts_code, **kwargs):
                if ts_code == 'USDOLLAR.FXCM':
                    return pd.DataFrame([])
                if ts_code == 'USDOLLAR':
                    return pd.DataFrame([
                        {'trade_date': '20240229', 'bid_close': 10423.0, 'ask_close': 10425.0},
                    ])
                if ts_code == 'USDCNH.FXCM':
                    return pd.DataFrame([
                        {'trade_date': '20240229', 'bid_close': 7.2, 'ask_close': 7.2},
                    ])
                raise AssertionError(f'unexpected ts_code: {ts_code}')

        MacroSnapshot.objects.create(
            date='2024-01-01',
            cn10y_yield=Decimal('2.4'),
            cn2y_yield=Decimal('2.0'),
            metadata={'yield_sources': {'cn10y_yield': {'curve_type': '0'}}},
        )

        stub_pro = StubPro()
        mock_pro_api.return_value = stub_pro
        mock_fallback.return_value = {
            'date': timezone.now().date().replace(day=1),
            'dxy': None,
            'cny_usd': None,
            'cn10y_yield': None,
            'cn2y_yield': None,
            'pmi_manufacturing': None,
            'pmi_non_manufacturing': None,
            'cpi_yoy': None,
            'ppi_yoy': None,
            'metadata': {'source': 'akshare', 'source_used': 'akshare'},
        }

        call_command(
            'backfill_macro_snapshots',
            start_date='2024-01-01',
            end_date='2024-02-29',
            resume_yields=True,
        )

        feb_snapshot = MacroSnapshot.objects.get(date='2024-02-01')
        self.assertEqual(feb_snapshot.cn10y_yield, Decimal('2.5'))
        self.assertEqual(feb_snapshot.cn2y_yield, Decimal('2.1'))
        self.assertEqual(stub_pro.yc_cb_calls, [
            (10, '20240201', '20240229'),
            (2, '20240201', '20240229'),
        ])
        mock_refresh.assert_called_once()
