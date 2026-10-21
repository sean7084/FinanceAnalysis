"""TuShare sync tasks for the ETF rotation data layer.

Two decisions in here are non-obvious and carry the correctness of everything above
them, so they are documented at the point of impact rather than in a design note:

1. Units. TuShare ``fund_daily`` reports ``vol`` in 手 (100 份) and ``amount`` in 千元.
   Both are converted on ingest to 份 and 元 so ``ETFDaily`` matches ``markets.OHLCV``'s
   convention; a liquidity threshold written in 元 is then correct without a per-call
   scale factor. ``test_unit_conversion_matches_vwap`` pins the conversion down.
2. Sync shape. ``fund_daily(trade_date=...)`` and ``fund_adj(trade_date=...)`` each
   return the whole market in one call, so the primary path is one call pair per
   trading day rather than one per fund. That is both ~2 orders of magnitude faster
   over a 10-year backfill and it picks up delisted funds naturally, because they
   appear on the historical dates they actually traded.
"""

import logging
import time
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import tushare as ts
from celery import shared_task
from django.conf import settings
from django.db.models import Max, Min
from django.utils import timezone

from apps.core.date_floor import get_historical_data_floor
from apps.markets.models import ExchangeTradingCalendar

from .etf_universe import classify_etf, is_excluded_vehicle
from .models import ETFDaily, ETFUniverse


logger = logging.getLogger(__name__)

# TuShare returns fund volume in 手 and turnover in 千元 (see module docstring).
TUSHARE_VOL_HAND_TO_SHARES = Decimal(100)
TUSHARE_AMOUNT_QIAN_YUAN_TO_YUAN = Decimal(1000)

PRICE_QUANT = Decimal('0.0001')
ADJ_FACTOR_QUANT = Decimal('0.000001')


def _request_sleep_seconds():
    return float(getattr(settings, 'ETF_SYNC_REQUEST_SLEEP_SECONDS', 0.15))


def _retry_sleep_seconds():
    return float(getattr(settings, 'ETF_SYNC_RETRY_SLEEP_SECONDS', 65.0))


def _max_retries():
    return int(getattr(settings, 'ETF_SYNC_MAX_RETRIES', 5))


def _tushare_pro():
    token = getattr(settings, 'TUSHARE_TOKEN', None)
    if not token:
        raise ValueError('TUSHARE_TOKEN is not configured.')
    return ts.pro_api(token)


def _call_tushare_with_retry(fn, label):
    """Call ``fn``, retrying only on TuShare's rate-limit error.

    Mirrors ``apps.markets.tasks._call_tushare_with_retry``: any other failure is a
    real one and must surface rather than be retried five times before masking itself.
    """
    attempts = 0
    while True:
        try:
            result = fn()
            sleep = _request_sleep_seconds()
            if sleep > 0:
                time.sleep(sleep)
            return result
        except Exception as exc:
            message = str(exc)
            attempts += 1
            if '频率超限' not in message or attempts >= _max_retries():
                raise
            logger.warning(
                '%s: TuShare rate limit encountered, sleeping %.0fs before retry %s/%s.',
                label, _retry_sleep_seconds(), attempts, _max_retries(),
            )
            time.sleep(_retry_sleep_seconds())


def _safe_decimal(value, default=None):
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    try:
        return Decimal(str(value))
    except Exception:
        return default


def _safe_date(value):
    if value in (None, ''):
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    parsed = pd.to_datetime(str(value), format='%Y%m%d', errors='coerce')
    if pd.isna(parsed):
        parsed = pd.to_datetime(str(value), errors='coerce')
    if pd.isna(parsed):
        return None
    return parsed.date()


def _normalize_text(value):
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except Exception:
        pass
    return str(value).strip()


def _bounded(value, field_name):
    """Normalize a TuShare text field to its column width.

    ``fund_basic`` free text (``benchmark``, ``management``) overflows several
    ``max_length`` bounds on real rows, and Postgres raises instead of truncating --
    which aborted the first full universe sync. Reading the bound off the model keeps
    the two from drifting apart.
    """
    return _normalize_text(value)[: ETFUniverse._meta.get_field(field_name).max_length]


