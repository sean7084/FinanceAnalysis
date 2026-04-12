from datetime import timedelta
from decimal import Decimal
from statistics import mean, pstdev

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from apps.factors.models import FactorScore
from apps.markets.models import OHLCV
from apps.prediction.models import PredictionResult
from .models import BacktestRun, BacktestTrade


DECIMAL_0 = Decimal('0')
DECIMAL_1 = Decimal('1')
DECIMAL_100 = Decimal('100')
TRADING_DAYS = Decimal('252')


def _d(value):
    return Decimal(str(value))


def _clamp(v, low=Decimal('-1'), high=Decimal('10')):
    return max(low, min(high, v))


def _get_trading_dates(start_date, end_date):
    return list(
        OHLCV.objects.filter(date__gte=start_date, date__lte=end_date)
        .values_list('date', flat=True)
        .distinct()
        .order_by('date')
    )


def _build_price_map(start_date, end_date):
    rows = OHLCV.objects.filter(date__gte=start_date, date__lte=end_date).values_list('asset_id', 'date', 'close')
    return {(asset_id, dt): _d(close) for asset_id, dt, close in rows}


def _pick_candidates(run, dt):
    params = run.parameters or {}
    top_n = int(params.get('top_n', 5))

    if run.strategy_type == BacktestRun.StrategyType.BOTTOM_CANDIDATE:
        threshold = _d(params.get('bottom_threshold', '0.60'))
        qs = (
            FactorScore.objects.filter(date=dt, mode=FactorScore.FactorMode.COMPOSITE, bottom_probability_score__gte=threshold)
            .order_by('-bottom_probability_score')
            .values_list('asset_id', flat=True)
        )
        return list(qs[:top_n])

    horizon = int(params.get('horizon_days', 7))
    up_threshold = _d(params.get('up_threshold', '0.55'))
    qs = (
        PredictionResult.objects.filter(date=dt, horizon_days=horizon, up_probability__gte=up_threshold)
        .order_by('-up_probability')
        .values_list('asset_id', flat=True)
    )
    return list(qs[:top_n])


def _calc_max_drawdown(equity_curve):
    if not equity_curve:
        return DECIMAL_0
    peak = equity_curve[0]
    max_dd = DECIMAL_0
    for val in equity_curve:
        if val > peak:
            peak = val
        if peak > 0:
            dd = (peak - val) / peak
            if dd > max_dd:
                max_dd = dd
    return _clamp(max_dd, DECIMAL_0, DECIMAL_1)


def _calc_sharpe(daily_returns):
    if len(daily_returns) < 2:
        return DECIMAL_0
    mu = mean(daily_returns)
    sigma = pstdev(daily_returns)
    if sigma == 0:
        return DECIMAL_0
    sharpe = (Decimal(str(mu)) / Decimal(str(sigma))) * Decimal(str(float(TRADING_DAYS) ** 0.5))
    return _clamp(sharpe, Decimal('-10'), Decimal('10'))


