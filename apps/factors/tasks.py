from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import StringIO

from celery import shared_task
import pandas as pd
import talib
from django.conf import settings
from django.core.management import call_command
from django.utils import timezone

from apps.analytics.models import SignalEvent
from apps.markets.benchmarking import ensure_pit_membership_coverage, point_in_time_union_asset_ids
from apps.markets.models import Asset, OHLCV
from apps.prediction.historical_features import latest_bbands, latest_ohlcv, latest_rsi
from apps.sentiment.models import SentimentScore
from .models import FundamentalFactorSnapshot, CapitalFlowSnapshot, FactorScore


RSI_TIMEPERIOD = 14
BBANDS_TIMEPERIOD = 20
RSI_LOOKBACK_ROWS = max(RSI_TIMEPERIOD * 5, RSI_TIMEPERIOD + 5)
BBANDS_LOOKBACK_ROWS = max(BBANDS_TIMEPERIOD * 4, BBANDS_TIMEPERIOD + 5)
VOLUME_LOOKBACK_ROWS = 21
TECHNICAL_LOOKBACK_ROWS = max(RSI_LOOKBACK_ROWS, BBANDS_LOOKBACK_ROWS, VOLUME_LOOKBACK_ROWS)
FACTOR_SCORE_BATCH_SIZE = 1000


def _resolve_target_date(target_date=None):
    if target_date:
        try:
            return date.fromisoformat(str(target_date))
        except ValueError:
            return timezone.now().date()
    return timezone.now().date()


def _percentile_rank(value, values):
    """Simple percentile rank in [0, 1]."""
    if value is None or not values:
        return None
    sorted_values = sorted(values)
    below_or_equal = sum(1 for item in sorted_values if item <= value)
    return Decimal(str(round(below_or_equal / len(sorted_values), 6)))


def _avg_decimal(values, default=Decimal('0.5')):
    valid = [Decimal(str(v)) for v in values if v is not None]
    if not valid:
        return default
    return sum(valid) / Decimal(len(valid))


def _build_percentile_ranker(values):
    normalized_values = sorted(Decimal(str(value)) for value in values if value is not None)
    if not normalized_values:
        return lambda value: None

    total = len(normalized_values)

    def _rank(value):
        if value is None:
            return None
        normalized = Decimal(str(value))
        return Decimal(str(round(bisect_right(normalized_values, normalized) / total, 6)))

    return _rank


def _latest_rows_by_asset(model, asset_ids, as_of, extra_filters=None):
    if not asset_ids:
        return {}

    extra_filters = dict(extra_filters or {})
    rows = model.objects.filter(
        asset_id__in=asset_ids,
        date__lte=as_of,
        **extra_filters,
    ).order_by('asset_id', '-date', '-pk').distinct('asset_id')
    return {row.asset_id: row for row in rows}


def _sentiment_scores_by_asset(asset_ids, as_of):
    latest_scores = _latest_rows_by_asset(
        SentimentScore,
        asset_ids,
        as_of,
        extra_filters={'score_type': SentimentScore.ScoreType.ASSET_7D},
    )

    sentiment_scores = {}
    for asset_id, latest in latest_scores.items():
        raw = Decimal(str(latest.sentiment_score))
        mapped = (raw + Decimal('1')) / Decimal('2')
        sentiment_scores[asset_id] = max(Decimal('0'), min(Decimal('1'), mapped))
    return sentiment_scores


def _recent_ohlcv_rows_by_asset(asset_ids, as_of, lookback_rows=TECHNICAL_LOOKBACK_ROWS):
    if not asset_ids:
        return {}

    lookback_start = as_of - timedelta(days=max(lookback_rows * 3, 120))
    rows = (
        OHLCV.objects.filter(
            asset_id__in=asset_ids,
            date__gte=lookback_start,
            date__lte=as_of,
        )
        .values('asset_id', 'date', 'close', 'volume')
        .order_by('asset_id', 'date')
    )

    rows_by_asset = defaultdict(list)
    for row in rows:
        rows_by_asset[row['asset_id']].append(row)
    for asset_id in list(rows_by_asset.keys()):
        rows_by_asset[asset_id] = rows_by_asset[asset_id][-lookback_rows:]
    return rows_by_asset


