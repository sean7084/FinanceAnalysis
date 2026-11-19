"""Canonical parameter keys for ``TechnicalIndicator``.

The stored ``parameters`` jsonb cannot serve as a uniqueness key: BBANDS writes its
computed ``upper``/``middle``/``lower`` into the same dict as its inputs, so every row is
unique by its own value and re-computation inserts instead of updating. The key below
covers inputs only, which restores idempotency and is narrow enough to index.

The spelling deliberately reproduces the bracket content of the data-quality gate's
``_technical_indicator_variant_name``, so the gate can name a variant from this column
without fetching the jsonb payload.
"""
from __future__ import annotations

import json

#: Computed outputs that share a payload with its inputs. A key containing its own output
#: is unique per row, so it cannot enforce idempotency -- and for FIB_RET the nested
#: ``levels`` dict makes the key longer than the column. Both writers look up by inputs
#: only, so stripping restores the match. The payload itself is never modified: consumers
#: read the bands and the Fibonacci levels out of it.
COMPUTED_OUTPUT_KEYS = {
    'BBANDS': frozenset({'upper', 'middle', 'lower'}),
    'FIB_RET': frozenset({'high', 'low', 'range', 'levels', 'close'}),
}

#: Matches ``TechnicalIndicator.param_key``'s column width. Measured across a 0.2% page
#: sample of every type: the longest canonical key is STOCH at 44 characters.
PARAM_KEY_MAX_LENGTH = 120


def canonical_param_key(indicator_type, parameters):
    """Return ``k=v`` pairs sorted by key name, or ``''`` for an empty parameter set.

    Values are formatted with ``str`` exactly as the gate formats them, so a key derived
    here and a variant name derived there agree character for character.
    """
    if parameters is None:
        return ''
    if not isinstance(parameters, dict):
        # jsonb can legally hold a scalar. Production has none (0 of 343,695 sampled), but
        # a write must not crash on one; keep it distinct using the gate's own spelling.
        return json.dumps(parameters, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    resolved = dict(parameters)
    outputs = COMPUTED_OUTPUT_KEYS.get(indicator_type)
    if outputs:
        resolved = {k: v for k, v in resolved.items() if k not in outputs}
    if not resolved:
        return ''
    return ','.join(f'{key}={resolved[key]}' for key in sorted(resolved))