@shared_task(bind=True, name='apps.etf_rotation.tasks.sync_etf_universe',
             soft_time_limit=900, time_limit=1000)
def sync_etf_universe(self):
    """Refresh ``ETFUniverse`` from TuShare ``fund_basic(market='E')``.

    Every returned row is stored, including the ones that are not ETFs and the ones
    that are delisted: ``is_rankable`` records the exclusion so the pool filter is a
    plain SQL predicate, and keeping delisted rows is what lets a backtest see the
    funds that failed.
    """
    pro = _tushare_pro()
    frame = _call_tushare_with_retry(lambda: pro.fund_basic(market='E'), 'fund_basic(market=E)')
    if frame is None or frame.empty:
        raise ValueError('fund_basic(market="E") returned no rows.')

    rows = []
    for _, record in frame.iterrows():
        ts_code = _bounded(record.get('ts_code'), 'ts_code')
        if not ts_code:
            continue
        name = _bounded(record.get('name'), 'name') or ts_code
        fund_type = _bounded(record.get('fund_type'), 'fund_type')
        invest_type = _bounded(record.get('invest_type'), 'invest_type')
        benchmark = _bounded(record.get('benchmark'), 'benchmark')
        status = _bounded(record.get('status'), 'status')
        excluded = is_excluded_vehicle(ts_code, name, fund_type, invest_type)
        category, sub_category = classify_etf(
            name=name, invest_type=invest_type, benchmark=benchmark, fund_type=fund_type,
        )
        list_date = _safe_date(record.get('list_date'))
        rows.append(
            ETFUniverse(
                ts_code=ts_code,
                symbol=_bounded(ts_code.split('.')[0], 'symbol'),
                name=name,
                fund_type=fund_type,
                invest_type=invest_type,
                benchmark=benchmark,
                management=_bounded(record.get('management'), 'management'),
                custodian=_bounded(record.get('custodian'), 'custodian'),
                list_date=list_date,
                delist_date=_safe_date(record.get('delist_date')),
                status=status,
                is_active=status == ETFUniverse.Status.LISTED,
                is_rankable=not excluded,
                category=category,
                sub_category=_bounded(sub_category, 'sub_category'),
                m_fee=_safe_decimal(record.get('m_fee')),
                c_fee=_safe_decimal(record.get('c_fee')),
                metadata={
                    'found_date': _normalize_text(record.get('found_date')),
                    'issue_date': _normalize_text(record.get('issue_date')),
                    'due_date': _normalize_text(record.get('due_date')),
                    'p_issue_unit': _normalize_text(record.get('p_issue_unit')),
                    'type': _normalize_text(record.get('type')),
                    'exchange': _normalize_text(record.get('exchange')),
                    'currency': _normalize_text(record.get('currency')),
                    'excluded_vehicle': excluded,
                },
            )
        )

    update_fields = [
        'symbol', 'name', 'fund_type', 'invest_type', 'benchmark', 'management',
        'custodian', 'list_date', 'delist_date', 'status', 'is_active', 'is_rankable',
        'category', 'sub_category', 'm_fee', 'c_fee', 'metadata', 'updated_at',
    ]
    ETFUniverse.objects.bulk_create(
        rows,
        batch_size=2000,
        update_conflicts=True,
        update_fields=update_fields,
        unique_fields=['ts_code'],
    )

    # ``list_date`` is blank on a slice of the feed; without a substitute those funds
    # are invisible to the age filter for reasons that have nothing to do with age.
    stub_count = refresh_etf_first_trade_dates()
    return {
        'fetched': len(rows),
        'universe_total': ETFUniverse.objects.count(),
        'rankable_total': ETFUniverse.objects.filter(is_rankable=True).count(),
        'first_trade_dates_updated': stub_count,
    }


