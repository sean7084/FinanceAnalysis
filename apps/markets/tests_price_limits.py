"""Price-limit rule tests. Pure functions, so nothing here touches the database."""

from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from apps.markets.price_limits import (
    BOARD_BSE,
    BOARD_CHINEXT,
    BOARD_MAIN,
    BOARD_STAR,
    LIMIT_ST,
    LIMIT_TEN,
    LIMIT_THIRTY,
    LIMIT_TWENTY,
    NEW_LISTING_FREE_SESSIONS,
    board_for,
    is_st_name,
    limit_pct_for,
)


class BoardInferenceTests(SimpleTestCase):
    def test_recognised_boards(self):
        cases = {
            '600519.SH': BOARD_MAIN,
            '601398.SH': BOARD_MAIN,
            '603288.SH': BOARD_MAIN,
            '605499.SH': BOARD_MAIN,
            '000001.SZ': BOARD_MAIN,
            '001979.SZ': BOARD_MAIN,
            '002415.SZ': BOARD_MAIN,
            '003816.SZ': BOARD_MAIN,
            '300750.SZ': BOARD_CHINEXT,
            '301158.SZ': BOARD_CHINEXT,
            '302132.SZ': BOARD_CHINEXT,
            '688981.SH': BOARD_STAR,
            '689009.SH': BOARD_STAR,
            '830799.BJ': BOARD_BSE,
            '920002.BJ': BOARD_BSE,
            '430047.BJ': BOARD_BSE,
        }
        for ts_code, expected in cases.items():
            with self.subTest(ts_code=ts_code):
                self.assertEqual(board_for(ts_code), expected)

    def test_b_shares_are_not_main_board_confusion(self):
        # 900/200 are B shares. They are treated as main board here; that is a
        # deliberate simplification, not an oversight -- the limit is still ±10%.
        self.assertEqual(board_for('900901.SH'), BOARD_MAIN)
        self.assertEqual(board_for('200011.SZ'), BOARD_MAIN)

    def test_suffixless_and_malformed_codes_return_none(self):
        for ts_code in ['600519', '', None, 'ABC.SH', '.SH', '600519.XX']:
            with self.subTest(ts_code=ts_code):
                self.assertIsNone(board_for(ts_code))

    def test_lowercase_and_padded_codes_are_accepted(self):
        self.assertEqual(board_for(' 600519.sh '), BOARD_MAIN)

    def test_beijing_prefixes_are_matched_before_main_board(self):
        # '8' and '4' also open unassigned ranges; the exchange suffix decides.
        self.assertEqual(board_for('830799.BJ'), BOARD_BSE)


class SpecialTreatmentNameTests(SimpleTestCase):
    def test_st_prefixes(self):
        for name in ['ST长油', '*ST海润', 'SST前锋', 'S*ST二重', 'st海润', ' *ST海润 ']:
            with self.subTest(name=name):
                self.assertTrue(is_st_name(name))

    def test_names_containing_st_but_not_treated(self):
        for name in ['华东重机', '健康元', 'TCL科技', '', None, 'STAR', '东北制药']:
            with self.subTest(name=name):
                self.assertFalse(is_st_name(name))


class LimitByBoardTests(SimpleTestCase):
    def test_main_board_is_ten_percent(self):
        self.assertEqual(limit_pct_for('600519.SH', date(2026, 6, 1)), LIMIT_TEN)
        self.assertEqual(limit_pct_for('000001.SZ', date(2015, 1, 5)), LIMIT_TEN)

    def test_main_board_special_treatment_is_five_percent(self):
        self.assertEqual(
            limit_pct_for('600625.SH', date(2026, 6, 1), name='*ST加信'),
            LIMIT_ST,
        )

    def test_chinext_widens_on_the_reform_date(self):
        self.assertEqual(limit_pct_for('300750.SZ', date(2020, 8, 21)), LIMIT_TEN)
        self.assertEqual(limit_pct_for('300750.SZ', date(2020, 8, 24)), LIMIT_TWENTY)
        self.assertEqual(limit_pct_for('300750.SZ', date(2020, 8, 25)), LIMIT_TWENTY)

    def test_chinext_special_treatment_keeps_the_board_limit(self):
        # Post-reform ST on ChiNext is still ±20%, not the main board's ±5%.
        self.assertEqual(
            limit_pct_for('300750.SZ', date(2023, 1, 4), name='ST某创'),
            LIMIT_TWENTY,
        )
        self.assertEqual(
            limit_pct_for('300750.SZ', date(2019, 1, 4), name='ST某创'),
            LIMIT_TEN,
        )

    def test_star_is_twenty_percent_from_board_opening(self):
        self.assertEqual(limit_pct_for('688981.SH', date(2019, 7, 22)), LIMIT_TWENTY)
        self.assertEqual(limit_pct_for('688981.SH', date(2026, 1, 5)), LIMIT_TWENTY)
        self.assertEqual(
            limit_pct_for('688981.SH', date(2026, 1, 5), name='ST某科'),
            LIMIT_TWENTY,
        )

    def test_star_before_the_board_existed_has_no_rule(self):
        self.assertIsNone(limit_pct_for('688981.SH', date(2018, 1, 5)))

    def test_bse_is_thirty_percent_from_its_opening(self):
        self.assertEqual(limit_pct_for('830799.BJ', date(2021, 11, 15)), LIMIT_THIRTY)
        self.assertEqual(limit_pct_for('920002.BJ', date(2026, 1, 5)), LIMIT_THIRTY)
        self.assertIsNone(limit_pct_for('830799.BJ', date(2020, 1, 5)))

    def test_unknown_board_has_no_rule(self):
        self.assertIsNone(limit_pct_for('123456.SZ', date(2026, 1, 5)))

    def test_missing_trade_date_falls_back_to_the_current_rule(self):
        # An audit that does not know the date should not silently widen a
        # main-board bar; ChiNext is the only board where this differs.
        self.assertEqual(limit_pct_for('600519.SH'), LIMIT_TEN)
        self.assertEqual(limit_pct_for('300750.SZ'), LIMIT_TWENTY)


class NewListingExemptionTests(SimpleTestCase):
    def test_first_sessions_are_unlimited(self):
        for session in range(NEW_LISTING_FREE_SESSIONS):
            with self.subTest(session=session):
                self.assertIsNone(
                    limit_pct_for('600519.SH', date(2026, 6, 1), sessions_since_listing=session)
                )

    def test_limit_resumes_after_the_exempt_window(self):
        self.assertEqual(
            limit_pct_for('600519.SH', date(2026, 6, 1),
                          sessions_since_listing=NEW_LISTING_FREE_SESSIONS),
            LIMIT_TEN,
        )

    def test_none_means_unknown_not_exempt(self):
        self.assertEqual(
            limit_pct_for('600519.SH', date(2026, 6, 1), sessions_since_listing=None),
            LIMIT_TEN,
        )

    def test_exempt_window_is_not_a_fraction(self):
        self.assertIsInstance(NEW_LISTING_FREE_SESSIONS, int)


class ResultTypeTests(SimpleTestCase):
    def test_limits_are_decimal_not_float(self):
        # These values get multiplied against stored Decimal prices; a float here
        # would turn an exact comparison into a tolerance hunt.
        for ts_code, when in [('600519.SH', date(2026, 1, 5)), ('300750.SZ', date(2026, 1, 5))]:
            with self.subTest(ts_code=ts_code):
                self.assertIsInstance(limit_pct_for(ts_code, when), Decimal)
