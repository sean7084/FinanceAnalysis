from decimal import Decimal

from .models import MarketContext


CONTEXT_WEIGHT_PRESETS = {
    MarketContext.MacroPhase.RECOVERY: {
        'financial': Decimal('0.30'),
        'flow': Decimal('0.40'),
        'technical': Decimal('0.30'),
    },
    MarketContext.MacroPhase.OVERHEAT: {
        'financial': Decimal('0.25'),
        'flow': Decimal('0.30'),
        'technical': Decimal('0.45'),
    },
    MarketContext.MacroPhase.STAGFLATION: {
        'financial': Decimal('0.45'),
        'flow': Decimal('0.20'),
        'technical': Decimal('0.35'),
    },
    MarketContext.MacroPhase.RECESSION: {
        'financial': Decimal('0.50'),
        'flow': Decimal('0.15'),
        'technical': Decimal('0.35'),
    },
}


EVENT_ADJUSTMENTS = {
    'trade_war': {
        'financial': Decimal('1.15'),
        'flow': Decimal('0.85'),
        'technical': Decimal('1.05'),
    },
    'rate_cut_cycle': {
        'financial': Decimal('0.90'),
        'flow': Decimal('1.15'),
        'technical': Decimal('1.00'),
    },
}


def _normalize_weights(financial, flow, technical):
    total = financial + flow + technical
    if total <= 0:
        return Decimal('0.4'), Decimal('0.3'), Decimal('0.3')
    return financial / total, flow / total, technical / total


def resolve_context(macro_context=None, event_tag=None):
    """Resolve context from explicit params or active current context."""
    if macro_context or event_tag:
        return macro_context, event_tag

    active = MarketContext.objects.filter(context_key='current', is_active=True).order_by('-starts_at', '-updated_at').first()
    if not active:
        return None, None
    return active.macro_phase, active.event_tag or None


def apply_macro_context_to_weights(financial_weight, flow_weight, technical_weight, macro_context=None, event_tag=None):
    """Return normalized context-adjusted weights."""
    fw = Decimal(str(financial_weight))
    cw = Decimal(str(flow_weight))
    tw = Decimal(str(technical_weight))

    ctx, evt = resolve_context(macro_context, event_tag)

    if ctx in CONTEXT_WEIGHT_PRESETS:
        preset = CONTEXT_WEIGHT_PRESETS[ctx]
        fw, cw, tw = preset['financial'], preset['flow'], preset['technical']

    if evt:
        key = evt.strip().lower()
        if key in EVENT_ADJUSTMENTS:
            adj = EVENT_ADJUSTMENTS[key]
            fw *= adj['financial']
            cw *= adj['flow']
            tw *= adj['technical']

    fw, cw, tw = _normalize_weights(fw, cw, tw)
    return {
        'macro_context': ctx,
        'event_tag': evt,
        'financial_weight': fw,
        'flow_weight': cw,
        'technical_weight': tw,
    }
