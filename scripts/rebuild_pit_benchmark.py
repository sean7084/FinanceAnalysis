"""Rebuild the point-in-time union benchmark in bounded chunks, then assert the NAV chain.

Two problems shaped this script.

1. `build_pit_union_benchmark --start-date 2010 --end-date 2026` builds the whole NAV
   chain in one call and died with "server closed the connection unexpectedly": the
   database is on a NAS from WSL2, and one very long request leaves the connection idle
   long enough to be dropped. Fix: process year-sized chunks, each seeding from the
   previous chunk's last stored NAV.

2. Chunking needs an anchor rule, and getting it wrong is what produced the six
   chunk-boundary fossils this script repairs. The builder treats the first date of its
   range as a seed -- return zero, nav = ``initial_nav`` -- and ``emit_seed_row=False``
   drops that seed from the written rows while still using it as the price anchor. So a
   post-first chunk must START AT the previous chunk's last trade_date, not at Jan 1:
   starting at Jan 1 makes the boundary session itself the unwritten anchor, its fossil
   row is never overwritten, and its return is lost again. Anchoring on the prior row
   makes the first session after the boundary the first WRITTEN row, computed against the
   anchor's prices -- exactly what ``refresh_latest_point_in_time_union_benchmark`` does
   for the daily path.

An earlier revision of this script papered over the boundary problem by re-deriving
``daily_return`` from ``nav / lag(nav) - 1`` in one UPDATE after the walk. That is no
longer what the code needs: the builder derives each return from constituent prices and
multiplies the nav by it, so re-deriving afterwards would only restate the same number at
float precision, and would hide a builder that had written the wrong thing. The pass is a
read-only assertion now.

Usage::

    python scripts/rebuild_pit_benchmark.py     # writes markets_pointintimebenchmarkdaily

Idempotent: rows are upserted on ``(benchmark_code, trade_date)`` and nothing is deleted,
so an interrupted run can be restarted from the top. Stop Celery first -- the trigger for
the wedged NAS link is concurrency, not capacity.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date
from pathlib import Path

# Running this file directly puts scripts/ on sys.path[0], not the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('DJANGO_READ_DOT_ENV_FILE', 'True')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django

django.setup()

from decimal import Decimal  # noqa: E402

from django.db import InterfaceError, OperationalError, connection  # noqa: E402
from django.db.models import Max  # noqa: E402

from apps.markets.benchmarking import (  # noqa: E402
    PIT_BENCHMARK_CODE,
    refresh_point_in_time_union_benchmark,
)
from apps.markets.models import (  # noqa: E402
    OHLCV,
    PointInTimeBenchmarkDaily,
)

START = date(2010, 1, 1)
RETRIES = 5
RETRY_DELAY = 5.0
INITIAL_NAV = Decimal('100000')
CHAIN_TOLERANCE = '1e-6'


def close_connections():
    from django.db import connections

    connections.close_all()


def with_retry(label, action):
    for attempt in range(RETRIES):
        try:
            return action()
        except (OperationalError, InterfaceError) as exc:
            delay = RETRY_DELAY * (attempt + 1)
            print(f'  {label}: dropped ({exc.__class__.__name__}), '
                  f'retry {attempt + 1}/{RETRIES} in {delay:.0f}s', flush=True)
            close_connections()
            time.sleep(delay)
    raise RuntimeError(f'{label} failed after {RETRIES} attempts')


def chunks():
    """Year-sized (start, end) pairs bounded by the last day we have prices for."""
    end = with_retry('max ohlcv date', ohlcv_max)
    year = START.year
    while year <= end.year:
        first = max(START, date(year, 1, 1))
        last = min(end, date(year, 12, 31))
        if first <= last:
            yield first, last
        year += 1


def ohlcv_max():
    return OHLCV.objects.aggregate(m=Max('date'))['m']


def assert_chain(last_date):
    """Read-only: the stored returns must reproduce the nav chain the builder wrote.

    Returns the worst deviation, the count of rows over tolerance, the count checked, the
    series' own first date, and every zero-return date other than that first date. The
    first session is legitimately flat -- it is the chain's anchor -- so it is reported
    separately instead of failing the run.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH ordered AS (
              SELECT trade_date,
                     daily_return,
                     nav / lag(nav) OVER (PARTITION BY benchmark_code ORDER BY trade_date) - 1 AS derived
              FROM markets_pointintimebenchmarkdaily
              WHERE benchmark_code = %s AND trade_date >= %s AND trade_date <= %s
            )
            SELECT max(abs(daily_return - derived)),
                   count(*) FILTER (WHERE abs(daily_return - derived) > %s),
                   count(*)
              FROM ordered
             WHERE derived IS NOT NULL
            """,
            [PIT_BENCHMARK_CODE, START, last_date, CHAIN_TOLERANCE],
        )
        max_dev, bad_rows, rows_checked = cursor.fetchone()

    first_session = PointInTimeBenchmarkDaily.objects.filter(
        benchmark_code=PIT_BENCHMARK_CODE
    ).order_by('trade_date').values_list('trade_date', flat=True).first()
    flat = [
        day for day in PointInTimeBenchmarkDaily.objects.filter(
            trade_date__gte=START, trade_date__lte=last_date, daily_return=0
        ).values_list('trade_date', flat=True)
        if day != first_session
    ]
    return max_dev, bad_rows, rows_checked, first_session, flat


def rebuild_chunk(chunk_start, chunk_end):
    """Refresh one chunk, anchored on the previous chunk's last stored row."""
    prior = with_retry(
        f'{chunk_start} prior nav',
        lambda cs=chunk_start: PointInTimeBenchmarkDaily.objects.filter(
            trade_date__lt=cs
        ).order_by('-trade_date').first(),
    )
    if prior is None:
        # The series legitimately starts with a zero-return anchor row.
        return with_retry(
            f'{chunk_start}..{chunk_end}',
            lambda cs=chunk_start, ce=chunk_end: refresh_point_in_time_union_benchmark(
                start_date=cs, end_date=ce, initial_nav=INITIAL_NAV, emit_seed_row=True
            ),
        )
    return with_retry(
        f'{prior.trade_date}..{chunk_end}',
        lambda sd=prior.trade_date, ce=chunk_end, nav=prior.nav: refresh_point_in_time_union_benchmark(
            start_date=sd, end_date=ce, initial_nav=nav, emit_seed_row=False
        ),
    )


