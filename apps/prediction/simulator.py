"""Vectorized panel simulator primitives for the stock walk-forward harness (H2 / D10).

The full simulator (next window) composes these into a daily top-N portfolio over
the frozen panel. These primitives REUSE the engine's single implementations so the
simulator and the full backtest cannot drift: fee breakdown from backtest.tasks,
limit rules from markets.price_limits. The preregistration fixes the exact cost and
selection parameters (owner O4); defaults here mirror CORE_PROFILES base.
"""
from decimal import Decimal, ROUND_HALF_UP

from apps.markets.price_limits import limit_pct_for

DEFAULT_SLIPPAGE_BPS = Decimal('5')
EXECUTION_NEXT_DAY_OPEN = 'next_day_open'
EXECUTION_SAME_DAY_CLOSE = 'same_day_close'


def fill_price(base_price, slippage_bps, side) -> Decimal:
    """Open (next_day) or close (same_day) base ± slippage. Buy adds, sell subtracts."""
    base = Decimal(str(base_price))
    slip = base * Decimal(str(slippage_bps)) / Decimal('100') / Decimal('100')
    return base + slip if side == 'buy' else base - slip


def raw_open_from_row(open_price, close_price, raw_close):
    """Reconstruct the raw (unadjusted) open: open * raw_close / close (factor cancels).

    Returns None when raw_close is absent (legacy rows) so callers fall back to the
    adjusted series rather than inventing a price.
    """
    if raw_close is None or close_price is None or Decimal(str(close_price)) <= 0:
        return None
    return Decimal(str(open_price)) * Decimal(str(raw_close)) / Decimal(str(close_price))


def is_limit_locked(*, raw_open, prev_raw_close, ts_code, trade_date, side,
                    is_st=False, sessions_since_listing=None) -> bool:
    """Whether the raw open sits at the board limit in the fill direction.

    Buy locks at limit-up (raw_open >= limit_up); sell locks at limit-down
    (raw_open <= limit_down). Exchange rounding 0.01 half-up on the raw price.
    No limit (None) or missing prices => not locked. Mirrors A4 engine rules.
    """
    if raw_open is None or prev_raw_close is None or Decimal(str(prev_raw_close)) <= 0:
        return False
    limit = limit_pct_for(ts_code, trade_date, is_st=is_st,
                          sessions_since_listing=sessions_since_listing)
    if limit is None:
        return False
    prev = Decimal(str(prev_raw_close))
    raw = Decimal(str(raw_open))
    if side == 'buy':
        limit_up = (prev * (Decimal('1') + limit)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return raw >= limit_up
    limit_down = (prev * (Decimal('1') - limit)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return raw <= limit_down