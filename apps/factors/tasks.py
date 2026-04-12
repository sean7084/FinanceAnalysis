from decimal import Decimal
from datetime import date

from celery import shared_task
from django.utils import timezone

from apps.analytics.models import SignalEvent, TechnicalIndicator
from apps.markets.models import Asset, OHLCV
from apps.sentiment.models import SentimentScore
from .models import FundamentalFactorSnapshot, CapitalFlowSnapshot, FactorScore


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


def _technical_reversal_score(asset_id):
    """Build a technical reversal score from existing indicators and phase 10 signals."""
    score = Decimal('0')

    rsi = TechnicalIndicator.objects.filter(asset_id=asset_id, indicator_type='RSI').order_by('-timestamp').first()
    bb = TechnicalIndicator.objects.filter(asset_id=asset_id, indicator_type='BBANDS').order_by('-timestamp').first()
    close = OHLCV.objects.filter(asset_id=asset_id).order_by('-date').first()

    if rsi and Decimal(str(rsi.value)) <= Decimal('35'):
        score += Decimal('0.35')

    if bb and close:
        lower = bb.parameters.get('lower')
        if lower is not None and Decimal(str(close.close)) <= Decimal(str(lower)) * Decimal('1.03'):
            score += Decimal('0.25')

    oversold_signal = SignalEvent.objects.filter(
        asset_id=asset_id,
        signal_type=SignalEvent.SignalType.OVERSOLD_COMBINATION,
    ).exists()
    if oversold_signal:
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
    if target_date:
        try:
            as_of = date.fromisoformat(str(target_date))
        except ValueError:
            as_of = timezone.now().date()
    else:
        as_of = timezone.now().date()

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

    assets = list(Asset.objects.all())

    latest_fundamentals = {}
    latest_flows = {}
    pe_values = []
    pb_values = []
    nb_values = []
    mf_values = []
    mb_values = []

    for asset in assets:
        f = FundamentalFactorSnapshot.objects.filter(asset=asset, date__lte=as_of).order_by('-date').first()
        c = CapitalFlowSnapshot.objects.filter(asset=asset, date__lte=as_of).order_by('-date').first()
        latest_fundamentals[asset.id] = f
        latest_flows[asset.id] = c

        if f and f.pe is not None:
            pe_values.append(Decimal(str(f.pe)))
        if f and f.pb is not None:
            pb_values.append(Decimal(str(f.pb)))
        if c and c.northbound_net_5d is not None:
            nb_values.append(Decimal(str(c.northbound_net_5d)))
        if c and c.main_force_net_5d is not None:
            mf_values.append(Decimal(str(c.main_force_net_5d)))
        if c and c.margin_balance_change_5d is not None:
            mb_values.append(Decimal(str(c.margin_balance_change_5d)))

    created_count = 0
    for asset in assets:
        f = latest_fundamentals.get(asset.id)
        c = latest_flows.get(asset.id)

        # Lower PE/PB is better for "bottom" candidates.
        pe_rank = _percentile_rank(Decimal(str(f.pe)) if f and f.pe is not None else None, pe_values)
        pb_rank = _percentile_rank(Decimal(str(f.pb)) if f and f.pb is not None else None, pb_values)
        pe_score = (Decimal('1') - pe_rank) if pe_rank is not None else None
        pb_score = (Decimal('1') - pb_rank) if pb_rank is not None else None

        roe_trend = None
        if f and f.roe_qoq is not None:
            roe_raw = Decimal(str(f.roe_qoq))
            roe_trend = max(Decimal('0'), min(Decimal('1'), (roe_raw + Decimal('0.2')) / Decimal('0.4')))

        nb_score = _percentile_rank(
            Decimal(str(c.northbound_net_5d)) if c and c.northbound_net_5d is not None else None,
            nb_values,
        )
        mf_score = _percentile_rank(
            Decimal(str(c.main_force_net_5d)) if c and c.main_force_net_5d is not None else None,
            mf_values,
        )
        mb_score = _percentile_rank(
            Decimal(str(c.margin_balance_change_5d)) if c and c.margin_balance_change_5d is not None else None,
            mb_values,
        )

        technical_score = _technical_reversal_score(asset.id)
        sentiment_score = _sentiment_factor_score(asset.id, as_of)
        fundamental_score = _avg_decimal([pe_score, pb_score, roe_trend])
        capital_flow_score = _avg_decimal([nb_score, mf_score, mb_score])

        composite = (
            fundamental_score * fw +
            capital_flow_score * cw +
            technical_score * tw +
            sentiment_score * sw
        )
        bottom_probability = max(Decimal('0'), min(Decimal('1'), composite))

        obj, created = FactorScore.objects.update_or_create(
            asset=asset,
            date=as_of,
            mode=FactorScore.FactorMode.COMPOSITE,
            defaults={
                'pe_percentile_score': pe_score,
                'pb_percentile_score': pb_score,
                'roe_trend_score': roe_trend,
                'northbound_flow_score': nb_score,
                'main_force_flow_score': mf_score,
                'margin_flow_score': mb_score,
                'technical_reversal_score': technical_score,
                'sentiment_score': sentiment_score,
                'fundamental_score': fundamental_score,
                'capital_flow_score': capital_flow_score,
                'technical_score': technical_score,
                'financial_weight': fw,
                'flow_weight': cw,
                'technical_weight': tw,
                'sentiment_weight': sw,
                'composite_score': composite,
                'bottom_probability_score': bottom_probability,
                'metadata': {
                    'target_date': str(as_of),
                    'source': 'phase11_scoring_with_sentiment',
                },
            },
        )
        if created:
            created_count += 1

    return f'Factor scores calculated for {len(assets)} assets on {as_of}. Created: {created_count}'
