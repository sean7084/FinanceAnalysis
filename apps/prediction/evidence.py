"""Append-only measurement ledger for the stock universe campaign (H2 / plan D9).

Reuses the ETF rotation preregistration digest and measurement-id helpers (single
implementation, so a stock verdict ties back to the same hashing rule) with a
separate stock ledger path. The ledger is a JSONL file rather than a table on
purpose: the record must survive the code that produced it, needs no migration to
add a field, and belongs next to the reports that quote it. STOCK_EVIDENCE_PATH
overrides the location so tests never write into docs/.
"""
import json
import os
from datetime import datetime, timezone

from django.conf import settings

from apps.etf_rotation.evidence import measurement_id, preregistration_digest  # noqa: F401 (re-exported)

DEFAULT_STOCK_EVIDENCE_RELATIVE_PATH = 'docs/reports/evidence/stock-model-measurements.jsonl'


def stock_evidence_path() -> str:
    configured = str(getattr(settings, 'STOCK_EVIDENCE_PATH', '') or '')
    if configured:
        return configured
    return os.path.join(str(settings.BASE_DIR), DEFAULT_STOCK_EVIDENCE_RELATIVE_PATH)


def record_stock_measurement(*, context: dict, results: dict, verdict: str = '',
                             preregistration: str = '') -> dict:
    """Append one stock measurement and return the record (including its id).

    ``preregistration`` is the prereg file digest when the run had one; an
    unregistered run is still recorded with an empty value, because hiding it
    would defeat the ledger's purpose.
    """
    created_at = datetime.now(timezone.utc).isoformat()
    record = {
        'id': measurement_id(context, created_at),
        'created_at': created_at,
        'context': context,
        'results': results,
        'verdict': str(verdict or ''),
        'preregistration': str(preregistration or ''),
    }
    path = stock_evidence_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, sort_keys=True, default=str) + '\n')
    return record


def read_stock_measurements() -> list:
    """Every recorded stock measurement, oldest first. A missing file is empty."""
    path = stock_evidence_path()
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def stock_context_repeats(records, context: dict) -> int:
    """How many earlier rows used exactly this context (panel-spending visibility)."""
    key = json.dumps(context, sort_keys=True, default=str)
    return sum(
        1 for record in records
        if json.dumps(record.get('context') or {}, sort_keys=True, default=str) == key
    )