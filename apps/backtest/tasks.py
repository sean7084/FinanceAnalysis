from bisect import bisect_left
from datetime import date, timedelta
from decimal import Decimal
from statistics import mean, pstdev

import numpy as np
from celery import shared_task
from django.db import models, transaction
from django.utils import timezone

from apps.factors.models import FactorScore
from apps.markets.benchmarking import PIT_UNION_BENCHMARK_CODE, effective_universe_tradeable_asset_ids
from apps.markets.models import OHLCV, PointInTimeBenchmarkDaily
from apps.macro.models import MarketContext
from apps.prediction.odds import estimate_trade_decision
from apps.prediction.models import ModelVersion
from apps.prediction.tasks import _confidence, _feature_snapshot, _predicted_label, _probabilities_from_features
from apps.prediction.tasks_lightgbm import _extract_features_for_asset, _load_model_artifacts
from apps.prediction.tasks_lstm import _predict_with_lstm, _prime_lstm_inference_asset_ids
from apps.prediction.models_lightgbm import LightGBMModelArtifact
from .models import BacktestRun, BacktestTrade


DECIMAL_0 = Decimal('0')
DECIMAL_1 = Decimal('1')
DECIMAL_100 = Decimal('100')
DECIMAL_1000 = Decimal('1000')
TRADING_DAYS = Decimal('252')
DEFAULT_COMMISSION_RATE_PER_MILLE = Decimal('0.1')
DEFAULT_COMMISSION_MIN = Decimal('5')
DEFAULT_TRANSACTION_HANDLING_RATE_PER_MILLE = Decimal('0.0341')
DEFAULT_REGULATORY_FEE_RATE_PER_MILLE = Decimal('0.02')
DEFAULT_STAMP_DUTY_RATE_PER_MILLE = Decimal('0.5')
DEFAULT_TRANSFER_FEE_RATE_PER_MILLE = Decimal('0.01')
WEEKDAY_NAME_TO_INDEX = {
    'MON': 0,
    'TUE': 1,
    'WED': 2,
    'THU': 3,
    'FRI': 4,
}
VALID_TRADE_SCORE_SCOPES = {'independent', 'combined'}
PREDICTION_PAYLOAD_SOURCES = {'heuristic', 'lightgbm', 'lstm'}
TOP_N_METRIC_HORIZON_MAP = {
    'up_prob_3d': 3,
    'up_prob_7d': 7,
    'up_prob_30d': 30,
}
BACKTEST_CHUNK_TRADING_DAYS = 20


def _to_decimal_or_none(value):
    if value in (None, ''):
        return None
    try:
        return _d(value)
    except Exception:
        return None


def _d(value):
    return Decimal(str(value))


def _per_mille_to_ratio(value):
    return _d(value) / DECIMAL_1000


def _non_negative_decimal(name, value):
    resolved = _d(value)
    if resolved < 0:
        raise ValueError(f'{name} must be greater than or equal to 0.')
    return resolved


def _clamp(v, low=Decimal('-1'), high=Decimal('10')):
    return max(low, min(high, v))


def _resolve_fee_config(run):
    params = run.parameters or {}
    structured_keys = {
        'commission_rate_per_mille',
        'commission_min',
        'exchange_fee_rate_per_mille',
        'regulatory_fee_rate_per_mille',
        'stamp_duty_rate_per_mille',
        'transfer_fee_rate_per_mille',
    }

    if 'fee_rate' in params and any(key in params for key in structured_keys):
        raise ValueError('fee_rate cannot be combined with structured fee parameters.')

    if 'fee_rate' in params:
        legacy_rate = _non_negative_decimal('fee_rate', params.get('fee_rate', '0'))
        return {
            'mode': 'legacy_flat_fee',
            'commission_rate': DECIMAL_0,
            'commission_min': DECIMAL_0,
            'exchange_fee_rate': DECIMAL_0,
            'regulatory_fee_rate': DECIMAL_0,
            'stamp_duty_rate': DECIMAL_0,
            'transfer_fee_rate': DECIMAL_0,
            'buy_levy_rate': legacy_rate,
            'sell_levy_rate': legacy_rate,
            'summary': {
                'fee_rate': float(legacy_rate),
            },
        }

    commission_rate_per_mille = _non_negative_decimal(
        'commission_rate_per_mille',
        params.get('commission_rate_per_mille', DEFAULT_COMMISSION_RATE_PER_MILLE),
    )
    commission_min = _non_negative_decimal(
        'commission_min',
        params.get('commission_min', DEFAULT_COMMISSION_MIN),
    )
    transaction_handling_rate_per_mille = _non_negative_decimal(
        'exchange_fee_rate_per_mille',
        params.get('exchange_fee_rate_per_mille', DEFAULT_TRANSACTION_HANDLING_RATE_PER_MILLE),
    )
    regulatory_fee_rate_per_mille = _non_negative_decimal(
        'regulatory_fee_rate_per_mille',
        params.get('regulatory_fee_rate_per_mille', DEFAULT_REGULATORY_FEE_RATE_PER_MILLE),
    )
    stamp_duty_rate_per_mille = _non_negative_decimal(
        'stamp_duty_rate_per_mille',
        params.get('stamp_duty_rate_per_mille', DEFAULT_STAMP_DUTY_RATE_PER_MILLE),
    )
    transfer_fee_rate_per_mille = _non_negative_decimal(
        'transfer_fee_rate_per_mille',
        params.get('transfer_fee_rate_per_mille', DEFAULT_TRANSFER_FEE_RATE_PER_MILLE),
    )

    commission_rate = _per_mille_to_ratio(commission_rate_per_mille)
    transaction_handling_rate = _per_mille_to_ratio(transaction_handling_rate_per_mille)
    regulatory_fee_rate = _per_mille_to_ratio(regulatory_fee_rate_per_mille)
    stamp_duty_rate = _per_mille_to_ratio(stamp_duty_rate_per_mille)
    transfer_fee_rate = _per_mille_to_ratio(transfer_fee_rate_per_mille)

    return {
        'mode': 'cn_a_share_default',
        'commission_rate': commission_rate,
        'commission_min': commission_min,
        'exchange_fee_rate': transaction_handling_rate,
        'regulatory_fee_rate': regulatory_fee_rate,
        'stamp_duty_rate': stamp_duty_rate,
        'transfer_fee_rate': transfer_fee_rate,
        'buy_levy_rate': transaction_handling_rate + regulatory_fee_rate + transfer_fee_rate,
        'sell_levy_rate': transaction_handling_rate + regulatory_fee_rate + transfer_fee_rate + stamp_duty_rate,
        'summary': {
            'commission_rate_per_mille': float(commission_rate_per_mille),
            'commission_min': float(commission_min),
            'exchange_fee_rate_per_mille': float(transaction_handling_rate_per_mille),
            'regulatory_fee_rate_per_mille': float(regulatory_fee_rate_per_mille),
            'stamp_duty_rate_per_mille': float(stamp_duty_rate_per_mille),
            'transfer_fee_rate_per_mille': float(transfer_fee_rate_per_mille),
            'buy_total_rate_per_mille_ex_commission': float(
                transaction_handling_rate_per_mille + regulatory_fee_rate_per_mille + transfer_fee_rate_per_mille
            ),
            'sell_total_rate_per_mille_ex_commission': float(
                transaction_handling_rate_per_mille
                + regulatory_fee_rate_per_mille
                + stamp_duty_rate_per_mille
                + transfer_fee_rate_per_mille
            ),
        },
    }


def _trade_fee_breakdown(amount, fee_config, *, is_sell):
    if fee_config['mode'] == 'legacy_flat_fee':
        legacy_rate = fee_config['sell_levy_rate'] if is_sell else fee_config['buy_levy_rate']
        total_fee = amount * legacy_rate
        return {
            'commission': DECIMAL_0,
            'exchange_fee': DECIMAL_0,
            'regulatory_fee': DECIMAL_0,
            'transfer_fee': DECIMAL_0,
            'stamp_duty': DECIMAL_0,
            'total_fee': total_fee,
        }

    commission = amount * fee_config['commission_rate']
    if fee_config['commission_min'] > 0:
        commission = max(commission, fee_config['commission_min'])

    exchange_fee = amount * fee_config['exchange_fee_rate']
    regulatory_fee = amount * fee_config['regulatory_fee_rate']
    transfer_fee = amount * fee_config['transfer_fee_rate']
    stamp_duty = amount * fee_config['stamp_duty_rate'] if is_sell else DECIMAL_0
    total_fee = commission + exchange_fee + regulatory_fee + transfer_fee + stamp_duty
    return {
        'commission': commission,
        'exchange_fee': exchange_fee,
        'regulatory_fee': regulatory_fee,
        'transfer_fee': transfer_fee,
        'stamp_duty': stamp_duty,
        'total_fee': total_fee,
    }