def _technical_reversal_scores_by_asset(asset_ids, as_of):
    if not asset_ids:
        return {}

    next_day = timezone.make_aware(datetime.combine(as_of + timedelta(days=1), datetime.min.time()))
    oversold_asset_ids = set(
        SignalEvent.objects.filter(
            asset_id__in=asset_ids,
            signal_type=SignalEvent.SignalType.OVERSOLD_COMBINATION,
            timestamp__lt=next_day,
        ).values_list('asset_id', flat=True).distinct()
    )
    recent_rows_by_asset = _recent_ohlcv_rows_by_asset(asset_ids, as_of)

    technical_scores = {}
    for asset_id in asset_ids:
        rows = recent_rows_by_asset.get(asset_id, [])
        if not rows:
            technical_scores[asset_id] = Decimal('0')
            continue

        closes = pd.Series([float(row['close']) for row in rows], dtype='float64')
        current_close = Decimal(str(rows[-1]['close']))

        rsi_value = Decimal('50')
        rsi_window = closes.iloc[-RSI_LOOKBACK_ROWS:]
        if len(rsi_window) >= RSI_TIMEPERIOD:
            rsi_values = pd.Series(talib.RSI(rsi_window, timeperiod=RSI_TIMEPERIOD)).dropna()
            if not rsi_values.empty:
                rsi_value = Decimal(str(rsi_values.iloc[-1]))

        lower_band = None
        bbands_window = closes.iloc[-BBANDS_LOOKBACK_ROWS:]
        if len(bbands_window) >= BBANDS_TIMEPERIOD:
            _, _, lower_values = talib.BBANDS(
                bbands_window,
                timeperiod=BBANDS_TIMEPERIOD,
                nbdevup=2,
                nbdevdn=2,
            )
            lower_values = pd.Series(lower_values).dropna()
            if not lower_values.empty:
                lower_band = Decimal(str(lower_values.iloc[-1]))

        volume_confirmation = False
        if len(rows) >= VOLUME_LOOKBACK_ROWS:
            recent_volumes = [Decimal(str(row['volume'])) for row in rows[-VOLUME_LOOKBACK_ROWS:]]
            latest_volume = recent_volumes[-1]
            average_volume = sum(recent_volumes[:-1]) / Decimal(str(VOLUME_LOOKBACK_ROWS - 1))
            volume_confirmation = average_volume > 0 and latest_volume < average_volume * Decimal('0.8')

        score = Decimal('0')
        if rsi_value <= Decimal('35'):
            score += Decimal('0.35')

        if lower_band is not None and current_close <= lower_band * Decimal('1.03'):
            score += Decimal('0.25')

        if asset_id in oversold_asset_ids or (
            lower_band is not None and
            rsi_value < Decimal('30') and
            current_close <= lower_band * Decimal('1.02') and
            volume_confirmation
        ):
            score += Decimal('0.40')

        technical_scores[asset_id] = min(score, Decimal('1'))

    return technical_scores


def _technical_reversal_score(asset_id, as_of):
    """Build a technical reversal score from existing indicators and phase 10 signals."""
    score = Decimal('0')

    rsi_value = latest_rsi(asset_id, as_of, default=Decimal('50'))
    bbands = latest_bbands(asset_id, as_of)
    latest_bar = latest_ohlcv(asset_id, as_of)

    if rsi_value <= Decimal('35'):
        score += Decimal('0.35')

    current_close = Decimal(str(latest_bar.close)) if latest_bar else None
    lower_band = bbands.get('lower') if bbands else None

    if lower_band is not None and current_close is not None and current_close <= lower_band * Decimal('1.03'):
            score += Decimal('0.25')

    oversold_signal = SignalEvent.objects.filter(
        asset_id=asset_id,
        signal_type=SignalEvent.SignalType.OVERSOLD_COMBINATION,
        timestamp__date__lte=as_of,
    ).exists()

    recent_rows = list(
        OHLCV.objects.filter(asset_id=asset_id, date__lte=as_of)
        .order_by('-date')
        .values_list('volume', flat=True)[:21]
    )
    volume_confirmation = False
    if len(recent_rows) >= 21:
        latest_volume = Decimal(str(recent_rows[0]))
        average_volume = sum(Decimal(str(volume)) for volume in recent_rows[1:21]) / Decimal('20')
        volume_confirmation = average_volume > 0 and latest_volume < average_volume * Decimal('0.8')

    if oversold_signal or (
        current_close is not None and
        lower_band is not None and
        rsi_value < Decimal('30') and
        current_close <= lower_band * Decimal('1.02') and
        volume_confirmation
    ):
        score += Decimal('0.40')

    return min(score, Decimal('1'))


