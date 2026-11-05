"""Adjustment-basis audit tests. Pure functions over dicts, so no database is touched.

The behaviour worth pinning is the one that took a failed first attempt to learn:
drift is 1-3%, price limits are 5-30%, and the provider rounds prices to two
decimals. Anything that confuses those three makes this module useless.
"""

from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from apps.markets.adjustment_basis import (
    MIN_DEVIATION,
    PRICE_TICK_HALF,
    audit_asset_basis,
    deviation_of,
)

D1 = date(2024, 1, 2)
D2 = date(2024, 1, 3)
D3 = date(2024, 1, 4)
D4 = date(2024, 1, 5)


def series(closes):
    return dict(zip([D1, D2, D3, D4], [Decimal(value) for value in closes]))


class ConsistentSeriesTests(SimpleTestCase):
    def test_rows_written_under_the_reference_basis_show_no_drift(self):
        raw = series(['10.00', '10.50', '11.00', '10.80'])
        factors = {D1: Decimal('1.0'), D2: Decimal('1.0'),
                   D3: Decimal('1.1'), D4: Decimal('1.1')}
        # stored = raw * factor / newest_factor, which is what a single sync produces.
        stored = {day: raw[day] * factors[day] / Decimal('1.1') for day in raw}
        finding = audit_asset_basis('600000.SH', stored, raw, factors)
        self.assertEqual(finding.off_basis_rows, 0)
        self.assertFalse(finding.drifted)
        self.assertEqual(finding.rows_compared, 4)

    def test_reference_is_the_newest_stored_date_not_the_newest_known_factor(self):
        # The provider moved after the last sync. Every row would read as off-basis if
        # the reference came from the provider's newest date -- that mistake is what an
        # earlier version of this measurement made.
        raw = {D1: Decimal('10.00'), D2: Decimal('10.00')}
        stored = {D1: Decimal('10.00'), D2: Decimal('10.00')}
        factors = {D1: Decimal('1.0'), D2: Decimal('1.0'), D3: Decimal('1.05')}
        finding = audit_asset_basis('600000.SH', stored, raw, factors)
        self.assertEqual(finding.reference_factor, Decimal('1.0'))
        self.assertEqual(finding.off_basis_rows, 0)


class DriftedSeriesTests(SimpleTestCase):
    def test_a_rebasing_prefix_is_reported_as_drift(self):
        raw = series(['10.00', '10.50', '11.00', '10.80'])
        factors = {D1: Decimal('1.0'), D2: Decimal('1.0'),
                   D3: Decimal('1.03'), D4: Decimal('1.03')}
        # First two rows written when 1.0 was the newest factor; last two under 1.03.
        stored = {
            D1: Decimal('10.00'), D2: Decimal('10.50'),
            D3: raw[D3] * Decimal('1.03') / Decimal('1.03'),
            D4: raw[D4] * Decimal('1.03') / Decimal('1.03'),
        }
        finding = audit_asset_basis('600000.SH', stored, raw, factors)
        self.assertEqual(finding.off_basis_rows, 2)
        self.assertTrue(finding.drifted)
        self.assertEqual(finding.first_off_basis_date, D1)
        self.assertEqual(finding.last_off_basis_date, D2)
        self.assertTrue(finding.contiguous_prefix)
        self.assertAlmostEqual(float(finding.max_deviation), 0.03, places=4)

    def test_a_step_smaller_than_the_narrowest_price_limit_is_still_drift(self):
        # 1.5% is a fifth of the narrowest daily limit. A limit-breach test cannot see
        # this at all, which is the entire reason this module exists.
        raw = {D1: Decimal('100.00'), D2: Decimal('100.00')}
        factors = {D1: Decimal('1.0'), D2: Decimal('1.0')}
        stored = {D1: Decimal('101.50'), D2: Decimal('100.00')}
        finding = audit_asset_basis('600519.SH', stored, raw, factors)
        self.assertEqual(finding.off_basis_rows, 1)

    def test_exdividend_dates_inside_the_span_are_counted(self):
        raw = {day: Decimal('10.00') for day in [D1, D2, D3, D4]}
        factors = {D1: Decimal('1.0'), D2: Decimal('1.02'),
                   D3: Decimal('1.02'), D4: Decimal('1.05')}
        stored = {day: raw[day] * factors[day] / Decimal('1.0') for day in raw}
        finding = audit_asset_basis('600000.SH', stored, raw, factors)
        self.assertTrue(finding.drifted)
        self.assertGreaterEqual(finding.exdiv_dates_in_span, 1)

    def test_verdict_and_row_shape(self):
        raw = {D1: Decimal('10.00')}
        factors = {D1: Decimal('1.0')}
        finding = audit_asset_basis('600000.SH', {D1: Decimal('11.00')}, raw, factors)
        row = finding.as_row()
        self.assertEqual(row['verdict'], 'drifted')
        self.assertEqual(row['ts_code'], '600000.SH')
        self.assertIn('max_deviation_pct', row)
        self.assertEqual(finding.off_basis_share, Decimal('1.0000'))


