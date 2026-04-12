from datetime import date

from celery import shared_task
from django.utils import timezone

from .models import MacroSnapshot, MarketContext


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


@shared_task
def sync_macro_data_monthly(payload=None):
    """Monthly macro synchronization entrypoint (payload-friendly for tests/manual backfill)."""
    payload = payload or {}
    snapshot_date = payload.get('date')
    if snapshot_date:
        try:
            d = date.fromisoformat(str(snapshot_date))
        except ValueError:
            d = timezone.now().date()
    else:
        d = timezone.now().date().replace(day=1)

    snapshot, _ = MacroSnapshot.objects.update_or_create(
        date=d,
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

    phase = _infer_phase(snapshot)

    MarketContext.objects.filter(context_key='current', is_active=True).update(is_active=False)

    MarketContext.objects.create(
        context_key='current',
        macro_phase=phase,
        event_tag=event_tag,
        is_active=True,
        starts_at=snapshot.date,
        notes='Auto-updated from latest macro snapshot.',
        metadata={'snapshot_id': snapshot.id},
    )
    return f'Current market context set to {phase}'