def _sentiment_factor_score(asset_id, as_of):
    latest = SentimentScore.objects.filter(
        asset_id=asset_id,
        date__lte=as_of,
        score_type=SentimentScore.ScoreType.ASSET_7D,
    ).order_by('-date').first()
    if latest is None:
        return Decimal('0.5')

    raw = Decimal(str(latest.sentiment_score))
    # Map [-1, 1] -> [0, 1]
    mapped = (raw + Decimal('1')) / Decimal('2')
    return max(Decimal('0'), min(Decimal('1'), mapped))


@shared_task
def sync_daily_capital_flow_snapshots(target_date=None, lookback_days=None):
    """Refresh recent capital-flow data daily using the same backfill command path."""
    as_of = _resolve_target_date(target_date)
    configured_lookback = lookback_days
    if configured_lookback is None:
        configured_lookback = getattr(settings, 'CAPITAL_FLOW_DAILY_SYNC_LOOKBACK_DAYS', 20)
    try:
        configured_lookback = int(configured_lookback)
    except (TypeError, ValueError):
        configured_lookback = 20
    configured_lookback = max(configured_lookback, 10)

    start_date = as_of - timedelta(days=configured_lookback)
    output = StringIO()
    call_command(
        'backfill_capital_flow_snapshots',
        start_date=start_date.isoformat(),
        end_date=as_of.isoformat(),
        stdout=output,
    )
    summary = output.getvalue().strip()
    if summary:
        return summary
    return f'Capital flow sync completed for {start_date}..{as_of}'


