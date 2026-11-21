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


def simulate_topn_returns(predictions, prices, *, top_n=3, up_threshold=0.55, holding=1,
                          slippage_bps=DEFAULT_SLIPPAGE_BPS,
                          commission_rate=Decimal('0.00025'),
                          stamp_duty_rate=Decimal('0.001'),
                          transfer_fee_rate=Decimal('0.00001'),
                          execution_model=EXECUTION_NEXT_DAY_OPEN,
                          ts_code_map=None, suspension_set=None, st_by_date=None):
    """Equal-weight top-N portfolio return series with a holding period (H2 / D10).

    Selection/cost/holding parameters are fixed by the preregistration (owner O4);
    defaults here are provisional. The portfolio REBALANCES every ``holding`` sessions
    (CORE_PROFILES base: h3=5, h7=10, h30=20), applying ONE round-trip cost per holding
    period — NOT daily, which would over-apply cost ~holding-fold. This is a
    return-series simulator (no position-level ledger); the full-engine reconciliation
    on sampled fold windows validates it. The rate-based cost model deliberately omits
    the CNY-5 minimum commission (position-size dependent, engine-level). Semantics:

    - Signal at a rebalance date T: rank assets by up_prob desc, keep >= up_threshold,
      take top_n.
    - next_day_open: enter at T+1 open, exit at T+1+holding open;
      gross = open[exit]/open[entry]-1, amortized to a daily return over the period.
      same_day_close: enter T close, exit T+holding close.
    - Buy limit-lock at the entry open (raw price) skips that asset; suspended assets
      skipped. Equal weight across the assets that actually fill.
    - Costs per holding period (one round trip): 2x slippage + 2x commission + stamp
      duty (sell) + transfer fee. holding=1 reproduces the per-day behavior.

    predictions: DataFrame [date, asset_id, up_prob]. prices: DataFrame
    [date, asset_id, open, close, raw_close]. Returns DataFrame [date, portfolio_return,
    n_selected]; for holding=1 indexed by signal date, else by the holding days.
    """
    import pandas as pd

    ts_code_map = ts_code_map or {}
    suspension_set = suspension_set or set()
    st_by_date = st_by_date or {}
    # Coerce to Decimal: the manifest passes JSON floats, the defaults are Decimal.
    slip = Decimal(str(slippage_bps)) / Decimal('10000')
    commission_rate = Decimal(str(commission_rate))
    stamp_duty_rate = Decimal(str(stamp_duty_rate))
    transfer_fee_rate = Decimal(str(transfer_fee_rate))
    cost_rate = float(2 * slip + 2 * commission_rate + stamp_duty_rate + transfer_fee_rate)

    open_p = prices.pivot(index='date', columns='asset_id', values='open')
    close_p = prices.pivot(index='date', columns='asset_id', values='close')
    raw_p = prices.pivot(index='date', columns='asset_id', values='raw_close')
    dates = sorted(open_p.index)
    date_pos = {d: i for i, d in enumerate(dates)}

    rows = []
    stride = max(1, int(holding))
    by_date = {d: grp for d, grp in predictions.groupby('date')}
    signal_dates = sorted(by_date)
    for r in range(0, len(signal_dates), stride):
        signal_date = signal_dates[r]
        if signal_date not in date_pos:
            continue
        i = date_pos[signal_date]
        entry_i = i + 1 if execution_model == EXECUTION_NEXT_DAY_OPEN else i
        exit_i = entry_i + stride
        if exit_i >= len(dates):
            break
        entry_date, exit_date = dates[entry_i], dates[exit_i]
        group = by_date[signal_date]
        ranked = group[group['up_prob'] >= up_threshold].sort_values('up_prob', ascending=False).head(top_n)
        rets = []
        for _, candidate in ranked.iterrows():
            aid = candidate['asset_id']
            if (aid, entry_date) in suspension_set:
                continue
            entry_open = open_p.at[entry_date, aid] if aid in open_p.columns else None
            exit_open = open_p.at[exit_date, aid] if aid in open_p.columns else None
            if entry_open is None or exit_open is None or pd.isna(entry_open) or pd.isna(exit_open) or entry_open <= 0:
                continue
            if execution_model == EXECUTION_NEXT_DAY_OPEN:
                prev_raw = raw_p.at[signal_date, aid] if (aid in raw_p.columns and signal_date in raw_p.index) else None
                raw_entry = raw_open_from_row(entry_open, close_p.at[entry_date, aid], raw_p.at[entry_date, aid] if aid in raw_p.columns else None)
                is_st = aid in st_by_date.get(entry_date, set())
                if is_limit_locked(raw_open=raw_entry, prev_raw_close=prev_raw,
                                   ts_code=ts_code_map.get(aid, ''), trade_date=entry_date,
                                   side='buy', is_st=is_st):
                    continue
                gross = float(exit_open) / float(entry_open) - 1.0
            else:
                entry_close = close_p.at[signal_date, aid]
                exit_close = close_p.at[exit_date, aid]
                if pd.isna(entry_close) or pd.isna(exit_close) or entry_close <= 0:
                    continue
                gross = float(exit_close) / float(entry_close) - 1.0
            rets.append(gross - cost_rate)  # one round-trip cost per holding period
        period_net = sum(rets) / len(rets) if rets else 0.0
        if stride == 1:
            rows.append({'date': signal_date, 'portfolio_return': period_net, 'n_selected': len(rets)})
        else:
            daily = (1.0 + period_net) ** (1.0 / stride) - 1.0
            for di in range(entry_i, exit_i):
                rows.append({'date': dates[di], 'portfolio_return': daily, 'n_selected': len(rets)})

    return pd.DataFrame(rows, columns=['date', 'portfolio_return', 'n_selected'])