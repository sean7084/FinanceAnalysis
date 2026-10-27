"""One TuShare client seam, shared by every app that calls the provider.

Before this module ``apps.etf_rotation.tasks`` and ``apps.markets.tasks`` each grew their own
client and retry helper; a third copy was about to appear in the entitlement probe. The
behaviour is the old one: retry only on TuShare's rate-limit message, surface everything else.
"""
from __future__ import annotations

import time

from django.conf import settings


def pro(token: str | None = None):
    """A TuShare pro client, or a ``ValueError`` naming the missing setting."""
    import tushare as ts

    resolved = token or getattr(settings, 'TUSHARE_TOKEN', None)
    if not resolved:
        raise ValueError('TUSHARE_TOKEN is not configured.')
    return ts.pro_api(resolved)


def call_with_retry(fn, label: str, *, max_retries: int | None = None, sleep_seconds: float = 0.0):
    """Call ``fn``, retrying only when TuShare reports a rate limit."""
    attempts = 0
    limit = int(max_retries if max_retries is not None else getattr(settings, 'ETF_SYNC_MAX_RETRIES', 5))
    while True:
        try:
            result = fn()
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
            return result
        except Exception as exc:
            attempts += 1
            if '频率超限' not in str(exc) or attempts >= limit:
                raise
            time.sleep(max(1.0, sleep_seconds))