def _fee_breakdown_payload(breakdown):
    return {key: float(value) for key, value in breakdown.items()}


def _max_buy_amount_for_budget(total_budget, fee_config):
    if total_budget <= 0:
        return DECIMAL_0

    variable_denom = DECIMAL_1 + fee_config['buy_levy_rate'] + fee_config['commission_rate']
    variable_amount = (total_budget / variable_denom) if variable_denom > 0 else DECIMAL_0

    if fee_config['commission_min'] <= 0:
        return max(DECIMAL_0, variable_amount)

    if fee_config['commission_rate'] > 0 and variable_amount * fee_config['commission_rate'] >= fee_config['commission_min']:
        return max(DECIMAL_0, variable_amount)

    if total_budget <= fee_config['commission_min']:
        return DECIMAL_0

    fixed_denom = DECIMAL_1 + fee_config['buy_levy_rate']
    return max(DECIMAL_0, (total_budget - fee_config['commission_min']) / fixed_denom)


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


def _eligible_backtest_asset_ids(dt, cache):
    cache_key = ('eligible_backtest_asset_ids', dt.isoformat())
    if cache_key in cache:
        return cache[cache_key]

    eligible_asset_ids = effective_universe_tradeable_asset_ids(
        dt,
        context=f'Backtest universe selection for {dt}',
    )
    cache[cache_key] = eligible_asset_ids
    return eligible_asset_ids


def _resolve_macro_context_for_date(dt, cache):
    cache_key = ('macro_context', dt.isoformat())
    if cache_key in cache:
        return cache[cache_key]

    context = (
        MarketContext.objects.filter(
            context_key='current',
            is_active=True,
            starts_at__lte=dt,
        )
        .filter(models.Q(ends_at__isnull=True) | models.Q(ends_at__gte=dt))
        .order_by('-starts_at', '-updated_at')
        .first()
    )

    if context is None:
        context = (
            MarketContext.objects.filter(context_key='current', is_active=True, starts_at__lte=dt)
            .order_by('-starts_at', '-updated_at')
            .first()
        )

    result = {
        'macro_phase': getattr(context, 'macro_phase', None),
        'event_tag': getattr(context, 'event_tag', ''),
    }
    cache[cache_key] = result
    return result


def _macro_rank_multiplier(macro_phase, up_probability):
    if up_probability is None:
        return DECIMAL_1

    up = _d(up_probability)
    if macro_phase == MarketContext.MacroPhase.RECOVERY:
        return Decimal('1.00') + max(DECIMAL_0, up - Decimal('0.50')) * Decimal('0.40')
    if macro_phase == MarketContext.MacroPhase.OVERHEAT:
        return Decimal('0.98') + max(DECIMAL_0, up - Decimal('0.55')) * Decimal('0.25')
    if macro_phase == MarketContext.MacroPhase.STAGFLATION:
        return Decimal('0.90') + max(DECIMAL_0, up - Decimal('0.60')) * Decimal('0.30')
    if macro_phase == MarketContext.MacroPhase.RECESSION:
        return Decimal('0.85') + max(DECIMAL_0, up - Decimal('0.65')) * Decimal('0.25')
    return DECIMAL_1


def _trade_score_scope(run):
    scope = str((run.parameters or {}).get('trade_score_scope', 'independent')).lower()
    return scope if scope in VALID_TRADE_SCORE_SCOPES else 'independent'


def _candidate_mode(run):
    mode = str((run.parameters or {}).get('candidate_mode', 'top_n')).lower()
    return mode if mode in {'top_n', 'trade_score'} else 'top_n'


def _top_n_metric(run, fallback_horizon):
    params = run.parameters or {}
    metric = str(params.get('top_n_metric') or '').lower()
    if metric not in {'trade_score', 'up_prob_3d', 'up_prob_7d', 'up_prob_30d'}:
        metric = {
            3: 'up_prob_3d',
            7: 'up_prob_7d',
            30: 'up_prob_30d',
        }.get(int(fallback_horizon), 'up_prob_7d')
    metric_horizon = TOP_N_METRIC_HORIZON_MAP.get(metric, int(fallback_horizon))
    return metric, metric_horizon


def _max_positions(run):
    params = run.parameters or {}
    if _candidate_mode(run) == 'trade_score':
        return max(1, int(params.get('max_positions', params.get('top_n', 5))))
    return 10 ** 6


def _trade_score_threshold(run):
    return _d((run.parameters or {}).get('trade_score_threshold', '1.0'))


def _use_macro_context(run):
    return bool((run.parameters or {}).get('use_macro_context', False))


def _enable_stop_target_exit(run):
    return bool((run.parameters or {}).get('enable_stop_target_exit', False))


def _trade_decision_policy(run):
    raw_policy = (run.parameters or {}).get('trade_decision_policy') or {}
    if not isinstance(raw_policy, dict):
        return {}
    policy = {}
    if 'include_near_round_target' in raw_policy:
        policy['include_near_round_target'] = bool(raw_policy['include_near_round_target'])
    for key in ('min_target_return_pct', 'min_stop_distance_pct'):
        value = _to_decimal_or_none(raw_policy.get(key))
        if value is not None:
            policy[key] = value
    return policy


def _trade_decision_policy_cache_key(policy):
    return tuple(sorted((key, str(value)) for key, value in (policy or {}).items()))


def _payload_trade_decision_policy(policy):
    payload = {}
    for key, value in (policy or {}).items():
        payload[key] = float(value) if isinstance(value, Decimal) else value
    return payload


def _predict_lightgbm_for_asset(asset_id, dt, horizon, cache, trade_decision_policy=None):
    policy_key = _trade_decision_policy_cache_key(trade_decision_policy)
    cache_key = ('lightgbm_prediction', int(asset_id), dt.isoformat(), int(horizon), policy_key)
    if cache_key in cache:
        return cache[cache_key]

    runtime = _get_lightgbm_runtime(horizon, cache)
    model_artifact = runtime['model_artifact']
    artifacts = runtime['artifacts']
    feature_names = runtime['feature_names']

    features_dict = _extract_features_for_asset(asset_id, dt)
    X = np.array([features_dict.get(name, 0.0) for name in feature_names]).reshape(1, -1)
    X_scaled = artifacts['scaler'].transform(X)
    calibrated_probs = artifacts['calibrator'].predict_proba(X_scaled)[0]
    down_prob, flat_prob, up_prob = [Decimal(str(value)) for value in calibrated_probs]
    confidence = max(up_prob, flat_prob, down_prob)
    predicted_label = ['DOWN', 'FLAT', 'UP'][int(np.argmax(calibrated_probs))]
    trade_decision = estimate_trade_decision(
        asset_id=asset_id,
        as_of=dt,
        horizon_days=horizon,
        up_probability=up_prob,
        predicted_label=predicted_label,
        policy_options=trade_decision_policy,
    )

    payload = {
        'up_probability': up_prob,
        'flat_probability': flat_prob,
        'down_probability': down_prob,
        'confidence': confidence,
        'predicted_label': predicted_label,
        'trade_score': trade_decision.get('trade_score'),
        'target_price': trade_decision.get('target_price'),
        'stop_loss_price': trade_decision.get('stop_loss_price'),
        'risk_reward_ratio': trade_decision.get('risk_reward_ratio'),
        'suggested': bool(trade_decision.get('suggested') or False),
        'model_artifact_id': model_artifact.id,
        'model_version': model_artifact.version,
        'generated_on_demand': True,
    }
    if trade_decision_policy:
        payload['trade_decision_policy'] = _payload_trade_decision_policy(trade_decision_policy)
    cache[cache_key] = payload
    return payload