def main():
    pairs = list(chunks())
    print(f'{len(pairs)} yearly chunks {START} .. {pairs[-1][1]}', flush=True)
    started = time.time()
    total_rows = 0
    for chunk_start, chunk_end in pairs:
        summary = rebuild_chunk(chunk_start, chunk_end)
        total_rows += summary['rows_written']
        print(f'  {chunk_start}..{chunk_end}: rows={summary["rows_written"]}', flush=True)

    last_date = pairs[-1][1]
    max_dev, bad_rows, rows_checked, first_session, flat = with_retry(
        'chain assertion', lambda: assert_chain(last_date)
    )
    print(f'PIT rebuilt: {total_rows} row-writes in {(time.time() - started) / 60:.1f} min', flush=True)
    print(f'chain: rows_checked={rows_checked} max_dev={max_dev} bad_rows={bad_rows}', flush=True)
    print(f'zero-return rows other than the {first_session} anchor: {[str(day) for day in flat]}', flush=True)

    sample = PointInTimeBenchmarkDaily.objects.filter(
        trade_date__gte=date(2010, 12, 28), trade_date__lte=date(2011, 1, 6)
    ).order_by('trade_date')
    for row in sample:
        print(f'  {row.trade_date} nav={row.nav} ret={row.daily_return}', flush=True)

    return 1 if (bad_rows or flat) else 0


if __name__ == '__main__':
    sys.exit(main())