@shared_task(bind=True, name='apps.etf_rotation.tasks.refresh_etf_first_trade_dates',
             soft_time_limit=900, time_limit=1000)
def refresh_etf_first_trade_dates(self):
    """Backfill ``first_trade_date`` from the bars we actually hold.

    Also repairs ``list_date`` for funds TuShare left blank, using the same evidence.
    """
    bounds = list(
        ETFDaily.objects.values('etf_id').annotate(first=Min('date'), last=Max('date')),
    )
    by_id = {row['etf_id']: row for row in bounds}
    if not by_id:
        return 0

    stubs = list(
        ETFUniverse.objects.filter(
            list_date__isnull=True,
            first_trade_date__isnull=True,
        ).values_list('id', flat=True),
    )
    # Any row whose stored first-trade-date is not the real minimum needs a refresh too,
    # which is the case on the first backfill after the universe was synced.
    candidates = list(
        ETFUniverse.objects.filter(first_trade_date__isnull=True).values_list('id', flat=True),
    )
    ids = sorted({*stubs, *candidates})
    to_update = []
    for etf_id in ids:
        bound = by_id.get(etf_id)
        if not bound:
            continue
        try:
            universe = ETFUniverse.objects.get(id=etf_id)
        except ETFUniverse.DoesNotExist:
            continue
        universe.first_trade_date = bound['first']
        if universe.list_date is None:
            universe.list_date = bound['first']
        to_update.append(universe)

    if not to_update:
        return 0
    ETFUniverse.objects.bulk_update(
        to_update, ['first_trade_date', 'list_date', 'updated_at'], batch_size=2000,
    )
    return len(to_update)


def _ensure_universe_rows(ts_codes, trade_day):
    """Create stub rows for codes ``fund_basic`` never returned.

    Returning the codes that had to be invented lets the caller log that the universe
    is behind, rather than silently dropping bars.
    """
    existing = dict(ETFUniverse.objects.filter(ts_code__in=ts_codes).values_list('ts_code', 'id'))
    missing = [code for code in ts_codes if code not in existing]
    if missing:
        stubs = [
            ETFUniverse(
                ts_code=code,
                symbol=code.split('.')[0],
                name=code,
                category='other',
                # Not curated by fund_basic, so not rankable until a universe sync can
                # classify it; keeping it unrankable is what stops an unknown vehicle
                # from entering a momentum pool on the strength of a stub row.
                is_rankable=False,
                is_active=False,
                first_trade_date=trade_day,
                metadata={'auto_created_from_trade_date': trade_day.isoformat()},
            )
            for code in missing
        ]
        ETFUniverse.objects.bulk_create(stubs, batch_size=2000, ignore_conflicts=True)
        existing = dict(ETFUniverse.objects.filter(ts_code__in=ts_codes).values_list('ts_code', 'id'))
    return existing, missing


def _carry_forward_adj_factors(etf_ids, trade_day):
    """Last known adjustment factor per fund, for dates ``fund_adj`` does not cover.

    Read from the single previous bar rather than a per-fund scan: a 10-year backfill
    hits this path on every early date, and ``fund_adj`` only changes on distributions,
    so the immediately preceding factor is both cheap and the right substitute.
    """
    if not etf_ids:
        return {}
    previous_day = (
        ETFDaily.objects.filter(date__lt=trade_day)
        .order_by('-date')
        .values_list('date', flat=True)
        .first()
    )
    if previous_day is None:
        return {}
    return {
        row['etf_id']: row['adj_factor']
        for row in ETFDaily.objects.filter(etf_id__in=etf_ids, date=previous_day).values('etf_id', 'adj_factor')
    }


@shared_task(bind=True, name='apps.etf_rotation.tasks.sync_etf_daily_by_date',
             soft_time_limit=300, time_limit=360)