def _build_lightgbm_prediction_map(dt, horizon, cache, trade_decision_policy=None):
    policy_key = _trade_decision_policy_cache_key(trade_decision_policy)
    cache_key = ('lightgbm_prediction_map', dt.isoformat(), int(horizon), policy_key)
    if cache_key in cache:
        return cache[cache_key]

    mapping = {}
    for asset_id in _eligible_backtest_asset_ids(dt, cache):
        mapping[asset_id] = _predict_lightgbm_for_asset(asset_id, dt, horizon, cache, trade_decision_policy)

    cache[cache_key] = mapping
    return mapping


def _predict_heuristic_for_asset(asset_id, dt, horizon, cache, trade_decision_policy=None):
    policy_key = _trade_decision_policy_cache_key(trade_decision_policy)
    cache_key = ('heuristic_prediction', int(asset_id), dt.isoformat(), int(horizon), policy_key)
    if cache_key in cache:
        return cache[cache_key]

    model_context = _get_heuristic_model_context(cache)
    features = _feature_snapshot(asset_id, dt)
    up, flat, down = _probabilities_from_features(features, horizon, '')
    predicted_label = _predicted_label(up, flat, down)
    confidence = _confidence(up, flat, down)
    trade_decision = estimate_trade_decision(
        asset_id=asset_id,
        as_of=dt,
        horizon_days=horizon,
        up_probability=up,
        predicted_label=predicted_label,
        policy_options=trade_decision_policy,
    )

    payload = {
        'up_probability': up,
        'flat_probability': flat,
        'down_probability': down,
        'confidence': confidence,
        'predicted_label': predicted_label,
        'trade_score': trade_decision.get('trade_score'),
        'target_price': trade_decision.get('target_price'),
        'stop_loss_price': trade_decision.get('stop_loss_price'),
        'risk_reward_ratio': trade_decision.get('risk_reward_ratio'),
        'suggested': bool(trade_decision.get('suggested') or False),
        'model_version_id': model_context['model_version_id'],
        'model_version': model_context['model_version'],
        'generated_on_demand': True,
    }
    if trade_decision_policy:
        payload['trade_decision_policy'] = _payload_trade_decision_policy(trade_decision_policy)
    cache[cache_key] = payload
    return payload


def _build_heuristic_prediction_map(dt, horizon, cache, trade_decision_policy=None):
    policy_key = _trade_decision_policy_cache_key(trade_decision_policy)
    cache_key = ('heuristic_prediction_map', dt.isoformat(), int(horizon), policy_key)
    if cache_key in cache:
        return cache[cache_key]

    mapping = {}
    for asset_id in _eligible_backtest_asset_ids(dt, cache):
        mapping[asset_id] = _predict_heuristic_for_asset(asset_id, dt, horizon, cache, trade_decision_policy)

    cache[cache_key] = mapping
    return mapping


def _predict_lstm_for_asset(asset_id, dt, horizon, cache, trade_decision_policy=None):
    policy_key = _trade_decision_policy_cache_key(trade_decision_policy)
    cache_key = ('lstm_prediction', int(asset_id), dt.isoformat(), int(horizon), policy_key)
    if cache_key in cache:
        return cache[cache_key]

    runtime_cache = cache.setdefault('lstm_runtime_cache', {})
    prediction = _predict_with_lstm(
        asset_id=asset_id,
        target_date=dt,
        horizon_days=horizon,
        cache=runtime_cache,
    )
    if prediction is None:
        cache[cache_key] = None
        return None

    if trade_decision_policy:
        trade_decision = estimate_trade_decision(
            asset_id=asset_id,
            as_of=dt,
            horizon_days=horizon,
            up_probability=_d(prediction.get('up_probability', 0)),
            predicted_label=prediction.get('predicted_label') or '',
            policy_options=trade_decision_policy,
        )
    else:
        trade_decision = prediction.get('trade_decision') or {}
    model_version = prediction.get('model_version')
    payload = {
        'up_probability': _d(prediction.get('up_probability', 0)),
        'flat_probability': _d(prediction.get('flat_probability', 0)),
        'down_probability': _d(prediction.get('down_probability', 0)),
        'confidence': _d(prediction.get('confidence', 0)),
        'predicted_label': prediction.get('predicted_label') or '',
        'trade_score': trade_decision.get('trade_score'),
        'target_price': trade_decision.get('target_price'),
        'stop_loss_price': trade_decision.get('stop_loss_price'),
        'risk_reward_ratio': trade_decision.get('risk_reward_ratio'),
        'suggested': bool(trade_decision.get('suggested') or False),
        'model_version_id': getattr(model_version, 'id', None),
        'model_version': getattr(model_version, 'version', None),
        'generated_on_demand': True,
    }
    if trade_decision_policy:
        payload['trade_decision_policy'] = _payload_trade_decision_policy(trade_decision_policy)
    cache[cache_key] = payload
    return payload


def _build_lstm_prediction_map(dt, horizon, cache, trade_decision_policy=None):
    policy_key = _trade_decision_policy_cache_key(trade_decision_policy)
    cache_key = ('lstm_prediction_map', dt.isoformat(), int(horizon), policy_key)
    if cache_key in cache:
        return cache[cache_key]

    asset_ids = _eligible_backtest_asset_ids(dt, cache)
    runtime_cache = cache.setdefault('lstm_runtime_cache', {})
    _prime_lstm_inference_asset_ids(runtime_cache, dt, asset_ids)

    mapping = {}
    for asset_id in asset_ids:
        payload = _predict_lstm_for_asset(asset_id, dt, horizon, cache, trade_decision_policy)
        if payload is not None:
            mapping[asset_id] = payload

    cache[cache_key] = mapping
    return mapping


def _build_trade_score_candidates(run, dt, horizon, up_threshold, max_positions, cache):
    scope = _trade_score_scope(run)
    threshold = _trade_score_threshold(run)
    prediction_source = _prediction_source(run)
    trade_decision_policy = _trade_decision_policy(run)
    policy_key = _trade_decision_policy_cache_key(trade_decision_policy)

    cache_key = ('trade_score_candidates', scope, prediction_source, dt.isoformat(), horizon, str(up_threshold), str(threshold), max_positions, policy_key)
    if cache_key in cache:
        return cache[cache_key]

    heuristic_map = _build_heuristic_prediction_map(dt, horizon, cache, trade_decision_policy)
    lightgbm_map = _build_lightgbm_prediction_map(dt, horizon, cache, trade_decision_policy) if scope == 'combined' or prediction_source == 'lightgbm' else {}
    lstm_map = _build_lstm_prediction_map(dt, horizon, cache, trade_decision_policy) if prediction_source == 'lstm' else {}
    selected_rows = []

    if scope == 'combined':
        for asset_id in sorted(set(heuristic_map.keys()) | set(lightgbm_map.keys())):
            h_row = heuristic_map.get(asset_id)
            l_row = lightgbm_map.get(asset_id)
            scores = [x for x in [_to_decimal_or_none(h_row.get('trade_score') if h_row else None), _to_decimal_or_none(l_row.get('trade_score') if l_row else None)] if x is not None]
            up_probs = [x for x in [_to_decimal_or_none(h_row.get('up_probability') if h_row else None), _to_decimal_or_none(l_row.get('up_probability') if l_row else None)] if x is not None]
            if not scores or not up_probs:
                continue

            combined_trade_score = sum(scores, DECIMAL_0) / _d(len(scores))
            combined_up_prob = sum(up_probs, DECIMAL_0) / _d(len(up_probs))
            if combined_trade_score < threshold or combined_up_prob < up_threshold:
                continue

            selected_rows.append({
                'asset_id': asset_id,
                'rank_value': combined_trade_score,
                'signal_payload': {
                    'strategy': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
                    'prediction_source': prediction_source,
                    'candidate_mode': 'trade_score',
                    'trade_score_scope': 'combined',
                    'combined_trade_score': float(combined_trade_score),
                    'combined_up_probability': float(combined_up_prob),
                    'horizon_days': horizon,
                    'heuristic_trade_score': float(h_row['trade_score']) if h_row and h_row.get('trade_score') is not None else None,
                    'lightgbm_trade_score': float(l_row['trade_score']) if l_row and l_row.get('trade_score') is not None else None,
                    'target_price': float(h_row['target_price']) if h_row and h_row.get('target_price') is not None else (float(l_row['target_price']) if l_row and l_row.get('target_price') is not None else None),
                    'stop_loss_price': float(h_row['stop_loss_price']) if h_row and h_row.get('stop_loss_price') is not None else (float(l_row['stop_loss_price']) if l_row and l_row.get('stop_loss_price') is not None else None),
                    'generated_on_demand': True,
                },
            })
    else:
        if prediction_source == 'lightgbm':
            source_map = lightgbm_map
        elif prediction_source == 'lstm':
            source_map = lstm_map
        else:
            source_map = heuristic_map
        for asset_id, row in source_map.items():
            up_prob = _to_decimal_or_none(row.get('up_probability'))
            trade_score = _to_decimal_or_none(row.get('trade_score'))
            if up_prob is None or trade_score is None:
                continue
            if up_prob < up_threshold or trade_score < threshold:
                continue

            payload = {
                'strategy': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
                'prediction_source': prediction_source,
                'candidate_mode': 'trade_score',
                'trade_score_scope': 'independent',
                'horizon_days': horizon,
                'up_probability': float(up_prob),
                'flat_probability': float(row.get('flat_probability') or 0),
                'down_probability': float(row.get('down_probability') or 0),
                'confidence': float(row.get('confidence') or 0),
                'predicted_label': row.get('predicted_label') or '',
                'trade_score': float(trade_score),
                'target_price': float(row['target_price']) if row.get('target_price') is not None else None,
                'stop_loss_price': float(row['stop_loss_price']) if row.get('stop_loss_price') is not None else None,
                'risk_reward_ratio': float(row['risk_reward_ratio']) if row.get('risk_reward_ratio') is not None else None,
                'suggested': bool(row.get('suggested') or False),
                'model_version_id': row.get('model_version_id'),
                'model_version': row.get('model_version'),
                'model_artifact_id': row.get('model_artifact_id'),
                'generated_on_demand': bool(row.get('generated_on_demand', False)),
            }
            selected_rows.append({
                'asset_id': asset_id,
                'rank_value': trade_score,
                'signal_payload': payload,
            })

    selected_rows.sort(key=lambda row: row['rank_value'], reverse=True)
    rows = selected_rows[:max_positions]
    cache[cache_key] = rows
    return rows