class NoiseBandTests(SimpleTestCase):
    def test_two_decimal_rounding_on_a_cheap_stock_is_not_drift(self):
        # stored 2.005 against an expected 2.000 is +0.25%, entirely explained by the
        # provider's 0.01 price step. Flagging this is how the first attempt produced
        # hundreds of fake "bases" per asset.
        raw = {D1: Decimal('2.00')}
        factors = {D1: Decimal('1.0')}
        stored = {D1: Decimal('2.005')}
        finding = audit_asset_basis('600000.SH', stored, raw, factors)
        self.assertEqual(finding.off_basis_rows, 0)

    def test_a_real_step_on_a_cheap_stock_is_still_drift(self):
        raw = {D1: Decimal('2.00')}
        factors = {D1: Decimal('1.0')}
        stored = {D1: Decimal('2.04')}
        finding = audit_asset_basis('600000.SH', stored, raw, factors)
        self.assertEqual(finding.off_basis_rows, 1)

    def test_the_floor_and_the_noise_band_are_both_bounded(self):
        self.assertGreater(PRICE_TICK_HALF, Decimal(0))
        self.assertLess(MIN_DEVIATION, Decimal('0.01'))


class IncompleteDataTests(SimpleTestCase):
    def test_dates_missing_from_the_provider_are_skipped_not_flagged(self):
        raw = {D1: Decimal('10.00')}
        factors = {D1: Decimal('1.0'), D2: Decimal('1.0')}
        stored = {D1: Decimal('10.00'), D2: Decimal('10.00')}
        finding = audit_asset_basis('600000.SH', stored, raw, factors)
        # D2 has a stored row and a factor but no raw price: it shrinks the sample
        # rather than becoming evidence of anything.
        self.assertEqual(finding.rows_compared, 1)
        self.assertEqual(finding.rows_skipped, 1)
        self.assertEqual(finding.off_basis_rows, 0)

    def test_no_overlap_with_the_provider_gives_an_empty_finding(self):
        finding = audit_asset_basis('600000.SH', {D1: Decimal('10')}, {}, {D2: Decimal('1')})
        self.assertEqual(finding.rows_compared, 0)
        self.assertIsNone(finding.reference_factor)
        self.assertFalse(finding.drifted)
        self.assertEqual(finding.off_basis_share, Decimal(0))

    def test_empty_stored_series_is_not_an_error(self):
        finding = audit_asset_basis('600000.SH', {}, {}, {})
        self.assertEqual(finding.rows_compared, 0)
        self.assertEqual(finding.rows_skipped, 0)


class DeviationOfTests(SimpleTestCase):
    def test_exact_match_is_one(self):
        self.assertEqual(
            deviation_of(Decimal('10.00'), Decimal('10.00'), Decimal('1.0'), Decimal('1.0')),
            Decimal('1'),
        )

    def test_basis_half_the_reference_halves_the_deviation(self):
        self.assertEqual(
            deviation_of(Decimal('5.00'), Decimal('10.00'), Decimal('1.0'), Decimal('2.0')),
            Decimal('1'),
        )

    def test_unusable_inputs_return_none(self):
        cases = [
            (None, Decimal('10'), Decimal('1'), Decimal('1')),
            (Decimal('10'), None, Decimal('1'), Decimal('1')),
            (Decimal('10'), Decimal('10'), None, Decimal('1')),
            (Decimal('10'), Decimal('10'), Decimal('10'), None),
            (Decimal('0'), Decimal('10'), Decimal('1'), Decimal('1')),
            (Decimal('-1'), Decimal('10'), Decimal('1'), Decimal('1')),
            (Decimal('10'), Decimal('-1'), Decimal('1'), Decimal('1')),
            (Decimal('10'), Decimal('10'), Decimal('0'), Decimal('1')),
            (Decimal('10'), Decimal('10'), Decimal('1'), Decimal('0')),
        ]
        for stored, raw, factor, reference in cases:
            with self.subTest(stored=stored, raw=raw, factor=factor, reference=reference):
                self.assertIsNone(deviation_of(stored, raw, factor, reference))
