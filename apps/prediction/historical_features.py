from decimal import Decimal

from django.db import models
from django.utils import timezone

from apps.analytics.technical_staleness import (
    latest_official_trade_date,
    ordered_trading_dates_for_asset,
    stored_indicator_is_fresh,
    trading_date_positions,
)
from apps.analytics.models import TechnicalIndicator
from apps.markets.models import Asset, OHLCV


def _cache_key(*parts):
    return ('historical_features', *parts)


def _parameter_cache_items(parameters):
    return tuple(sorted((str(key), str(value)) for key, value in (parameters or {}).items()))


def _to_decimal(value, default):
    if value is None:
        if default is None:
            return None
        return Decimal(str(default))
    try:
        return Decimal(str(value))
    except Exception:
        if default is None:
            return None
        return Decimal(str(default))


def _asset_trading_context(asset_id, as_of, cache=None):
    cache_entry = _cache_key('asset_trading_context', int(asset_id), str(as_of))
    if cache is not None and cache_entry in cache:
        return cache[cache_entry]

    asset = Asset.objects.select_related('market').get(id=asset_id)
    ordered_trading_dates = ordered_trading_dates_for_asset(asset, as_of)
    position_map = trading_date_positions(ordered_trading_dates)
    current_trade_date = latest_official_trade_date(ordered_trading_dates, as_of)
    resolved = (asset, current_trade_date, position_map)
    if cache is not None:
        cache[cache_entry] = resolved
    return resolved


def _indicator_parameter_score(actual_parameters, expected_parameters, required_keys):
    resolved_actual = dict(actual_parameters or {})
    resolved_expected = dict(expected_parameters or {})
    if not resolved_expected:
        return 1
    if not resolved_actual:
        return 1

    score = 1
    for key in required_keys:
        if key not in resolved_actual:
            return -1
        if resolved_actual.get(key) != resolved_expected.get(key):
            return -1
        score += 1

    for key, value in resolved_expected.items():
        if key in required_keys or key not in resolved_actual:
            continue
        if resolved_actual.get(key) != value:
            return -1
        score += 1
    return score


def _parameter_compatibility_filter(parameter_key, parameter_value):
    if not parameter_key:
        return models.Q()
    return (
        models.Q(**{f'parameters__{parameter_key}': parameter_value})
        | models.Q(parameters={})
        | models.Q(parameters__isnull=True)
    )


#: Counted so a priming pass that quietly stops covering anything is visible rather than
#: assumed: a fallback that fires on every asset is the old cost with extra machinery.
#: `installed` counts candidate lists the priming wrote, `cache_hit` counts reads served from
#: one, and `fallback` counts reads that had to query -- the middle number is what separates a
#: prefetch that works from one that installs entries nobody reads.
INDICATOR_PREFETCH_STATS = {'installed': 0, 'cache_hit': 0, 'fallback': 0}


def _candidate_take(limit):
    """How many raw candidate rows a read needs before the Python scoring pass.

    The multiplier is the existing behaviour, kept in one place so the per-day priming and
    the single-asset fallback cannot ask for different depths.
    """
    return max(int(limit or 1) * 4, 12)


def _indicator_candidate_queryset(asset_filter, as_of, indicator_type, parameter_key, parameter_value, take):
    """The one spelling of "the most recent `take` candidate rows for this asset".

    Both paths call it -- the single-asset read with `Q(asset_id=...)` and the per-day
    priming with `Q(asset=OuterRef('pk'))` -- so the primed path cannot drift from the
    filter it is replacing. That matters because the JSONField compatibility test below is
    permissive in a way that is easy to get wrong by hand: it accepts rows whose
    `parameters` are empty as well as rows whose key matches, and the scoring pass then
    picks among them. Hand-writing that predicate in SQL was measured to be wider than this
    filter while returning the same row COUNT, which is why the prefetch reuses the
    queryset object instead of restating it.
    """
    queryset = TechnicalIndicator.objects.filter(
        asset_filter,
        indicator_type=indicator_type,
        timestamp__date__lte=as_of,
    )
    if parameter_key:
        queryset = queryset.filter(_parameter_compatibility_filter(parameter_key, parameter_value))
    return queryset.order_by('-timestamp')[:take]


def _candidate_cache_key(asset_id, as_of, indicator_type, parameter_key, parameter_value, take):
    return _cache_key(
        'indicator_candidates',
        int(asset_id),
        str(as_of),
        str(indicator_type),
        str(parameter_key or ''),
        '' if parameter_value is None else str(parameter_value),
        int(take),
    )