def _build_top_n_trade_score_candidates(run, dt, horizon, up_threshold, top_n, cache):
    prediction_source = _prediction_source(run)
    trade_decision_policy = _trade_decision_policy(run)
    policy_key = _trade_decision_policy_cache_key(trade_decision_policy)
    cache_key = ('top_n_trade_score_candidates', prediction_source, dt.isoformat(), horizon, str(up_threshold), top_n, policy_key)
    if cache_key in cache:
        return cache[cache_key]

    if prediction_source == 'lightgbm':
        source_map = _build_lightgbm_prediction_map(dt, horizon, cache, trade_decision_policy)
    elif prediction_source == 'lstm':
        source_map = _build_lstm_prediction_map(dt, horizon, cache, trade_decision_policy)
    else:
        source_map = _build_heuristic_prediction_map(dt, horizon, cache, trade_decision_policy)

    selected_rows = []
    for asset_id, row in source_map.items():
        up_prob = _to_decimal_or_none(row.get('up_probability'))
        trade_score = _to_decimal_or_none(row.get('trade_score'))
        if up_prob is None or trade_score is None:
            continue
        if up_prob < up_threshold:
            continue

        selected_rows.append({
            'asset_id': asset_id,
            'rank_value': trade_score,
            'signal_payload': {
                'strategy': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
                'prediction_source': prediction_source,
                'candidate_mode': 'top_n',
                'top_n_metric': 'trade_score',
                'horizon_days': horizon,
                'up_probability': float(up_prob),
                'flat_probability': float(row.get('flat_probability') or 0),
                'down_probability': float(row.get('down_probability') or 0),
                'confidence': float(row.get('confidence') or 0),
                'predicted_label': row.get('predicted_label') or '',
                'trade_score': float(trade_score),
                'target_price': float(row['target_price']) if row.get('target_price') is not None else None,
                'stop_loss_price': float(row['stop_loss_price']) if row.get('stop_loss_price') is not None else None,
                'risk_reward_ratio': float(row['risk_reward_ratio']) if row.get('risk_reward_ratio') is not None else None,
                'suggested': bool(row.get('suggested') or False),
                'model_artifact_id': row.get('model_artifact_id'),
                'model_version': row.get('model_version'),
                'generated_on_demand': bool(row.get('generated_on_demand', False)),
            },
        })

    selected_rows.sort(key=lambda row: row['rank_value'], reverse=True)
    rows = selected_rows[:top_n]
    cache[cache_key] = rows
    return rows


def _normalize_entry_weekdays(raw_value):
    if raw_value in (None, '', []):
        return None

    if isinstance(raw_value, str):
        values = [part.strip().upper() for part in raw_value.split(',') if part.strip()]
    elif isinstance(raw_value, (list, tuple)):
        values = list(raw_value)
    else:
        raise ValueError('entry_weekdays must be a list or comma-separated string.')

    normalized = []
    for item in values:
        if isinstance(item, int):
            weekday = item
        else:
            key = str(item).strip().upper()[:3]
            if key not in WEEKDAY_NAME_TO_INDEX:
                raise ValueError(f'Unsupported weekday value: {item}')
            weekday = WEEKDAY_NAME_TO_INDEX[key]
        if weekday < 0 or weekday > 6:
            raise ValueError(f'Weekday index out of range: {weekday}')
        if weekday not in normalized:
            normalized.append(weekday)
    return normalized or None


def _holding_period_days(run):
    params = run.parameters or {}
    raw_value = params.get('holding_period_days')
    if raw_value is None:
        return 1
    holding_period_days = int(raw_value)
    if holding_period_days <= 0:
        raise ValueError('holding_period_days must be greater than 0.')
    return holding_period_days


def _entry_weekdays(run):
    params = run.parameters or {}
    return _normalize_entry_weekdays(params.get('entry_weekdays'))


def _capital_fraction_per_entry(run, entry_weekdays):
    params = run.parameters or {}
    raw_value = params.get('capital_fraction_per_entry')
    if raw_value is not None:
        fraction = _d(raw_value)
    elif entry_weekdays:
        fraction = DECIMAL_1 / _d(len(entry_weekdays))
    else:
        fraction = DECIMAL_1

    if fraction <= 0:
        raise ValueError('capital_fraction_per_entry must be greater than 0.')
    return min(fraction, DECIMAL_1)


def _should_enter_position(dt, entry_weekdays):
    if not entry_weekdays:
        return True
    return dt.weekday() in entry_weekdays


def _resolve_exit_date(trading_dates, entry_date, holding_period_days):
    target_exit_date = entry_date + timedelta(days=holding_period_days)
    position = bisect_left(trading_dates, target_exit_date)
    if position >= len(trading_dates):
        return None
    return trading_dates[position]


def _build_equal_weight_benchmark_equity_curve(trading_dates, price_map, initial_capital):
    benchmark_equity_curve = [_d(initial_capital)]
    benchmark_value = _d(initial_capital)
    for idx in range(1, len(trading_dates)):
        previous_date = trading_dates[idx - 1]
        current_date = trading_dates[idx]
        returns = []
        for (asset_id, dt), close in price_map.items():
            if dt != previous_date:
                continue
            next_close = price_map.get((asset_id, current_date))
            if next_close is None or close <= 0:
                continue
            returns.append((next_close - close) / close)
        if returns:
            average_return = sum(returns, DECIMAL_0) / _d(len(returns))
            benchmark_value *= (DECIMAL_1 + average_return)
        benchmark_equity_curve.append(benchmark_value)
    return benchmark_equity_curve