@shared_task
def run_backtest(backtest_run_id):
    run = BacktestRun.objects.filter(id=backtest_run_id).first()
    if not run:
        return f'Backtest run not found: {backtest_run_id}'

    run.status = BacktestRun.Status.RUNNING
    run.error_message = ''
    run.started_at = timezone.now()
    run.completed_at = None
    run.save(update_fields=['status', 'error_message', 'started_at', 'completed_at', 'updated_at'])

    try:
        with transaction.atomic():
            run.trades.all().delete()

        trading_dates = _get_trading_dates(run.start_date, run.end_date)
        if len(trading_dates) < 2:
            raise ValueError('Not enough OHLCV data in selected date range.')

        price_map = _build_price_map(run.start_date, run.end_date)
        fee_rate = _d((run.parameters or {}).get('fee_rate', '0.001'))
        slippage_bps = _d((run.parameters or {}).get('slippage_bps', '5'))

        cash = _d(run.initial_capital)
        equity_curve = [cash]
        closed_pnls = []

        for idx in range(len(trading_dates) - 1):
            buy_date = trading_dates[idx]
            sell_date = trading_dates[idx + 1]

            asset_ids = _pick_candidates(run, buy_date)
            if not asset_ids:
                equity_curve.append(cash)
                continue

            allocation = cash / _d(len(asset_ids))
            day_realized = DECIMAL_0

            for asset_id in asset_ids:
                buy_close = price_map.get((asset_id, buy_date))
                sell_close = price_map.get((asset_id, sell_date))
                if not buy_close or not sell_close or buy_close <= 0:
                    continue

                slippage_buy = buy_close * slippage_bps / DECIMAL_100 / DECIMAL_100
                buy_price = buy_close + slippage_buy
                quantity = allocation / buy_price
                if quantity <= 0:
                    continue

                buy_amount = quantity * buy_price
                buy_fee = buy_amount * fee_rate

                slippage_sell = sell_close * slippage_bps / DECIMAL_100 / DECIMAL_100
                sell_price = sell_close - slippage_sell
                sell_amount = quantity * sell_price
                sell_fee = sell_amount * fee_rate

                pnl = sell_amount - sell_fee - buy_amount - buy_fee
                day_realized += pnl
                closed_pnls.append(pnl)

                BacktestTrade.objects.create(
                    backtest_run=run,
                    asset_id=asset_id,
                    trade_date=buy_date,
                    side=BacktestTrade.Side.BUY,
                    quantity=quantity,
                    price=buy_price,
                    fee=buy_fee,
                    slippage=slippage_buy,
                    amount=buy_amount,
                    pnl=DECIMAL_0,
                    signal_payload={'strategy': run.strategy_type},
                )
                BacktestTrade.objects.create(
                    backtest_run=run,
                    asset_id=asset_id,
                    trade_date=sell_date,
                    side=BacktestTrade.Side.SELL,
                    quantity=quantity,
                    price=sell_price,
                    fee=sell_fee,
                    slippage=slippage_sell,
                    amount=sell_amount,
                    pnl=pnl,
                    signal_payload={'strategy': run.strategy_type},
                )

            cash += day_realized
            equity_curve.append(cash)

        final_value = cash
        total_return = (final_value - _d(run.initial_capital)) / _d(run.initial_capital) if run.initial_capital else DECIMAL_0

        n_days = max(1, (run.end_date - run.start_date).days)
        annualized = ((DECIMAL_1 + total_return) ** (Decimal('365') / _d(n_days))) - DECIMAL_1 if (DECIMAL_1 + total_return) > 0 else Decimal('-1')
        max_dd = _calc_max_drawdown(equity_curve)

        daily_returns = []
        for i in range(1, len(equity_curve)):
            prev = equity_curve[i - 1]
            if prev > 0:
                daily_returns.append(float((equity_curve[i] - prev) / prev))
        sharpe = _calc_sharpe(daily_returns)

        total_trades = len(closed_pnls)
        winning = len([p for p in closed_pnls if p > 0])
        win_rate = (_d(winning) / _d(total_trades)) if total_trades else DECIMAL_0

        run.status = BacktestRun.Status.COMPLETED
        run.cash = final_value
        run.final_value = final_value
        run.total_return = _clamp(total_return)
        run.annualized_return = _clamp(annualized)
        run.max_drawdown = _clamp(max_dd, DECIMAL_0, DECIMAL_1)
        run.sharpe_ratio = sharpe
        run.win_rate = _clamp(win_rate, DECIMAL_0, DECIMAL_1)
        run.total_trades = total_trades
        run.winning_trades = winning
        run.report = {
            'equity_curve': [float(v) for v in equity_curve],
            'num_trading_days': len(trading_dates),
            'strategy': run.strategy_type,
        }
        run.completed_at = timezone.now()
        run.save()
        return f'Backtest completed for run_id={run.id}'

    except Exception as exc:
        run.status = BacktestRun.Status.FAILED
        run.error_message = str(exc)
        run.completed_at = timezone.now()
        run.save(update_fields=['status', 'error_message', 'completed_at', 'updated_at'])
        return f'Backtest failed for run_id={run.id}: {exc}'
