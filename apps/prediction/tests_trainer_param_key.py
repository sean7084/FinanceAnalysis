"""The trainer's param_key scoring must preserve the jsonb scoring it replaced.

The risk in moving from the jsonb payload to param_key is the 1-vs-2 score distinction:
``_indicator_frame_from_queryset`` sorts by parameter_score and keeps the LAST row per
(asset_id, timestamp), so an exact parameter match must outrank a parameterless row.
Pushing an equality filter into SQL instead would have destroyed that tie-break.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.analytics.models import TechnicalIndicator
from apps.core.date_floor import session_key_timestamp
from apps.markets.models import Asset, Market
from apps.prediction.tasks_lightgbm import _indicator_frame_from_queryset


class TrainerParamKeyScoringTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.market = Market.objects.create(code='CN', name='China')
        cls.asset = Asset.objects.create(
            market=cls.market, symbol='600100', ts_code='600100.SH', name='Trainer',
            list_date=date(2015, 1, 1),
        )
        cls.stamp = session_key_timestamp(date(2026, 11, 6))

    def _row(self, value, parameters):
        return TechnicalIndicator.objects.create(
            asset=self.asset, timestamp=self.stamp, indicator_type='RSI',
            value=Decimal(value), parameters=parameters,
        )

    def _frame(self):
        queryset = TechnicalIndicator.objects.filter(
            asset_id=self.asset.pk, indicator_type='RSI',
        ).values('asset_id', 'timestamp', 'value', 'param_key').order_by('asset_id', 'timestamp')
        return _indicator_frame_from_queryset(
            queryset, 'rsi', parameter_key='timeperiod', parameter_value=14,
        )

    def test_exact_match_outranks_parameterless_row(self):
        self._row('11.00000000', {})
        self._row('22.00000000', {'timeperiod': 14})
        frame = self._frame()
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame['rsi'].iloc[0], 22.0)

    def test_order_of_insertion_does_not_change_the_winner(self):
        self._row('22.00000000', {'timeperiod': 14})
        self._row('11.00000000', {})
        frame = self._frame()
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame['rsi'].iloc[0], 22.0)

    def test_mismatched_variant_is_dropped(self):
        self._row('33.00000000', {'timeperiod': 7})
        frame = self._frame()
        self.assertEqual(len(frame), 0)

    def test_parameterless_row_survives_when_no_exact_match_exists(self):
        self._row('11.00000000', {})
        frame = self._frame()
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame['rsi'].iloc[0], 11.0)

    def test_unbackfilled_null_key_is_excluded_not_treated_as_parameterless(self):
        """A NULL key means "variant unknown", not "parameterless".

        Scoring it 1 would let a WRONG variant survive and win the drop_duplicates
        tie-break, silently corrupting the feature matrix before the backfill finishes.
        """
        row = self._row('33.00000000', {'timeperiod': 7})
        TechnicalIndicator.objects.filter(pk=row.pk).update(param_key=None)
        frame = self._frame()
        self.assertEqual(len(frame), 0)

    def test_null_key_row_cannot_outrank_an_exact_match(self):
        wrong = self._row('11.00000000', {'timeperiod': 7})
        TechnicalIndicator.objects.filter(pk=wrong.pk).update(param_key=None)
        self._row('22.00000000', {'timeperiod': 14})
        frame = self._frame()
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame['rsi'].iloc[0], 22.0)

    def test_projection_does_not_fetch_the_jsonb_payload(self):
        self._row('22.00000000', {'timeperiod': 14})
        queryset = TechnicalIndicator.objects.filter(
            asset_id=self.asset.pk, indicator_type='RSI',
        ).values('asset_id', 'timestamp', 'value', 'param_key')
        self.assertNotIn('parameters', queryset.query.values_select)
        self.assertIn('param_key', queryset.query.values_select)