def _build_benchmark_equity_curve(trading_dates, price_map, initial_capital):
    pit_rows = list(
        PointInTimeBenchmarkDaily.objects.filter(
            benchmark_code=PIT_UNION_BENCHMARK_CODE,
            trade_date__in=trading_dates,
        )
        .order_by('trade_date')
        .values('trade_date', 'nav', 'weighting_method', 'metadata')
    )

    pit_dates = [row['trade_date'] for row in pit_rows]
    if pit_rows and pit_dates == list(trading_dates):
        base_nav = _d(pit_rows[0]['nav']) if pit_rows[0]['nav'] is not None else DECIMAL_0
        scale = (_d(initial_capital) / base_nav) if base_nav > 0 else DECIMAL_1
        return [
            _d(row['nav']) * scale
            for row in pit_rows
        ], {
            'strategy': 'point_in_time_union_benchmark',
            'benchmark_code': PIT_UNION_BENCHMARK_CODE,
            'weighting_method': pit_rows[0]['weighting_method'] or 'free_float_market_cap',
            'source': 'precomputed_point_in_time_benchmark',
            'coverage_status': 'complete',
            'trade_dates': [dt.isoformat() for dt in pit_dates],
        }

    return _build_equal_weight_benchmark_equity_curve(trading_dates, price_map, initial_capital), {
        'strategy': 'equal_weight_universe_daily_return',
        'source': 'runtime_fallback_equal_weight',
        'coverage_status': 'missing_or_incomplete_point_in_time_benchmark',
    }


def _serialize_provenance_value(value):
    return value.isoformat() if hasattr(value, 'isoformat') else value


def _compact_provenance_row(row):
    return {
        key: _serialize_provenance_value(value)
        for key, value in row.items()
        if value not in (None, '', [], {})
    }


def _reference_metadata_subset(metadata):
    if not isinstance(metadata, dict):
        return {}

    row = {}
    for key in (
        'schema_version',
        'effective_universe_policy',
        'universe_version',
        'label_definition',
        'data_snapshot_version',
        'code_version',
        'git_commit',
        'missing_value_strategy',
    ):
        value = metadata.get(key)
        if value not in (None, '', [], {}):
            row[key] = value
    return row


def _collect_run_model_references(run):
    version_payloads = {}
    artifact_payloads = {}

    for trade in run.trades.only('signal_payload').all():
        payload = trade.signal_payload or {}
        prediction_source = str(payload.get('prediction_source') or '').lower()
        try:
            horizon_days = int(payload['horizon_days']) if payload.get('horizon_days') is not None else None
        except (TypeError, ValueError):
            horizon_days = None

        if payload.get('model_version_id'):
            try:
                model_version_id = int(payload['model_version_id'])
            except (TypeError, ValueError):
                model_version_id = None
            if model_version_id is not None and model_version_id not in version_payloads:
                version_payloads[model_version_id] = {
                    'prediction_source': prediction_source,
                    'horizon_days': horizon_days,
                }

        if payload.get('model_artifact_id'):
            try:
                model_artifact_id = int(payload['model_artifact_id'])
            except (TypeError, ValueError):
                model_artifact_id = None
            if model_artifact_id is not None and model_artifact_id not in artifact_payloads:
                artifact_payloads[model_artifact_id] = {
                    'prediction_source': prediction_source,
                    'horizon_days': horizon_days,
                }

    references = []
    for model_version in ModelVersion.objects.filter(id__in=version_payloads.keys()).order_by('model_type', 'version'):
        payload_context = version_payloads.get(model_version.id, {})
        references.append(_compact_provenance_row({
            'prediction_source': payload_context.get('prediction_source') or model_version.model_type.lower(),
            'reference_type': 'ModelVersion',
            'reference_id': model_version.id,
            'version': model_version.version,
            'horizon_days': payload_context.get('horizon_days'),
            'status': model_version.status,
            'is_active': model_version.is_active,
            'feature_count': len(model_version.feature_schema or []),
            'training_window_start': model_version.training_window_start,
            'training_window_end': model_version.training_window_end,
            'trained_at': model_version.trained_at,
            **_reference_metadata_subset(model_version.metadata),
        }))

    for artifact in LightGBMModelArtifact.objects.filter(id__in=artifact_payloads.keys()).order_by('horizon_days', 'version'):
        payload_context = artifact_payloads.get(artifact.id, {})
        references.append(_compact_provenance_row({
            'prediction_source': payload_context.get('prediction_source') or 'lightgbm',
            'reference_type': 'LightGBMModelArtifact',
            'reference_id': artifact.id,
            'version': artifact.version,
            'horizon_days': payload_context.get('horizon_days') or artifact.horizon_days,
            'status': artifact.status,
            'is_active': artifact.is_active,
            'feature_count': len(artifact.feature_names or []),
            'training_window_start': artifact.training_window_start,
            'training_window_end': artifact.training_window_end,
            'trained_at': artifact.trained_at,
            **_reference_metadata_subset(artifact.metadata),
        }))

    return references


def _prediction_source(run):
    params = run.parameters or {}
    return str(params.get('prediction_source', 'heuristic')).lower()


def _get_lightgbm_runtime(horizon, cache):
    runtime_key = ('lightgbm_runtime', horizon)
    if runtime_key in cache:
        return cache[runtime_key]

    model_artifact = LightGBMModelArtifact.objects.filter(
        horizon_days=horizon,
        is_active=True,
        status=LightGBMModelArtifact.Status.READY,
    ).order_by('-trained_at').first()
    if model_artifact is None:
        raise ValueError(f'No active LightGBM artifact available for horizon {horizon}.')

    artifacts = _load_model_artifacts(horizon, model_artifact.version)
    if not artifacts:
        raise ValueError(f'Unable to load LightGBM artifacts for horizon {horizon}.')

    runtime = {
        'model_artifact': model_artifact,
        'artifacts': artifacts,
        'feature_names': artifacts['metadata']['feature_names'],
    }
    cache[runtime_key] = runtime
    return runtime


def _get_heuristic_model_context(cache):
    context_key = 'heuristic_model_context'
    if context_key in cache:
        return cache[context_key]

    model_version = ModelVersion.objects.filter(
        model_type=ModelVersion.ModelType.ENSEMBLE,
        is_active=True,
        status=ModelVersion.Status.READY,
    ).order_by('-trained_at', '-created_at').first()

    context = {
        'model_version_id': model_version.id if model_version else None,
        'model_version': model_version.version if model_version else 'heuristic-baseline',
    }
    cache[context_key] = context
    return context


def _build_heuristic_candidates(dt, horizon, up_threshold, top_n, cache, trade_decision_policy=None):
    rows = []
    for asset_id, payload in _build_heuristic_prediction_map(dt, horizon, cache, trade_decision_policy).items():
        up = _to_decimal_or_none(payload.get('up_probability')) or DECIMAL_0
        if up < up_threshold:
            continue
        rows.append({
            'asset_id': asset_id,
            'rank_value': up,
            'signal_payload': {
                'strategy': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
                'prediction_source': 'heuristic',
                'horizon_days': horizon,
                'up_probability': float(up),
                'flat_probability': float(payload.get('flat_probability') or 0),
                'down_probability': float(payload.get('down_probability') or 0),
                'confidence': float(payload.get('confidence') or 0),
                'predicted_label': payload.get('predicted_label') or '',
                'trade_score': float(payload['trade_score']) if payload.get('trade_score') is not None else None,
                'target_price': float(payload['target_price']) if payload.get('target_price') is not None else None,
                'stop_loss_price': float(payload['stop_loss_price']) if payload.get('stop_loss_price') is not None else None,
                'risk_reward_ratio': float(payload['risk_reward_ratio']) if payload.get('risk_reward_ratio') is not None else None,
                'suggested': bool(payload.get('suggested') or False),
                'model_version_id': payload.get('model_version_id'),
                'model_version': payload.get('model_version'),
                'trade_decision_policy': payload.get('trade_decision_policy'),
                'generated_on_demand': True,
            },
        })

    rows.sort(key=lambda row: row['rank_value'], reverse=True)
    return rows[:top_n]


