from datetime import date
from types import SimpleNamespace

from django.test import TestCase

from apps.analytics.technical_staleness import (
    clear_ordered_trading_dates_cache,
    ordered_trading_dates_for_asset,
    ordered_trading_dates_for_exchange,
)
from apps.markets.models import ExchangeTradingCalendar


class OrderedTradingDatesCacheTests(TestCase):
    def setUp(self):
        clear_ordered_trading_dates_cache()
        ExchangeTradingCalendar.objects.create(
            exchange_code='SSE', trade_date=date(2025, 1, 2), is_open=True,
        )
        ExchangeTradingCalendar.objects.create(
            exchange_code='SSE', trade_date=date(2025, 1, 3), is_open=True,
        )
        ExchangeTradingCalendar.objects.create(
            exchange_code='SZSE', trade_date=date(2025, 1, 2), is_open=True,
        )

    def tearDown(self):
        clear_ordered_trading_dates_cache()

    def test_repeated_exchange_lookup_queries_once(self):
        with self.assertNumQueries(1):
            first = ordered_trading_dates_for_exchange('SSE', date(2025, 1, 3))
            second = ordered_trading_dates_for_exchange('SSE', date(2025, 1, 3))
        self.assertEqual(first, second)
        self.assertEqual(first, (date(2025, 1, 2), date(2025, 1, 3)))

    def test_calendar_write_invalidates_cached_dates(self):
        self.assertEqual(
            ordered_trading_dates_for_exchange('SSE', date(2025, 1, 3)),
            (date(2025, 1, 2), date(2025, 1, 3)),
        )
        ExchangeTradingCalendar.objects.create(
            exchange_code='SSE', trade_date=date(2025, 1, 1), is_open=True,
        )
        with self.assertNumQueries(1):
            refreshed = ordered_trading_dates_for_exchange('SSE', date(2025, 1, 3))
        self.assertEqual(refreshed[0], date(2025, 1, 1))

    def test_end_date_and_exchange_are_part_of_the_cache_key(self):
        with self.assertNumQueries(3):
            ordered_trading_dates_for_exchange('SSE', date(2025, 1, 2))
            ordered_trading_dates_for_exchange('SSE', date(2025, 1, 3))
            ordered_trading_dates_for_exchange('SZSE', date(2025, 1, 3))
        self.assertEqual(
            ordered_trading_dates_for_exchange('SSE', date(2025, 1, 2)),
            (date(2025, 1, 2),),
        )

    def test_asset_lookup_shares_the_exchange_cache(self):
        asset = SimpleNamespace(ts_code='600000.SH')
        with self.assertNumQueries(1):
            from_asset = ordered_trading_dates_for_asset(asset, date(2025, 1, 3))
            from_exchange = ordered_trading_dates_for_exchange('SSE', date(2025, 1, 3))
        self.assertEqual(from_asset, from_exchange)
