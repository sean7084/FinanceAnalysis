"""Trading-calendar range helpers shared across apps.

These live here rather than in ``apps.prediction`` because they query
``ExchangeTradingCalendar``, which is a markets model, and because freshness
reasoning everywhere in the system is measured in official trading days -- never
in calendar days and never by counting sparse OHLCV rows.
"""

from datetime import date

from apps.markets.models import ExchangeTradingCalendar

DEFAULT_EXCHANGE_CODE = 'SSE'


def open_trading_dates(as_of, lookback_trading_days, exchange_code=DEFAULT_EXCHANGE_CODE):
    """The last ``lookback_trading_days`` open dates up to and including ``as_of``.

    Oldest first, so a repair pass heals the backlog in date order.
    """
    count = max(int(lookback_trading_days or 0), 0)
    if count <= 0:
        return []
    resolved_as_of = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of))
    dates = (
        ExchangeTradingCalendar.objects.filter(
            exchange_code=exchange_code,
            is_open=True,
            trade_date__lte=resolved_as_of,
        )
        .order_by('-trade_date')
        .values_list('trade_date', flat=True)[:count]
    )
    return list(reversed(dates))