def _build_lightgbm_candidates(dt, horizon, up_threshold, top_n, cache, trade_decision_policy=None):
    rows = []
    for asset_id, payload in _build_lightgbm_prediction_map(dt, horizon, cache, trade_decision_policy).items():
        up_prob = _to_decimal_or_none(payload.get('up_probability')) or DECIMAL_0
        if up_prob < up_threshold:
            continue
        rows.append({
            'asset_id': asset_id,
            'rank_value': up_prob,
            'signal_payload': {
                'strategy': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
                'prediction_source': 'lightgbm',
                'horizon_days': horizon,
                'up_probability': float(up_prob),
                'flat_probability': float(payload.get('flat_probability') or 0),
                'down_probability': float(payload.get('down_probability') or 0),
                'confidence': float(payload.get('confidence') or 0),
                'predicted_label': payload.get('predicted_label') or '',
                'trade_score': float(payload['trade_score']) if payload.get('trade_score') is not None else None,
                'target_price': float(payload['target_price']) if payload.get('target_price') is not None else None,
                'stop_loss_price': float(payload['stop_loss_price']) if payload.get('stop_loss_price') is not None else None,
                'risk_reward_ratio': float(payload['risk_reward_ratio']) if payload.get('risk_reward_ratio') is not None else None,
                'suggested': bool(payload.get('suggested') or False),
                'model_artifact_id': payload.get('model_artifact_id'),
                'model_version': payload.get('model_version'),
                'trade_decision_policy': payload.get('trade_decision_policy'),
                'generated_on_demand': bool(payload.get('generated_on_demand') or False),
            },
        })

    rows.sort(key=lambda row: row['rank_value'], reverse=True)
    return rows[:top_n]


def _pick_candidates(run, dt, cache):
    params = run.parameters or {}
    top_n = int(params.get('top_n', 5))

    if run.strategy_type == BacktestRun.StrategyType.BOTTOM_CANDIDATE:
        threshold = _d(params.get('bottom_threshold', '0.60'))
        eligible_asset_ids = _eligible_backtest_asset_ids(dt, cache)
        qs = (
            FactorScore.objects.filter(
                asset_id__in=eligible_asset_ids,
                date=dt,
                mode=FactorScore.FactorMode.COMPOSITE,
                bottom_probability_score__gte=threshold,
            )
            .order_by('-bottom_probability_score')
            .values_list('asset_id', 'bottom_probability_score')
        )
        return [
            {
                'asset_id': asset_id,
                'signal_payload': {
                    'strategy': BacktestRun.StrategyType.BOTTOM_CANDIDATE,
                    'prediction_source': 'factor_score',
                    'bottom_probability_score': float(score),
                    'generated_on_demand': False,
                },
            }
            for asset_id, score in list(qs[:top_n])
        ]

    horizon = int(params.get('horizon_days', 7))
    up_threshold = _d(params.get('up_threshold', '0.55'))
    candidate_mode = _candidate_mode(run)
    max_positions = _max_positions(run)
    macro_context_enabled = _use_macro_context(run)
    macro_context = _resolve_macro_context_for_date(dt, cache) if macro_context_enabled else {'macro_phase': None, 'event_tag': ''}

    if candidate_mode == 'trade_score':
        rows = _build_trade_score_candidates(run, dt, horizon, up_threshold, max_positions, cache)
        if macro_context_enabled and rows:
            adjusted_rows = []
            for row in rows:
                up_prob = row['signal_payload'].get('up_probability')
                if up_prob is None:
                    up_prob = row['signal_payload'].get('combined_up_probability')
                multiplier = _macro_rank_multiplier(macro_context.get('macro_phase'), up_prob)
                signal_payload = {
                    **row['signal_payload'],
                    'macro_phase': macro_context.get('macro_phase'),
                    'event_tag': macro_context.get('event_tag') or '',
                    'macro_rank_multiplier': float(multiplier),
                }
                adjusted_rows.append({
                    'asset_id': row['asset_id'],
                    'rank_value': row['rank_value'] * multiplier,
                    'signal_payload': signal_payload,
                })
            adjusted_rows.sort(key=lambda item: item['rank_value'], reverse=True)
            return adjusted_rows[:max_positions]
        return rows

    prediction_source = _prediction_source(run)
    top_n_metric, metric_horizon = _top_n_metric(run, horizon)
    trade_decision_policy = _trade_decision_policy(run)
    policy_key = _trade_decision_policy_cache_key(trade_decision_policy)
    cache_key = ('prediction_candidates', prediction_source, top_n_metric, dt.isoformat(), metric_horizon, str(up_threshold), top_n, policy_key)
    if cache_key in cache:
        rows = cache[cache_key]
    elif top_n_metric == 'trade_score':
        rows = _build_top_n_trade_score_candidates(run, dt, metric_horizon, up_threshold, top_n, cache)
        cache[cache_key] = rows
    elif prediction_source == 'lightgbm':
        rows = _build_lightgbm_candidates(dt, metric_horizon, up_threshold, top_n, cache, trade_decision_policy)
        for row in rows:
            row['signal_payload']['top_n_metric'] = top_n_metric
            row['signal_payload']['candidate_mode'] = 'top_n'
        cache[cache_key] = rows
    elif prediction_source == 'lstm':
        rows = []
        for asset_id, payload in _build_lstm_prediction_map(dt, metric_horizon, cache, trade_decision_policy).items():
            rank_value = _to_decimal_or_none(payload.get('up_probability')) or DECIMAL_0
            if rank_value < up_threshold:
                continue
            rows.append({
                'asset_id': asset_id,
                'rank_value': rank_value,
                'signal_payload': {
                    'strategy': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
                    'prediction_source': 'lstm',
                    'candidate_mode': 'top_n',
                    'top_n_metric': top_n_metric,
                    'horizon_days': metric_horizon,
                    'up_probability': float(payload.get('up_probability') or 0),
                    'flat_probability': float(payload.get('flat_probability') or 0),
                    'down_probability': float(payload.get('down_probability') or 0),
                    'confidence': float(payload.get('confidence') or 0),
                    'predicted_label': payload.get('predicted_label') or '',
                    'trade_score': float(payload['trade_score']) if payload.get('trade_score') is not None else None,
                    'target_price': float(payload['target_price']) if payload.get('target_price') is not None else None,
                    'stop_loss_price': float(payload['stop_loss_price']) if payload.get('stop_loss_price') is not None else None,
                    'risk_reward_ratio': float(payload['risk_reward_ratio']) if payload.get('risk_reward_ratio') is not None else None,
                    'suggested': bool(payload.get('suggested') or False),
                    'model_version_id': payload.get('model_version_id'),
                    'model_version': payload.get('model_version'),
                    'trade_decision_policy': payload.get('trade_decision_policy'),
                    'generated_on_demand': True,
                },
            })
        rows.sort(key=lambda row: row['rank_value'], reverse=True)
        rows = rows[:top_n]
        cache[cache_key] = rows
    else:
        rows = _build_heuristic_candidates(dt, metric_horizon, up_threshold, top_n, cache, trade_decision_policy)
        for row in rows:
            row['signal_payload']['top_n_metric'] = top_n_metric
            row['signal_payload']['candidate_mode'] = 'top_n'
        cache[cache_key] = rows

    if not macro_context_enabled or not rows:
        return rows

    adjusted_rows = []
    for row in rows:
        up_prob = row['signal_payload'].get('up_probability')
        multiplier = _macro_rank_multiplier(macro_context.get('macro_phase'), up_prob)
        signal_payload = {
            **row['signal_payload'],
            'macro_phase': macro_context.get('macro_phase'),
            'event_tag': macro_context.get('event_tag') or '',
            'macro_rank_multiplier': float(multiplier),
        }
        adjusted_rows.append({
            'asset_id': row['asset_id'],
            'rank_value': row['rank_value'] * multiplier,
            'signal_payload': signal_payload,
        })
    adjusted_rows.sort(key=lambda item: item['rank_value'], reverse=True)
    return adjusted_rows[:top_n]


def _backfill_prediction_trade_decision(asset_id, current_date, signal_payload):
    if signal_payload.get('strategy') != BacktestRun.StrategyType.PREDICTION_THRESHOLD:
        return signal_payload
    if signal_payload.get('prediction_source') not in PREDICTION_PAYLOAD_SOURCES:
        return signal_payload

    missing_trade_fields = any(
        signal_payload.get(field) is None
        for field in ('trade_score', 'target_price', 'stop_loss_price')
    )
    missing_suggested = signal_payload.get('suggested') is None
    if not missing_trade_fields and not missing_suggested:
        return signal_payload

    horizon = signal_payload.get('horizon_days')
    up_probability = _to_decimal_or_none(signal_payload.get('up_probability'))
    predicted_label = signal_payload.get('predicted_label') or ''
    if horizon is None or up_probability is None or not predicted_label:
        return signal_payload

    try:
        horizon_days = int(horizon)
    except (TypeError, ValueError):
        return signal_payload

    trade_decision = estimate_trade_decision(
        asset_id=asset_id,
        as_of=current_date,
        horizon_days=horizon_days,
        up_probability=up_probability,
        predicted_label=predicted_label,
        policy_options=signal_payload.get('trade_decision_policy'),
    )

    for field in ('trade_score', 'target_price', 'stop_loss_price'):
        if signal_payload.get(field) is None and trade_decision.get(field) is not None:
            signal_payload[field] = float(trade_decision[field])
    if signal_payload.get('suggested') is None:
        signal_payload['suggested'] = bool(trade_decision.get('suggested') or False)

    return signal_payload


