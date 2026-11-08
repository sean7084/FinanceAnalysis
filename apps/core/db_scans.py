"""Bounded bulk scans for the data-quality gate.

Two failure modes shape this module, both measured rather than assumed.

**Not ``.iterator(chunk_size=...)``.** A chunked iterator makes Django open a named
server-side cursor, and PostgreSQL will not use a parallel plan through one. Measured
2026-11-05 on the LightGBM trainer (``tmp/retrain/iterator-ab.log``, recorded in
``apps/prediction/tasks_lightgbm.py:_indicator_frame_from_queryset``): the identical
5,703,455-row RSI query returned in **86.1 s** as a plain bulk fetch and had **still not
finished after 432 s** through the cursor. In ``pg_stat_activity`` a parallel plan shows
several backends sharing one ``query_start``; a cursor shows a single backend doing
``FETCH FORWARD`` forever. ``validate_data_quality`` is the release gate, and a gate that
takes an hour gets skipped.

**Not one unbounded ``list()`` either.** ``apps/prediction/tasks_lightgbm.py:_query_frame``
records 22.9 GB peak RSS from holding twelve ``list(qs.values())`` frames at once. The
OHLCV presence scan over a 16-year window is ~20M tuples, which is 1.6-2 GB in one frame.
So the scan is cut into date windows and each window's list is released before the next
is fetched: peak RSS is one window, not one history.

Aggregating in SQL is *not* an option for the coverage bitmaps. They need per-(date,
asset) identity -- the mask machinery compares each feature's non-null mask against the
membership mask and the reports name which assets are missing -- and the per-date
effective-universe restriction lives in a Python dict, not a joinable table. A
``GROUP BY date`` would destroy exactly the information the report exists to carry.

Measured 2026-11-08 on the live 179,093,920-row ``analytics_technicalindicator``, via
``EXPLAIN`` (planning only -- ``ANALYZE`` on this shape is the operation that wedged the
link, so it was deliberately not run). Narrowing the coverage scan's filter from
``indicator_type__in=<19 defaults>`` to ``indicator_type='RS_SCORE'``, which is the only
type that loop ever consumed:

===================================  ==================  ===========  ========
plan                                 estimated rows      cost         width
===================================  ==================  ===========  ========
Seq Scan (old filter)                117,248,783         8,771,287    19
Bitmap Index Scan (RS_SCORE only)      1,116,352         2,639,966    12
===================================  ==================  ===========  ========

105x fewer rows and an index instead of a sequential scan. This isolates the *filter*
half of the win; the cursor-versus-bulk half rests on the 86.1 s / >432 s A/B above,
because ``EXPLAIN`` of a Django queryset renders a plain SELECT and so does not
reproduce the named cursor that ``.iterator()`` opens at execution time.

End-to-end, measured as a real A/B on the same database and the same window
(``2026-05-01 .. 2026-11-06``, ``--effective-universe-only``), old code from a worktree
at ``main`` against new code here: **58 s -> 53 s**, with **byte-identical output**
(``critical_issues=1530``, ``detail_rows_written=68769``, ``detail_rows_dropped=0``).

That is a ~9% win, not an order of magnitude, and the reason matters: on a six-month
window the coverage scans are not the dominant cost -- the per-asset continuity and
cross-table validators are, and this module does not touch them.

**The full-range run settles it, and refutes the expectation.** Measured 2026-11-08 with
the whole rewrite in place, ``--effective-universe-only`` over the entire history: the
command was killed at the **90-minute** timeout without finishing
(``VALIDATE_RC=124``, ``WALL_SECONDS=5401``). Sampling ``pg_stat_activity`` through the
run showed it spending the bulk of that time in the **per-asset** OHLCV continuity phase,
not in the coverage scans. So the earlier assumption -- that the 16-year run was dominated
by the seq-scanned coverage loop, and that narrowing it would take the gate to single-digit
minutes -- was wrong. It is dominated by validating 1,886 assets across ~4,000 sessions
one asset at a time.

What the rewrite did buy, and this part is real: the largest single scan now reads 105x
fewer rows through a bitmap index instead of a sequential scan, peak memory is one quarter
rather than one history, and the six-month A/B is byte-identical and slightly faster. What
it did not buy is a usable full-range gate.

Making the gate usable needs the per-asset phase addressed -- narrowing it to sessions
where the asset is actually in the effective universe, or parallelising across assets --
which is separate work with its own verification. **Do not quote a full-range speedup:
there is not one yet.**
"""
from __future__ import annotations

from datetime import timedelta

#: Roughly a quarter. Small enough that one window of the widest scan stays in the tens
#: of MB, large enough that a 16-year range is ~65 queries rather than thousands.
DEFAULT_WINDOW_DAYS = 90


def date_windows(start_date, end_date, window_days=DEFAULT_WINDOW_DAYS):
    """Inclusive ``(start, end)`` pairs covering ``[start_date, end_date]`` exactly once.

    Windows are half-open in effect only because the next one starts at ``end + 1 day``;
    both bounds are inclusive so a caller can pass them straight to ``__gte``/``__lte``.
    """
    if window_days < 1:
        raise ValueError('window_days must be at least 1')
    if start_date > end_date:
        return
    window_start = start_date
    step = timedelta(days=window_days)
    while window_start <= end_date:
        window_end = min(window_start + step - timedelta(days=1), end_date)
        yield window_start, window_end
        window_start = window_end + timedelta(days=1)


def windowed_rows(fetch, start_date, end_date, window_days=DEFAULT_WINDOW_DAYS):
    """Yield one window's rows as a list, releasing it before fetching the next.

    ``fetch(window_start, window_end)`` returns the queryset for that window. Keeping the
    filter construction with the caller means the window bounds are applied the way each
    table needs them -- a ``date`` column for most, ``session_midnight`` timestamp bounds
    for ``TechnicalIndicator`` so the filter stays sargable.

    The caller must consume each yielded list before the next iteration; it is deleted
    on resume, so holding a reference to it defeats the memory bound.
    """
    for window_start, window_end in date_windows(start_date, end_date, window_days):
        rows = list(fetch(window_start, window_end))
        yield rows
        del rows


def iter_windowed(fetch, start_date, end_date, window_days=DEFAULT_WINDOW_DAYS):
    """Row-at-a-time view of :func:`windowed_rows`, for callers with a long per-row body.

    Same memory bound -- one window's list is live at a time and released before the next
    is fetched -- without re-indenting an existing loop body into a nested pair.
    """
    for rows in windowed_rows(fetch, start_date, end_date, window_days):
        yield from rows