def sync_etf_daily_by_date(self, trade_date):
    """Pull the whole market's ETF bars for one trading date."""
    trade_day = trade_date if isinstance(trade_date, date) else date.fromisoformat(str(trade_date))
    if trade_day < get_historical_data_floor():
        raise ValueError(
            f'trade_date {trade_day.isoformat()} is before HISTORICAL_DATA_FLOOR '
            f'{get_historical_data_floor().isoformat()}.'
        )

    pro = _tushare_pro()
    token = trade_day.strftime('%Y%m%d')
    daily_frame = _call_tushare_with_retry(
        lambda: pro.fund_daily(trade_date=token), f'fund_daily(trade_date={token})',
    )
    if daily_frame is None or daily_frame.empty:
        return {'trade_date': token, 'rows_written': 0, 'created_universe': 0, 'skipped': 'empty'}

    adj_frame = _call_tushare_with_retry(
        lambda: pro.fund_adj(trade_date=token), f'fund_adj(trade_date={token})',
    )
    adj_map = {}
    if adj_frame is not None and not adj_frame.empty:
        for _, record in adj_frame.iterrows():
            code = _normalize_text(record.get('ts_code'))
            factor = _safe_decimal(record.get('adj_factor'))
            if code and factor is not None:
                adj_map[code] = factor

    codes = [_normalize_text(value) for value in daily_frame['ts_code'].tolist()]
    codes = [code for code in codes if code]
    etf_ids, created_codes = _ensure_universe_rows(sorted(set(codes)), trade_day)

    carried = _carry_forward_adj_factors(
        [etf_ids[code] for code in set(codes) if code in etf_ids and code not in adj_map],
        trade_day,
    )

    records = []
    for _, record in daily_frame.iterrows():
        code = _normalize_text(record.get('ts_code'))
        etf_id = etf_ids.get(code)
        if not code or etf_id is None:
            continue
        close = _safe_decimal(record.get('close'))
        open_price = _safe_decimal(record.get('open'), close)
        high = _safe_decimal(record.get('high'), close)
        low = _safe_decimal(record.get('low'), close)
        if close is None:
            continue

        vol_shares = _safe_decimal(record.get('vol'))
        volume = int((vol_shares * TUSHARE_VOL_HAND_TO_SHARES).to_integral_value()) if vol_shares is not None else 0
        amount_yuan = _safe_decimal(record.get('amount'))
        amount = (amount_yuan * TUSHARE_AMOUNT_QIAN_YUAN_TO_YUAN) if amount_yuan is not None else Decimal(0)
        factor = adj_map.get(code)
        if factor is None:
            factor = carried.get(etf_id, Decimal(1))

        records.append(
            ETFDaily(
                etf_id=etf_id,
                date=trade_day,
                open=open_price.quantize(PRICE_QUANT),
                high=high.quantize(PRICE_QUANT),
                low=low.quantize(PRICE_QUANT),
                close=close.quantize(PRICE_QUANT),
                adj_close=(close * factor).quantize(PRICE_QUANT),
                volume=volume,
                amount=amount.quantize(Decimal('0.0001')),
                adj_factor=factor.quantize(ADJ_FACTOR_QUANT),
            )
        )

    if not records:
        return {'trade_date': token, 'rows_written': 0, 'created_universe': len(created_codes)}

    ETFDaily.objects.bulk_create(
        records,
        batch_size=2000,
        update_conflicts=True,
        update_fields=['open', 'high', 'low', 'close', 'adj_close', 'volume', 'amount', 'adj_factor'],
        unique_fields=['etf', 'date'],
    )
    return {
        'trade_date': token,
        'rows_written': len(records),
        'created_universe': len(created_codes),
    }


def missing_etf_daily_dates(start_date, end_date, exchange_code='SSE'):
    """Open trading dates in ``[start_date, end_date]`` that have no ``ETFDaily`` rows."""
    open_dates = list(
        ExchangeTradingCalendar.objects.filter(
            exchange_code=exchange_code,
            is_open=True,
            trade_date__gte=start_date,
            trade_date__lte=end_date,
        ).values_list('trade_date', flat=True).order_by('trade_date'),
    )
    if not open_dates:
        return []
    covered = set(
        ETFDaily.objects.filter(
            date__gte=open_dates[0],
            date__lte=open_dates[-1],
        ).values_list('date', flat=True),
    )
    return [day for day in open_dates if day not in covered]