def _selection_audit_payload(run, row, candidate_rank):
    params = run.parameters or {}
    signal_payload = row.get('signal_payload') or {}
    rank_value = row.get('rank_value')
    payload = {
        'candidate_rank': int(candidate_rank),
        'rank_value': float(rank_value) if rank_value is not None else None,
        'candidate_rank_value': float(rank_value) if rank_value is not None else None,
        'candidate_selected': True,
    }

    up_threshold = params.get('up_threshold')
    if up_threshold is not None:
        payload['up_threshold'] = float(up_threshold)
        up_probability = signal_payload.get('up_probability')
        if up_probability is None:
            up_probability = signal_payload.get('combined_up_probability')
        if up_probability is not None:
            payload['passed_up_threshold'] = float(up_probability) >= float(up_threshold)

    if str(signal_payload.get('candidate_mode') or '').lower() == 'trade_score':
        trade_score_threshold = params.get('trade_score_threshold')
        if trade_score_threshold is not None:
            payload['trade_score_threshold'] = float(trade_score_threshold)
            trade_score = signal_payload.get('trade_score')
            if trade_score is None:
                trade_score = signal_payload.get('combined_trade_score')
            if trade_score is not None:
                payload['passed_trade_score_threshold'] = float(trade_score) >= float(trade_score_threshold)

    return payload


def _close_positions_for_date(run, current_date, open_positions, cash, price_map, fee_config, slippage_bps, closed_pnls, enable_stop_target_exit):
    remaining_positions = []
    for position in open_positions:
        sell_close = price_map.get((position['asset_id'], current_date))
        if not sell_close or sell_close <= 0:
            remaining_positions.append(position)
            continue

        exit_reason = None
        if enable_stop_target_exit:
            stop_loss_price = position.get('stop_loss_price')
            target_price = position.get('target_price')
            if stop_loss_price is not None and sell_close <= stop_loss_price:
                exit_reason = 'STOP_LOSS'
            elif target_price is not None and sell_close >= target_price:
                exit_reason = 'TARGET_PRICE'

        # If the scheduled exit day had no valid close, retry the scheduled sell
        # on the next later tradable date with a usable price.
        if exit_reason is None and current_date < position['exit_date']:
            remaining_positions.append(position)
            continue

        if exit_reason is None:
            exit_reason = 'SCHEDULED'

        slippage_sell = sell_close * slippage_bps / DECIMAL_100 / DECIMAL_100
        sell_price = sell_close - slippage_sell
        sell_amount = position['quantity'] * sell_price
        sell_fee_breakdown = _trade_fee_breakdown(sell_amount, fee_config, is_sell=True)
        sell_fee = sell_fee_breakdown['total_fee']
        pnl = sell_amount - sell_fee - position['buy_amount'] - position['buy_fee']
        cash += sell_amount - sell_fee
        closed_pnls.append(pnl)

        BacktestTrade.objects.create(
            backtest_run=run,
            asset_id=position['asset_id'],
            trade_date=current_date,
            side=BacktestTrade.Side.SELL,
            quantity=position['quantity'],
            price=sell_price,
            fee=sell_fee,
            slippage=slippage_sell,
            amount=sell_amount,
            pnl=pnl,
            signal_payload=position['signal_payload'],
            metadata={
                'exit_reason': exit_reason,
                'fee_model': fee_config['mode'],
                'fee_breakdown': _fee_breakdown_payload(sell_fee_breakdown),
            },
        )

    return cash, remaining_positions


def _open_positions_for_date(run, current_date, exit_date, candidate_rows, cash, initial_capital, capital_fraction, price_map, fee_config, slippage_bps, open_positions, max_positions):
    if not candidate_rows:
        return cash

    available_slots = max(0, int(max_positions) - len(open_positions))
    if available_slots <= 0:
        return cash

    candidate_rows = candidate_rows[:available_slots]

    deployable_capital = min(cash, initial_capital * capital_fraction)
    if deployable_capital <= 0:
        return cash

    allocation_budget = deployable_capital / _d(len(candidate_rows))
    for candidate_rank, row in enumerate(candidate_rows, start=1):
        asset_id = row['asset_id']
        signal_payload = {
            **row['signal_payload'],
            **_selection_audit_payload(run, row, candidate_rank),
            'entry_date': current_date.isoformat(),
            'scheduled_exit_date': exit_date.isoformat(),
        }
        buy_close = price_map.get((asset_id, current_date))
        if not buy_close or buy_close <= 0:
            continue
        signal_payload = _backfill_prediction_trade_decision(asset_id, current_date, signal_payload)

        slippage_buy = buy_close * slippage_bps / DECIMAL_100 / DECIMAL_100
        buy_price = buy_close + slippage_buy
        buy_amount = _max_buy_amount_for_budget(allocation_budget, fee_config)
        if buy_amount <= 0:
            continue
        quantity = buy_amount / buy_price
        if quantity <= 0:
            continue

        buy_amount = quantity * buy_price
        buy_fee_breakdown = _trade_fee_breakdown(buy_amount, fee_config, is_sell=False)
        buy_fee = buy_fee_breakdown['total_fee']
        total_cost = buy_amount + buy_fee
        if total_cost > cash:
            continue

        cash -= total_cost
        open_positions.append({
            'asset_id': asset_id,
            'quantity': quantity,
            'buy_amount': buy_amount,
            'buy_fee': buy_fee,
            'buy_price': buy_price,
            'signal_payload': signal_payload,
            'exit_date': exit_date,
            'target_price': _to_decimal_or_none(signal_payload.get('target_price')),
            'stop_loss_price': _to_decimal_or_none(signal_payload.get('stop_loss_price')),
        })

        BacktestTrade.objects.create(
            backtest_run=run,
            asset_id=asset_id,
            trade_date=current_date,
            side=BacktestTrade.Side.BUY,
            quantity=quantity,
            price=buy_price,
            fee=buy_fee,
            slippage=slippage_buy,
            amount=buy_amount,
            pnl=DECIMAL_0,
            signal_payload=signal_payload,
            metadata={
                'fee_model': fee_config['mode'],
                'fee_breakdown': _fee_breakdown_payload(buy_fee_breakdown),
            },
        )

    return cash


def _portfolio_equity(current_date, cash, open_positions, price_map):
    equity = cash
    for position in open_positions:
        latest_close = price_map.get((position['asset_id'], current_date), position['buy_price'])
        equity += position['quantity'] * latest_close
    return equity


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


def _serialize_open_positions(open_positions):
    rows = []
    for position in open_positions:
        rows.append({
            **position,
            'quantity': str(position['quantity']),
            'buy_amount': str(position['buy_amount']),
            'buy_fee': str(position['buy_fee']),
            'buy_price': str(position['buy_price']),
            'exit_date': position['exit_date'].isoformat(),
            'target_price': str(position['target_price']) if position.get('target_price') is not None else None,
            'stop_loss_price': str(position['stop_loss_price']) if position.get('stop_loss_price') is not None else None,
        })
    return rows


def _deserialize_open_positions(rows):
    positions = []
    for position in rows or []:
        positions.append({
            **position,
            'quantity': _d(position['quantity']),
            'buy_amount': _d(position['buy_amount']),
            'buy_fee': _d(position['buy_fee']),
            'buy_price': _d(position['buy_price']),
            'exit_date': date.fromisoformat(position['exit_date']),
            'target_price': _d(position['target_price']) if position.get('target_price') is not None else None,
            'stop_loss_price': _d(position['stop_loss_price']) if position.get('stop_loss_price') is not None else None,
        })
    return positions