@shared_task
def calculate_factor_scores_for_date(
    target_date=None,
    financial_weight=0.4,
    flow_weight=0.3,
    technical_weight=0.3,
    sentiment_weight=0.0,
):
    """
    Calculate daily multi-factor scores and bottom candidate probabilities.
    """
    as_of = _resolve_target_date(target_date)

    # Normalize weights
    fw = Decimal(str(financial_weight))
    cw = Decimal(str(flow_weight))
    tw = Decimal(str(technical_weight))
    sw = Decimal(str(sentiment_weight))
    total = fw + cw + tw + sw
    if total <= 0:
        fw, cw, tw, sw = Decimal('0.4'), Decimal('0.3'), Decimal('0.3'), Decimal('0.0')
        total = fw + cw + tw + sw
    fw /= total
    cw /= total
    tw /= total
    sw /= total

    ensure_pit_membership_coverage([as_of], context=f'Daily factor score refresh for {as_of}')

    union_asset_ids = point_in_time_union_asset_ids(as_of)
    assets = list(Asset.objects.filter(id__in=union_asset_ids).order_by('id'))
    asset_ids = [asset.id for asset in assets]

    latest_fundamentals = _latest_rows_by_asset(FundamentalFactorSnapshot, asset_ids, as_of)
    latest_flows = _latest_rows_by_asset(CapitalFlowSnapshot, asset_ids, as_of)
    sentiment_scores = _sentiment_scores_by_asset(asset_ids, as_of)
    technical_scores = _technical_reversal_scores_by_asset(asset_ids, as_of)

    pe_rank = _build_percentile_ranker(
        Decimal(str(row.pe))
        for row in latest_fundamentals.values()
        if row.pe is not None
    )
    pe_ttm_rank = _build_percentile_ranker(
        Decimal(str(row.pe_ttm))
        for row in latest_fundamentals.values()
        if row.pe_ttm is not None
    )
    pb_rank = _build_percentile_ranker(
        Decimal(str(row.pb))
        for row in latest_fundamentals.values()
        if row.pb is not None
    )
    mf_rank = _build_percentile_ranker(
        Decimal(str(row.main_force_net_5d))
        for row in latest_flows.values()
        if row.main_force_net_5d is not None
    )
    mb_rank = _build_percentile_ranker(
        Decimal(str(row.margin_balance_change_5d))
        for row in latest_flows.values()
        if row.margin_balance_change_5d is not None
    )

    existing_asset_ids = set(
        FactorScore.objects.filter(
            asset_id__in=asset_ids,
            date=as_of,
            mode=FactorScore.FactorMode.COMPOSITE,
        ).values_list('asset_id', flat=True)
    )
    created_count = len(asset_ids) - len(existing_asset_ids)
    now = timezone.now()
    pending_scores = []
    for asset in assets:
        f = latest_fundamentals.get(asset.id)
        c = latest_flows.get(asset.id)

        # Lower PE TTM/PB is better for "bottom" candidates.
        pe_score = None
        pe_ttm_score = None
        pb_score = None
        if f and f.pe is not None:
            current_pe_rank = pe_rank(Decimal(str(f.pe)))
            pe_score = (Decimal('1') - current_pe_rank) if current_pe_rank is not None else None
        if f and f.pe_ttm is not None:
            current_pe_ttm_rank = pe_ttm_rank(Decimal(str(f.pe_ttm)))
            pe_ttm_score = (Decimal('1') - current_pe_ttm_rank) if current_pe_ttm_rank is not None else None
        if f and f.pb is not None:
            current_pb_rank = pb_rank(Decimal(str(f.pb)))
            pb_score = (Decimal('1') - current_pb_rank) if current_pb_rank is not None else None

        roe_trend = None
        if f and f.roe_qoq is not None:
            roe_raw = Decimal(str(f.roe_qoq))
            roe_trend = max(Decimal('0'), min(Decimal('1'), (roe_raw + Decimal('0.2')) / Decimal('0.4')))

        mf_score = None
        mb_score = None
        if c and c.main_force_net_5d is not None:
            mf_score = mf_rank(Decimal(str(c.main_force_net_5d)))
        if c and c.margin_balance_change_5d is not None:
            mb_score = mb_rank(Decimal(str(c.margin_balance_change_5d)))

        technical_score = technical_scores.get(asset.id, Decimal('0'))
        sentiment_score = sentiment_scores.get(asset.id, Decimal('0.5'))
        fundamental_score = _avg_decimal([pe_ttm_score, pb_score, roe_trend])
        capital_flow_score = _avg_decimal([mf_score, mb_score])

        composite = (
            fundamental_score * fw +
            capital_flow_score * cw +
            technical_score * tw +
            sentiment_score * sw
        )
        bottom_probability = max(Decimal('0'), min(Decimal('1'), composite))

        pending_scores.append(
            FactorScore(
                asset=asset,
                date=as_of,
                mode=FactorScore.FactorMode.COMPOSITE,
                pe_percentile_score=pe_score,
                pe_ttm_percentile_score=pe_ttm_score,
                pb_percentile_score=pb_score,
                roe_trend_score=roe_trend,
                main_force_flow_score=mf_score,
                margin_flow_score=mb_score,
                technical_reversal_score=technical_score,
                sentiment_score=sentiment_score,
                fundamental_score=fundamental_score,
                capital_flow_score=capital_flow_score,
                technical_score=technical_score,
                financial_weight=fw,
                flow_weight=cw,
                technical_weight=tw,
                sentiment_weight=sw,
                composite_score=composite,
                bottom_probability_score=bottom_probability,
                metadata={
                    'target_date': str(as_of),
                    'source': 'phase11_scoring_with_sentiment',
                },
                created_at=now,
                updated_at=now,
            )
        )

    if pending_scores:
        FactorScore.objects.bulk_create(
            pending_scores,
            batch_size=FACTOR_SCORE_BATCH_SIZE,
            update_conflicts=True,
            unique_fields=['asset', 'date', 'mode'],
            update_fields=[
                'pe_percentile_score',
                'pe_ttm_percentile_score',
                'pb_percentile_score',
                'roe_trend_score',
                'main_force_flow_score',
                'margin_flow_score',
                'technical_reversal_score',
                'sentiment_score',
                'fundamental_score',
                'capital_flow_score',
                'technical_score',
                'financial_weight',
                'flow_weight',
                'technical_weight',
                'sentiment_weight',
                'composite_score',
                'bottom_probability_score',
                'metadata',
                'updated_at',
            ],
        )

    return f'Factor scores calculated for {len(assets)} assets on {as_of}. Created: {created_count}'
