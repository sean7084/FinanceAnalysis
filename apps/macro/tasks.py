from datetime import date, timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import MacroSnapshot, MarketContext
from .providers import fetch_macro_snapshot_with_fallback


def _infer_phase(snapshot):
    pmi = snapshot.pmi_manufacturing
    pmi_non = snapshot.pmi_non_manufacturing
    y10 = snapshot.cn10y_yield
    y2 = snapshot.cn2y_yield
    cpi = snapshot.cpi_yoy

    pmi_val = float(pmi) if pmi is not None else None
    pmi_non_val = float(pmi_non) if pmi_non is not None else None
    y10_val = float(y10) if y10 is not None else None
    y2_val = float(y2) if y2 is not None else None
    cpi_val = float(cpi) if cpi is not None else None

    slope = None
    if y10_val is not None and y2_val is not None:
        slope = y10_val - y2_val

    if pmi_val is not None and pmi_val < 50 and slope is not None and slope < 0:
        return MarketContext.MacroPhase.RECESSION
    if pmi_val is not None and pmi_val < 50 and cpi_val is not None and cpi_val > 2.5:
        return MarketContext.MacroPhase.STAGFLATION
    if pmi_val is not None and pmi_val >= 52 and pmi_non_val is not None and pmi_non_val >= 53:
        return MarketContext.MacroPhase.OVERHEAT
    return MarketContext.MacroPhase.RECOVERY


def sync_market_context_for_snapshot(
    snapshot,
    event_tag='',
    *,
    note='Auto-updated from latest macro snapshot.',
    metadata_source='latest_macro_snapshot',
):
    phase = _infer_phase(snapshot)
    existing_contexts = list(
        MarketContext.objects.filter(context_key='current', starts_at=snapshot.date)
        .order_by('-is_active', '-updated_at', '-id')
    )
    context = existing_contexts[0] if existing_contexts else MarketContext(context_key='current', starts_at=snapshot.date)
    created = not existing_contexts

    next_context = (
        MarketContext.objects.filter(context_key='current', is_active=True, starts_at__gt=snapshot.date)
        .order_by('starts_at', 'updated_at', 'id')
        .first()
    )
    ends_at = next_context.starts_at - timedelta(days=1) if next_context else None

    metadata = dict(context.metadata or {})
    metadata.update({
        'snapshot_id': snapshot.id,
        'source': metadata_source,
    })

    context.macro_phase = phase
    if event_tag or not context.event_tag:
        context.event_tag = event_tag
    context.is_active = True
    context.ends_at = ends_at
    context.notes = note
    context.metadata = metadata
    context.save()

    if len(existing_contexts) > 1:
        duplicate_ids = [row.id for row in existing_contexts if row.id != context.id]
        MarketContext.objects.filter(id__in=duplicate_ids).update(is_active=False, ends_at=ends_at)

    previous_context = (
        MarketContext.objects.filter(context_key='current', is_active=True, starts_at__lt=snapshot.date)
        .order_by('-starts_at', '-updated_at', '-id')
        .first()
    )
    previous_end = snapshot.date - timedelta(days=1)
    if previous_context is not None and previous_context.ends_at != previous_end:
        previous_context.ends_at = previous_end
        previous_context.save(update_fields=['ends_at', 'updated_at'])

    return context, created


@shared_task
def sync_macro_data_monthly(payload=None):
    """Macro synchronization entrypoint using TuShare primary and AkShare fallback."""
    payload = payload or {}
    snapshot_date = payload.get('date')
    if snapshot_date:
        try:
            d = date.fromisoformat(str(snapshot_date))
        except ValueError:
            d = timezone.now().date()
    else:
        d = timezone.now().date().replace(day=1)

    if not payload:
        payload = fetch_macro_snapshot_with_fallback(
            snapshot_date=d,
            primary=getattr(settings, 'MACRO_SYNC_PRIMARY_PROVIDER', 'tushare'),
            fallback=getattr(settings, 'MACRO_SYNC_FALLBACK_PROVIDER', 'akshare'),
        )

    snapshot, _ = MacroSnapshot.objects.update_or_create(
        date=payload.get('date') or d,
        defaults={
            'dxy': payload.get('dxy'),
            'cny_usd': payload.get('cny_usd'),
            'cn10y_yield': payload.get('cn10y_yield'),
            'cn2y_yield': payload.get('cn2y_yield'),
            'pmi_manufacturing': payload.get('pmi_manufacturing'),
            'pmi_non_manufacturing': payload.get('pmi_non_manufacturing'),
            'cpi_yoy': payload.get('cpi_yoy'),
            'ppi_yoy': payload.get('ppi_yoy'),
            'metadata': payload.get('metadata', {}),
        },
    )

    refresh_current_market_context.delay(snapshot.id)
    return f'Macro snapshot synced for {d}'


@shared_task
def refresh_current_market_context(snapshot_id=None, event_tag=''):
    """Infer and update current market context using latest macro snapshot."""
    if snapshot_id:
        snapshot = MacroSnapshot.objects.filter(id=snapshot_id).first()
    else:
        snapshot = MacroSnapshot.objects.order_by('-date').first()
    if not snapshot:
        return 'No macro snapshot available.'

    context, _created = sync_market_context_for_snapshot(
        snapshot,
        event_tag=event_tag,
        note='Auto-updated from latest macro snapshot.',
        metadata_source='latest_macro_snapshot',
    )
    return f'Current market context set to {context.macro_phase}'