def _load_runtime_state(run):
    raw_state = (run.report or {}).get('runtime_state')
    if not raw_state:
        return None

    return {
        'current_index': int(raw_state.get('current_index', 0)),
        'cash': _d(raw_state.get('cash', run.initial_capital)),
        'equity_curve': [_d(value) for value in raw_state.get('equity_curve', [])],
        'closed_pnls': [_d(value) for value in raw_state.get('closed_pnls', [])],
        'open_positions': _deserialize_open_positions(raw_state.get('open_positions', [])),
        'macro_monthly_report': raw_state.get('macro_monthly_report', {}),
    }


def _save_runtime_state(run, state, total_trading_days):
    report = dict(run.report or {})
    report['runtime_state'] = {
        'current_index': state['current_index'],
        'cash': str(state['cash']),
        'equity_curve': [str(value) for value in state['equity_curve']],
        'closed_pnls': [str(value) for value in state['closed_pnls']],
        'open_positions': _serialize_open_positions(state['open_positions']),
        'macro_monthly_report': state['macro_monthly_report'],
    }
    report['progress'] = {
        'processed_trading_days': state['current_index'],
        'total_trading_days': total_trading_days,
    }
    run.report = report


def _clear_runtime_state(report):
    cleaned_report = dict(report or {})
    cleaned_report.pop('runtime_state', None)
    cleaned_report.pop('progress', None)
    return cleaned_report


@shared_task(soft_time_limit=1800, time_limit=2100)
def run_backtest(backtest_run_id):
    run = BacktestRun.objects.filter(id=backtest_run_id).first()
    if not run:
        return f'Backtest run not found: {backtest_run_id}'

    runtime_state = _load_runtime_state(run)
    if runtime_state is None:
        run.status = BacktestRun.Status.RUNNING
        run.error_message = ''
        run.started_at = timezone.now()
        run.completed_at = None
        run.save(update_fields=['status', 'error_message', 'started_at', 'completed_at', 'updated_at'])
    elif run.status != BacktestRun.Status.RUNNING or run.completed_at is not None:
        run.status = BacktestRun.Status.RUNNING
        run.completed_at = None
        run.save(update_fields=['status', 'completed_at', 'updated_at'])

    try:
        if runtime_state is None:
            with transaction.atomic():
                run.trades.all().delete()

        trading_dates = _get_trading_dates(run.start_date, run.end_date)
        if len(trading_dates) < 2:
            raise ValueError('Not enough OHLCV data in selected date range.')

        price_map = _build_price_map(run.start_date, run.end_date)
        fee_config = _resolve_fee_config(run)
        slippage_bps = _non_negative_decimal('slippage_bps', (run.parameters or {}).get('slippage_bps', '5'))
        entry_weekdays = _entry_weekdays(run)
        holding_period_days = _holding_period_days(run)
        capital_fraction = _capital_fraction_per_entry(run, entry_weekdays)
        max_positions = _max_positions(run)
        candidate_cache = {}
        macro_monthly_report = {}
        stop_target_exit_enabled = _enable_stop_target_exit(run)

        if runtime_state is None:
            current_index = 0
            cash = _d(run.initial_capital)
            equity_curve = []
            closed_pnls = []
            open_positions = []
        else:
            current_index = runtime_state['current_index']
            cash = runtime_state['cash']
            equity_curve = runtime_state['equity_curve']
            closed_pnls = runtime_state['closed_pnls']
            open_positions = runtime_state['open_positions']
            macro_monthly_report = runtime_state['macro_monthly_report']

        chunk_end = min(current_index + BACKTEST_CHUNK_TRADING_DAYS, len(trading_dates))

        for current_date in trading_dates[current_index:chunk_end]:
            cash, open_positions = _close_positions_for_date(
                run,
                current_date,
                open_positions,
                cash,
                price_map,
                fee_config,
                slippage_bps,
                closed_pnls,
                stop_target_exit_enabled,
            )

            if _should_enter_position(current_date, entry_weekdays):
                exit_date = _resolve_exit_date(trading_dates, current_date, holding_period_days)
                if exit_date is not None and exit_date > current_date:
                    candidate_rows = _pick_candidates(run, current_date, candidate_cache)
                    if _use_macro_context(run):
                        macro_info = _resolve_macro_context_for_date(current_date, candidate_cache)
                        month_key = current_date.strftime('%Y-%m')
                        month_state = macro_monthly_report.setdefault(
                            month_key,
                            {
                                'macro_phase': macro_info.get('macro_phase') or '',
                                'event_tag': macro_info.get('event_tag') or '',
                                'entry_days': 0,
                                'selected_assets': 0,
                            },
                        )
                        month_state['entry_days'] += 1
                        month_state['selected_assets'] += len(candidate_rows)

                    cash = _open_positions_for_date(
                        run,
                        current_date,
                        exit_date,
                        candidate_rows,
                        cash,
                        _d(run.initial_capital),
                        capital_fraction,
                        price_map,
                        fee_config,
                        slippage_bps,
                        open_positions,
                        max_positions,
                    )

            equity_curve.append(_portfolio_equity(current_date, cash, open_positions, price_map))

        if chunk_end < len(trading_dates):
            _save_runtime_state(
                run,
                {
                    'current_index': chunk_end,
                    'cash': cash,
                    'equity_curve': equity_curve,
                    'closed_pnls': closed_pnls,
                    'open_positions': open_positions,
                    'macro_monthly_report': macro_monthly_report,
                },
                len(trading_dates),
            )
            run.cash = cash
            run.save(update_fields=['cash', 'report', 'updated_at'])
            run_backtest.delay(run.id)
            return f'Backtest chunk queued for run_id={run.id}: {chunk_end}/{len(trading_dates)} trading days processed'

        final_mark_to_market = equity_curve[-1] if equity_curve else cash
        benchmark_equity_curve, benchmark_metadata = _build_benchmark_equity_curve(trading_dates, price_map, run.initial_capital)

        final_value = final_mark_to_market
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

        benchmark_total_return = (
            (benchmark_equity_curve[-1] - _d(run.initial_capital)) / _d(run.initial_capital)
            if benchmark_equity_curve and run.initial_capital
            else DECIMAL_0
        )

        total_trades = len(closed_pnls)
        winning = len([p for p in closed_pnls if p > 0])
        win_rate = (_d(winning) / _d(total_trades)) if total_trades else DECIMAL_0
        resolved_horizon_days = int((run.parameters or {}).get('horizon_days', 7))
        compare_backtest_run_id = (run.parameters or {}).get('compare_backtest_run_id')
        model_references = _collect_run_model_references(run)

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
        run.report = _clear_runtime_state({
            'equity_curve': [float(v) for v in equity_curve],
            'num_trading_days': len(trading_dates),
            'strategy': run.strategy_type,
            'prediction_source': _prediction_source(run),
            'candidate_mode': _candidate_mode(run),
            'trade_score_scope': _trade_score_scope(run),
            'use_macro_context': _use_macro_context(run),
            'compare_backtest_run_id': compare_backtest_run_id,
            'horizon_days': resolved_horizon_days,
            'entry_weekdays': entry_weekdays,
            'holding_period_days': holding_period_days,
            'enable_stop_target_exit': stop_target_exit_enabled,
            'fee_model': fee_config['mode'],
            'fee_parameters': {
                **fee_config['summary'],
                'slippage_bps': float(slippage_bps),
            },
            'model_reference_count': len(model_references),
            'model_references': model_references,
            'macro_context_monthly': [
                {
                    'month': month,
                    **payload,
                }
                for month, payload in sorted(macro_monthly_report.items())
            ],
            'benchmark': {
                **benchmark_metadata,
                'equity_curve': [float(v) for v in benchmark_equity_curve],
                'total_return': float(benchmark_total_return),
            },
        })
        run.completed_at = timezone.now()
        run.save()
        return f'Backtest completed for run_id={run.id}'

    except Exception as exc:
        run.status = BacktestRun.Status.FAILED
        run.error_message = str(exc)
        run.completed_at = timezone.now()
        run.save(update_fields=['status', 'error_message', 'completed_at', 'updated_at'])
        return f'Backtest failed for run_id={run.id}: {exc}'