@shared_task(bind=True, name='apps.etf_rotation.tasks.sync_etf_daily_recent',
             soft_time_limit=600, time_limit=660)
def sync_etf_daily_recent(self, lookback_days=10):
    """Daily beat entry point: fill any gap in the trailing window, then today."""
    end_day = timezone.localdate()
    start_day = end_day - timedelta(days=max(int(lookback_days), 1))
    dates = missing_etf_daily_dates(start_day, end_day)
    results = []
    for day in dates:
        results.append(sync_etf_daily_by_date(day))
    return {
        'requested_range': [start_day.isoformat(), end_day.isoformat()],
        'dates_synced': len(results),
        'rows_written': sum(int(item.get('rows_written') or 0) for item in results),
    }


@shared_task(bind=True, name='apps.etf_rotation.tasks.compute_etf_features_daily',
             soft_time_limit=1800, time_limit=2000)
def compute_etf_features_daily(self, lookback_days=320):
    """Beat entry point: refresh the feature window every trading day.

    The lookback is long on purpose. ``mom_250d`` needs a year of bars before it
    produces a value, so a trailing-week window would silently leave the newest
    snapshots' longest-horizon features NULL and the ranker would score against a
    shrinking pool.
    """
    from django.core.management import call_command

    end_day = timezone.localdate()
    start_day = end_day - timedelta(days=max(int(lookback_days), 1))
    call_command(
        'compute_etf_features',
        start=start_day.isoformat(),
        end=end_day.isoformat(),
        verbosity=0,
    )
    return {
        'start': start_day.isoformat(),
        'end': end_day.isoformat(),
        'snapshots': ETFFeatureSnapshot.objects.filter(
            date__gte=start_day, date__lte=end_day,
        ).count(),
    }


@shared_task(bind=True, name='apps.etf_rotation.tasks.generate_etf_rotation_signal_daily',
             soft_time_limit=900, time_limit=1000)
def generate_etf_rotation_signal_daily(self, signal_type='MONTHLY', model_type='RULE'):
    """Beat entry point: write today's rotation signal."""
    from django.core.management import call_command

    return call_command(
        'generate_etf_rotation_signal',
        signal_type=signal_type,
        model_type=model_type,
        verbosity=0,
    )


@shared_task(bind=True, name='apps.etf_rotation.tasks.run_etf_rotation_backtest',
             soft_time_limit=3600, time_limit=3900)
def run_etf_rotation_backtest(self, backtest_id):
    """Execute a queued ``ETFRotationBacktest`` and write its result set in place.

    The row's own columns are the configuration: they are read out and fed back in as
    explicit overrides, so a run scheduled through the API cannot silently inherit a
    different cost model than the one the caller persisted.
    """
    from apps.etf_rotation.backtest_engine import run_rotation_backtest
    from apps.etf_rotation.models import ETFRotationBacktest

    run = ETFRotationBacktest.objects.get(pk=backtest_id)
    return run_rotation_backtest(
        start_date=run.start_date,
        end_date=run.end_date,
        model_type=run.model_type,
        rebalance_frequency=run.rebalance_frequency,
        max_positions=run.max_positions,
        momentum_lookback=run.momentum_lookback,
        absolute_momentum_filter=run.absolute_momentum_filter,
        defensive_asset=run.defensive_asset,
        initial_capital=float(run.initial_capital),
        commission_rate=run.commission_rate,
        min_commission=float(run.min_commission),
        slippage=run.slippage,
        liquidity_floor_multiple=run.liquidity_floor_multiple,
        benchmark_code=run.benchmark_code,
        config_overrides={'walk_forward_steps': run.walk_forward_steps},
        persist_to=run,
    )