def prime_indicator_candidates(asset_ids, as_of, signatures, cache):
    """Fetch one day's candidate lists for every asset in one query per signature.

    `signatures` is an iterable of ``(indicator_type, parameter_key, parameter_value, limit)``
    exactly as the single-asset reads pass them. Each becomes a correlated
    `ArraySubquery` that Postgres evaluates per asset with the same index descent and the
    same `LIMIT take` the single-asset query uses -- deliberately not a `ROW_NUMBER()`
    window, which would read every row below the bound and discard all but `take` of them,
    paying server-side work the current code never pays.

    Unprimed assets fall back to the single-asset read and are counted in
    ``INDICATOR_PREFETCH_STATS``, because a prefetch that silently covers nothing would look
    identical to one that works.
    """
    from django.contrib.postgres.expressions import ArraySubquery
    from django.db.models import OuterRef

    asset_ids = list(asset_ids)
    if not asset_ids or not signatures:
        return 0

    per_signature = []
    for indicator_type, parameter_key, parameter_value, limit in signatures:
        take = _candidate_take(limit)
        correlated = _indicator_candidate_queryset(
            models.Q(asset=OuterRef('pk')), as_of, indicator_type, parameter_key, parameter_value, take
        ).values('pk')
        rows = list(
            Asset.objects.filter(id__in=asset_ids)
            .annotate(candidate_pks=ArraySubquery(correlated))
            .values_list('id', 'candidate_pks')
        )
        # Hydrated per signature, not as one union across the day. A single `pk__in` list for
        # every signature was measured at 33.96 s for a candidate day that costs 20.72 s
        # without prefetching at all: Postgres parses a ~30,000-parameter IN list, which is
        # more expensive than the 2,495 small reads it replaces. One query per signature keeps
        # the round-trip win (~6,000 ids) without the parse cost.
        wanted_ids = sorted({pk for _asset_id, candidate_pks in rows for pk in (candidate_pks or [])})
        hydrated = {}
        if wanted_ids:
            for row in TechnicalIndicator.objects.filter(pk__in=wanted_ids):
                hydrated[row.pk] = row
        per_signature.append((indicator_type, parameter_key, parameter_value, take, rows, hydrated))

    primed = 0
    for indicator_type, parameter_key, parameter_value, take, rows, hydrated in per_signature:
        for asset_id, candidate_pks in rows:
            candidates = [hydrated[pk] for pk in (candidate_pks or []) if pk in hydrated]
            cache[_candidate_cache_key(asset_id, as_of, indicator_type, parameter_key, parameter_value, take)] = candidates
            primed += 1
    INDICATOR_PREFETCH_STATS['installed'] += primed
    return primed


def _matching_indicator_rows(
    asset_id,
    as_of,
    indicator_type,
    *,
    expected_parameters=None,
    required_keys=(),
    parameter_key=None,
    parameter_value=None,
    limit=1,
    cache=None,
):
    cache_entry = _cache_key(
        'matching_indicator_rows',
        int(asset_id),
        str(as_of),
        str(indicator_type),
        _parameter_cache_items(expected_parameters),
        tuple(required_keys or ()),
        str(parameter_key or ''),
        '' if parameter_value is None else str(parameter_value),
        int(limit or 1),
    )
    if cache is not None and cache_entry in cache:
        return cache[cache_entry]

    take = _candidate_take(limit)
    candidate_key = _candidate_cache_key(asset_id, as_of, indicator_type, parameter_key, parameter_value, take)
    if cache is not None and candidate_key in cache:
        candidates = cache[candidate_key]
        INDICATOR_PREFETCH_STATS['cache_hit'] += 1
    else:
        candidates = list(
            _indicator_candidate_queryset(
                models.Q(asset_id=asset_id), as_of, indicator_type, parameter_key, parameter_value, take
            )
        )
        INDICATOR_PREFETCH_STATS['fallback'] += 1
        if cache is not None:
            cache[candidate_key] = candidates
    best_rows_by_date = {}
    for indicator in candidates:
        score = _indicator_parameter_score(
            getattr(indicator, 'parameters', None),
            expected_parameters,
            required_keys,
        )
        if score < 0:
            continue
        indicator_date = timezone.localtime(indicator.timestamp).date()
        existing = best_rows_by_date.get(indicator_date)
        if existing is None or score > existing[0]:
            best_rows_by_date[indicator_date] = (score, indicator)

    ranked_dates = sorted(best_rows_by_date.keys(), reverse=True)
    rows = [best_rows_by_date[indicator_date][1] for indicator_date in ranked_dates[:limit]]
    if cache is not None:
        cache[cache_entry] = rows
    return rows


