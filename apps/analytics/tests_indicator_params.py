"""Canonical parameter keys for TechnicalIndicator.

The key must reproduce the bracket content of the data-quality gate's
``_technical_indicator_variant_name`` so the gate can name a variant without reading the
jsonb payload. BBANDS is the one type whose stored payload carries computed outputs
(``upper``/``middle``/``lower``) alongside its inputs; those are stripped, because a key
that contains its own output is unique per row and defeats the uniqueness constraint.
"""
from decimal import Decimal

from django.test import SimpleTestCase

from apps.analytics.indicator_params import canonical_param_key
from apps.core.management.commands.validate_data_quality import Command


class CanonicalParamKeyTests(SimpleTestCase):
    def test_empty_and_none_collapse_to_empty_string(self):
        self.assertEqual(canonical_param_key('OBV', {}), '')
        self.assertEqual(canonical_param_key('OBV', None), '')
        self.assertEqual(canonical_param_key('RS_SCORE', {}), '')

    def test_single_key(self):
        self.assertEqual(canonical_param_key('SMA', {'timeperiod': 50}), 'timeperiod=50')
        self.assertEqual(canonical_param_key('MOM_5D', {'n_days': 5}), 'n_days=5')
        self.assertEqual(
            canonical_param_key('REALIZED_VOLATILITY_5D', {'window': 5}), 'window=5'
        )

    def test_multi_key_is_sorted_by_key_name(self):
        self.assertEqual(
            canonical_param_key('MACD', {'slowperiod': 26, 'fastperiod': 12, 'signalperiod': 9}),
            'fastperiod=12,signalperiod=9,slowperiod=26',
        )

    def test_bbands_strips_computed_outputs(self):
        stored = {
            'lower': 4.42155298, 'upper': 5.06461702, 'middle': 4.743085,
            'nbdevdn': 2, 'nbdevup': 2, 'timeperiod': 20,
        }
        inputs_only = {'timeperiod': 20, 'nbdevup': 2, 'nbdevdn': 2}
        self.assertEqual(canonical_param_key('BBANDS', stored), 'nbdevdn=2,nbdevup=2,timeperiod=20')
        self.assertEqual(
            canonical_param_key('BBANDS', stored), canonical_param_key('BBANDS', inputs_only)
        )

    def test_bbands_outputs_are_not_stripped_from_other_types(self):
        self.assertEqual(
            canonical_param_key('SMA', {'timeperiod': 20, 'upper': 1.5}), 'timeperiod=20,upper=1.5'
        )

    def test_float_and_decimal_formatting_matches_python_str(self):
        self.assertEqual(canonical_param_key('SMA', {'timeperiod': 2.0}), 'timeperiod=2.0')
        self.assertEqual(canonical_param_key('SMA', {'timeperiod': Decimal('2.0')}), 'timeperiod=2.0')

    def test_matches_gate_variant_name_for_every_non_bbands_type(self):
        gate = Command()
        cases = [
            ('SMA', {'timeperiod': 50}),
            ('EMA', {'timeperiod': 200}),
            ('RSI', {'timeperiod': 14}),
            ('MACD', {'fastperiod': 12, 'slowperiod': 26, 'signalperiod': 9}),
            ('STOCH', {'fastk_period': 14, 'slowk_period': 3, 'slowd_period': 3}),
            ('ADX', {'timeperiod': 14}),
            ('OBV', {}),
            ('RS_SCORE', {}),
            ('FIB_RET', {'lookback_days': 60}),
            ('MOM_10D', {'n_days': 10}),
            ('RETURN_3D', {'n_days': 3}),
            ('RELATIVE_VOLUME_20D', {'n_days': 20}),
            ('REALIZED_VOLATILITY_5D', {'window': 5}),
        ]
        for indicator_type, parameters in cases:
            with self.subTest(indicator_type=indicator_type):
                expected = gate._technical_indicator_variant_name(indicator_type, parameters)
                bracket = expected[len(indicator_type):].strip('[]')
                self.assertEqual(canonical_param_key(indicator_type, parameters), bracket)

    def test_fib_ret_strips_computed_outputs_including_nested_levels(self):
        stored = {
            'lookback_days': 60, 'high': 12.0, 'low': 8.0, 'range': 4.0,
            'levels': {'0.236': 11.056, '0.382': 10.472, '0.500': 10.0,
                       '0.618': 9.528, '0.786': 8.856},
            'close': 10.5,
        }
        key = canonical_param_key('FIB_RET', stored)
        self.assertEqual(key, 'lookback_days=60')
        self.assertLessEqual(len(key), 120)
        self.assertEqual(key, canonical_param_key('FIB_RET', {'lookback_days': 60}))

    def test_scalar_payload_does_not_crash_and_stays_distinct(self):
        self.assertEqual(canonical_param_key('RSI', '14'), '"14"')
        self.assertEqual(canonical_param_key('RSI', 14), '14')
        self.assertNotEqual(canonical_param_key('RSI', '14'), canonical_param_key('RSI', 15))

    def test_key_fits_the_column_width(self):
        longest = canonical_param_key(
            'STOCH', {'fastk_period': 14, 'slowk_period': 3, 'slowd_period': 3}
        )
        self.assertLessEqual(len(longest), 120)