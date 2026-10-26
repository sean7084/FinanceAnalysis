"""Append-only measurement ledger for ETF rotation research.

The 2016-2026 panel is a finite resource: every study re-reads it, so "this arm passed" has a
higher prior of being luck each time the same context is measured again. This module makes
that visible. It records what was measured (panel, fold plan, purge, embargo, arm, model,
feature schema, label horizon) and what came out, one JSON line per run, and it never
overwrites: a later correction is a new line, not an edit.

Storage is a JSONL file rather than a table on purpose. The record has to survive the code
that produced it (a run from before a schema change still reads back), it must not need a
migration to add a field, and it is a research artefact that belongs next to the reports that
quote it. ``ETF_EVIDENCE_PATH`` overrides the location so tests never write into docs/.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

from django.conf import settings

DEFAULT_EVIDENCE_RELATIVE_PATH = 'docs/reports/evidence/etf-rotation-measurements.jsonl'


def evidence_path() -> str:
    configured = str(getattr(settings, 'ETF_EVIDENCE_PATH', '') or '')
    if configured:
        return configured
    return os.path.join(str(settings.BASE_DIR), DEFAULT_EVIDENCE_RELATIVE_PATH)


def measurement_id(context: dict, created_at: str) -> str:
    """Short stable id: same context recorded twice at different times gets different ids."""
    payload = json.dumps(context, sort_keys=True, default=str) + created_at
    return hashlib.sha1(payload.encode('utf-8')).hexdigest()[:12]


def preregistration_digest(text: str) -> str:
    """The 12-hex sha256 of a pre-registration file's text.

    One helper on purpose: the sensitivity grid names its report after this digest and the
    ledger stores it, so a reader can tie a verdict back to the criteria that were declared
    before the run. Two implementations would drift and the tie would be decorative.
    """
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]


def preregistration_file_digest(path) -> str:
    """Digest of a pre-registration file, or ``''`` when no path was given."""
    if not path:
        return ''
    with open(path, encoding='utf-8') as handle:
        return preregistration_digest(handle.read())


def record_measurement(*, context: dict, results: dict, verdict: str = '',
                       preregistration: str = '') -> dict:
    """Append one measurement and return the record (including its id).

    ``preregistration`` is the hash of the pre-registration file when the run had one; an
    unregistered run is still recorded, with an empty value, because hiding it would defeat
    the purpose of the ledger.
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
    path = evidence_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, sort_keys=True, default=str) + '\n')
    return record


def read_measurements() -> list:
    """Every recorded measurement, oldest first. A missing file is an empty ledger."""
    path = evidence_path()
    if not os.path.exists(path):
        return []
    records = []
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def context_repeats(records, context: dict, *, execution_defaults: dict | None = None) -> int:
    """How many earlier runs used exactly this context (0 for the first).

    ``results``/``created_at`` are excluded by construction: only the context identifies a
    measurement, which is what makes a repeat visible.

    ``execution_defaults`` covers the rows written before the execution model joined the
    context. Those records have no ``execution`` key at all, and every one of them ran the
    engine's defaults, so filling the absent key with the caller-resolved defaults keeps an
    old row comparable with a new default-configured run instead of silently restarting the
    repeat count. A run that changed any knob is a different context either way.
    """
    key = json.dumps(normalized_context(context, execution_defaults), sort_keys=True, default=str)
    return sum(
        1 for record in records
        if json.dumps(
            normalized_context(record.get('context') or {}, execution_defaults),
            sort_keys=True, default=str,
        ) == key
    )


def normalized_context(context: dict, execution_defaults: dict | None) -> dict:
    """A context with its execution model filled in, so records from both eras compare equal."""
    if not execution_defaults:
        return context
    execution = dict(execution_defaults)
    execution.update(context.get('execution') or {})
    return {**context, 'execution': execution}