def _latest_indicator_value(
    asset_id,
    as_of,
    indicator_type,
    *,
    default=None,
    expected_parameters=None,
    required_keys=(),
    parameter_key=None,
    parameter_value=None,
    max_gap=None,
    cache=None,
):
    _asset, current_trade_date, position_map = _asset_trading_context(asset_id, as_of, cache=cache)
    indicators = _matching_indicator_rows(
        asset_id,
        as_of,
        indicator_type,
        expected_parameters=expected_parameters,
        required_keys=required_keys,
        parameter_key=parameter_key,
        parameter_value=parameter_value,
        limit=1,
        cache=cache,
    )
    if not indicators:
        return None if default is None else _to_decimal(default, default)

    if not stored_indicator_is_fresh(
        timezone.localtime(indicators[0].timestamp).date(),
        current_trade_date,
        position_map,
        indicator_type=indicator_type,
        parameters=expected_parameters,
        max_gap=max_gap,
    ):
        return None if default is None else _to_decimal(default, default)
    return _to_decimal(getattr(indicators[0], 'value', default), default)


def latest_ohlcv(asset_id, as_of, max_gap_trading_days=0, cache=None):
    cache_entry = _cache_key('latest_ohlcv', int(asset_id), str(as_of), int(max_gap_trading_days))
    if cache is not None and cache_entry in cache:
        return cache[cache_entry]

    _asset, current_trade_date, position_map = _asset_trading_context(asset_id, as_of, cache=cache)
    latest = OHLCV.objects.filter(asset_id=asset_id, date__lte=as_of).order_by('-date').first()
    if latest is None:
        if cache is not None:
            cache[cache_entry] = None
        return None
    if not stored_indicator_is_fresh(
        latest.date,
        current_trade_date,
        position_map,
        max_gap=max_gap_trading_days,
    ):
        if cache is not None:
            cache[cache_entry] = None
        return None
    if cache is not None:
        cache[cache_entry] = latest
    return latest


def latest_rsi(asset_id, as_of, timeperiod=14, default=Decimal('50'), cache=None):
    _asset, current_trade_date, position_map = _asset_trading_context(asset_id, as_of, cache=cache)
    indicators = _matching_indicator_rows(
        asset_id,
        as_of,
        'RSI',
        expected_parameters={'timeperiod': timeperiod},
        required_keys=('timeperiod',),
        parameter_key='timeperiod',
        parameter_value=timeperiod,
        limit=1,
        cache=cache,
    )
    if not indicators:
        return _to_decimal(default, default)

    if not stored_indicator_is_fresh(
        timezone.localtime(indicators[0].timestamp).date(),
        current_trade_date,
        position_map,
        indicator_type='RSI',
        parameters={'timeperiod': timeperiod},
    ):
        return _to_decimal(default, default)
    return _to_decimal(getattr(indicators[0], 'value', default), default)


def latest_momentum(asset_id, as_of, n_days=5, default=Decimal('0'), cache=None):
    _asset, current_trade_date, position_map = _asset_trading_context(asset_id, as_of, cache=cache)
    indicators = _matching_indicator_rows(
        asset_id,
        as_of,
        'MOM_5D' if int(n_days) == 5 else f'MOM_{int(n_days)}D',
        expected_parameters={'n_days': int(n_days)},
        required_keys=('n_days',),
        parameter_key='n_days',
        parameter_value=int(n_days),
        limit=1,
        cache=cache,
    )
    if not indicators:
        return _to_decimal(default, default)

    if not stored_indicator_is_fresh(
        timezone.localtime(indicators[0].timestamp).date(),
        current_trade_date,
        position_map,
        max_gap=0,
    ):
        return _to_decimal(default, default)
    return _to_decimal(getattr(indicators[0], 'value', default), default)


def latest_bbands(asset_id, as_of, timeperiod=20, nbdevup=2, nbdevdn=2, cache=None):
    _asset, current_trade_date, position_map = _asset_trading_context(asset_id, as_of, cache=cache)
    parameters = {'timeperiod': timeperiod, 'nbdevup': nbdevup, 'nbdevdn': nbdevdn}
    indicators = _matching_indicator_rows(
        asset_id,
        as_of,
        'BBANDS',
        expected_parameters=parameters,
        required_keys=('timeperiod',),
        parameter_key='timeperiod',
        parameter_value=timeperiod,
        limit=1,
        cache=cache,
    )
    if not indicators:
        return None

    if not stored_indicator_is_fresh(
        timezone.localtime(indicators[0].timestamp).date(),
        current_trade_date,
        position_map,
        indicator_type='BBANDS',
        parameters=parameters,
    ):
        return None

    latest = indicators[0]
    stored_parameters = dict(getattr(latest, 'parameters', None) or {})
    if 'upper' not in stored_parameters or 'lower' not in stored_parameters:
        return None
    return {
        'upper': _to_decimal(stored_parameters.get('upper'), '0'),
        'middle': _to_decimal(stored_parameters.get('middle', getattr(latest, 'value', None)), '0'),
        'lower': _to_decimal(stored_parameters.get('lower'), '0'),
    }


def latest_sma(asset_id, as_of, timeperiod=60, default=None, cache=None):
    _asset, current_trade_date, position_map = _asset_trading_context(asset_id, as_of, cache=cache)
    parameters = {'timeperiod': timeperiod}
    indicators = _matching_indicator_rows(
        asset_id,
        as_of,
        'SMA',
        expected_parameters=parameters,
        required_keys=('timeperiod',),
        parameter_key='timeperiod',
        parameter_value=timeperiod,
        limit=1,
        cache=cache,
    )
    if not indicators:
        return None if default is None else _to_decimal(default, default)

    if not stored_indicator_is_fresh(
        timezone.localtime(indicators[0].timestamp).date(),
        current_trade_date,
        position_map,
        indicator_type='SMA',
        parameters=parameters,
    ):
        return None if default is None else _to_decimal(default, default)
    return _to_decimal(getattr(indicators[0], 'value', default), default or '0')


def latest_rs_score(asset_id, as_of, default=Decimal('0.5'), cache=None):
    _asset, current_trade_date, position_map = _asset_trading_context(asset_id, as_of, cache=cache)
    cache_entry = _cache_key('latest_rs_score_indicator', int(asset_id), str(as_of))
    if cache is not None and cache_entry in cache:
        indicator = cache[cache_entry]
    else:
        indicator = TechnicalIndicator.objects.filter(
            asset_id=asset_id,
            indicator_type='RS_SCORE',
            timestamp__date__lte=as_of,
        ).order_by('-timestamp').first()
        if cache is not None:
            cache[cache_entry] = indicator

    if indicator is None:
        return _to_decimal(default, default)
    if not stored_indicator_is_fresh(
        timezone.localtime(indicator.timestamp).date(),
        current_trade_date,
        position_map,
        indicator_type='RS_SCORE',
    ):
        return _to_decimal(default, default)
    return _to_decimal(getattr(indicator, 'value', default), default)


def latest_return(asset_id, as_of, n_days=3, default=Decimal('0'), cache=None):
    resolved_days = int(n_days)
    return _latest_indicator_value(
        asset_id,
        as_of,
        f'RETURN_{resolved_days}D',
        default=default,
        expected_parameters={'n_days': resolved_days},
        required_keys=('n_days',),
        parameter_key='n_days',
        parameter_value=resolved_days,
        max_gap=0,
        cache=cache,
    )


def latest_relative_volume(asset_id, as_of, n_days=5, default=Decimal('1'), cache=None):
    resolved_days = int(n_days)
    return _latest_indicator_value(
        asset_id,
        as_of,
        f'RELATIVE_VOLUME_{resolved_days}D',
        default=default,
        expected_parameters={'n_days': resolved_days},
        required_keys=('n_days',),
        parameter_key='n_days',
        parameter_value=resolved_days,
        max_gap=0,
        cache=cache,
    )


def latest_realized_volatility(asset_id, as_of, window=5, default=Decimal('0'), cache=None):
    resolved_window = int(window)
    return _latest_indicator_value(
        asset_id,
        as_of,
        f'REALIZED_VOLATILITY_{resolved_window}D',
        default=default,
        expected_parameters={'window': resolved_window},
        required_keys=('window',),
        parameter_key='window',
        parameter_value=resolved_window,
        max_gap=0,
        cache=cache,
    )
