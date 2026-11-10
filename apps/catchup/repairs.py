"""Repair entry points. Each one calls an existing, already-idempotent command.

Nothing here fetches data itself -- every repair delegates to a management command
or task that already owns that series, so the catch-up inherits its upsert
semantics, its throttle pacing and its blackout handling rather than duplicating
them. All of the commands used here were verified to be ``call_command``-safe: they
write through ``self.stdout``/``self.stderr`` and never prompt, ``sys.exit`` or
require a TTY.
"""

from dataclasses import dataclass
from io import StringIO

from django.core.cache import cache
from django.core.management import call_command

from apps.markets.tasks import DEFAULT_INDEX_CODES, DEFAULT_MEMBERSHIP_INDEX_CODES

from .models import CatchUpRun

# Set once, without expiry, when TuShare refuses the yield-curve endpoint. The
# entitlement probe found yc_cb and monetary_policy denied on this token tier, so
# retrying costs MACRO_YIELD_BACKFILL_CALL_SLEEP_SECONDS (31s) per attempt to learn
# something that will not change. See docs/reports/tushare-entitlement-2026-10-27.md.
MACRO_YIELDS_DENIED_KEY = 'catchup:macro_yields_entitlement_denied'

PERMISSION_MARKERS = ('没有权限', '权限', 'permission', 'denied', '抱歉，您没有')


@dataclass(frozen=True)
class RepairResult:
    units_done: int
    units_remaining: int
    estimated_calls_spent: int
    outcome: str
    cause: str = ''
    detail: str = ''
    repaired_dates: tuple = ()


def _ok(detail, *, calls=0, dates=(), remaining=0):
    return RepairResult(
        units_done=max(len(dates), 1) if dates else 1,
        units_remaining=remaining,
        estimated_calls_spent=calls,
        outcome=CatchUpRun.Outcome.OK,
        detail=detail,
        repaired_dates=tuple(dates),
    )


def _is_permission_error(exc):
    text = str(exc)
    return any(marker in text for marker in PERMISSION_MARKERS)


def _run(command, **kwargs):
    """Call a management command, discarding its stdout but keeping its failures."""
    output = StringIO()
    call_command(command, stdout=output, **kwargs)
    return output.getvalue().strip()


# --- Raw layer -----------------------------------------------------------------

def repair_calendar(ctx, gap, budget):
    summary = _run('backfill_trading_calendar',
                   start_date=gap.missing_dates[0].isoformat(),
                   end_date=gap.missing_dates[-1].isoformat(),
                   exchange_codes='SSE,SZSE')
    budget.spend(2)
    return _ok(summary or 'calendar refreshed', calls=2, dates=gap.missing_dates)


def repair_suspensions(ctx, gap, budget):
    """Re-fetch the whole watermark window in one pass.

    ``backfill_asset_suspensions`` deletes and recreates per 60-day window and pulls
    the entire market per window, so the window length barely changes the cost --
    one ``suspend_d`` call covers 60 days.
    """
    summary = _run('backfill_asset_suspensions',
                   start_date=gap.window_start.isoformat(),
                   end_date=gap.window_end.isoformat())
    budget.spend(1)
    return _ok(summary or 'suspensions refreshed', calls=1)


def repair_ohlcv(ctx, gap, budget):
    """One dispatcher run, never one run per date.

    ``sync_asset_history`` resumes each asset from ``latest.date + 1 day``, so a
    single pass fills a hole of any length. Replaying the N missed occurrences would
    spend N times the quota to arrive at the same rows.
    """
    from apps.markets.tasks import sync_daily_a_shares

    summary = sync_daily_a_shares()
    return _ok(str(summary)[:500], dates=gap.missing_dates)


def repair_benchmark(ctx, gap, budget):
    summary = _run('sync_benchmark_index_history',
                   index_codes=','.join(DEFAULT_INDEX_CODES),
                   start_date=gap.missing_dates[0].isoformat(),
                   end_date=gap.missing_dates[-1].isoformat())
    calls = len(DEFAULT_INDEX_CODES)
    budget.spend(calls)
    return _ok(summary or 'benchmark refreshed', calls=calls, dates=gap.missing_dates)


def repair_membership(ctx, gap, budget):
    # skip_sync_dispatch is load-bearing: without it the command fans out one
    # sync_asset_history task per constituent, which is both redundant (the ohlcv
    # series already repaired them) and a quota spike nobody budgeted for.
    summary = _run('sync_index_constituents',
                   index_codes=','.join(DEFAULT_MEMBERSHIP_INDEX_CODES),
                   start_date=gap.window_start.isoformat(),
                   end_date=gap.window_end.isoformat(),
                   skip_sync_dispatch=True)
    budget.spend(4)
    return _ok(summary or 'membership refreshed', calls=4)


def repair_etf_daily(ctx, gap, budget):
    """Widen the range, not the beat entry.

    ``sync_etf_daily`` reuses ``missing_etf_daily_dates`` internally, so passing the
    full gap span gets real gap detection over an arbitrary window while the daily
    ``sync_etf_daily_recent`` entry keeps its 10-day bound.
    """
    dates = budget.take_dates(gap.missing_dates, cost_per_unit=2)
    summary = _run('sync_etf_daily',
                   start=dates[0].isoformat(),
                   end=dates[-1].isoformat())
    calls = 2 * len(dates)
    budget.spend(calls)
    remaining = len(gap.missing_dates) - len(dates)
    outcome = CatchUpRun.Outcome.PARTIAL if remaining else CatchUpRun.Outcome.OK
    return RepairResult(
        units_done=len(dates), units_remaining=max(remaining, 0),
        estimated_calls_spent=calls, outcome=outcome,
        detail=summary or f'{len(dates)} ETF dates synced',
        repaired_dates=tuple(dates),
    )


def repair_capital_flow(ctx, gap, budget):
    """Per-asset, so the hole's length is irrelevant -- only the asset count is.

    Two calls per asset (``moneyflow`` + ``margin_detail``), each over a 5-year
    window, plus a 0.35s pause. ``--limit-assets`` is what makes this batchable
    across runs instead of one 1000-call spike.
    """
    limit = budget.affordable_assets(calls_per_asset=2)
    summary = _run('backfill_capital_flow_snapshots',
                   start_date=gap.missing_dates[0].isoformat(),
                   end_date=gap.missing_dates[-1].isoformat(),
                   limit_assets=limit)
    calls = 2 * limit
    budget.spend(calls)
    return _ok(summary or f'capital flow refreshed for up to {limit} assets',
               calls=calls, dates=gap.missing_dates)


def repair_fundamentals(ctx, gap, budget):
    """Per-asset. The command already skips assets it considers complete, so a
    repeat run is cheap by construction rather than by our bookkeeping."""
    limit = budget.affordable_assets(calls_per_asset=3)
    summary = _run('backfill_fundamental_snapshots',
                   start_date=gap.missing_dates[0].isoformat(),
                   end_date=gap.missing_dates[-1].isoformat(),
                   limit_assets=limit)
    calls = 3 * limit
    budget.spend(calls)
    return _ok(summary or f'fundamentals refreshed for up to {limit} assets',
               calls=calls, dates=gap.missing_dates)


def repair_macro_core(ctx, gap, budget):
    summary = _run('backfill_macro_snapshots',
                   start_date=gap.missing_dates[0].isoformat(),
                   end_date=gap.missing_dates[-1].isoformat(),
                   disable_fallback=True)
    budget.spend(5)
    return _ok(summary or 'macro snapshots refreshed', calls=5, dates=gap.missing_dates)


def repair_macro_yields(ctx, gap, budget):
    """Never retry a denied entitlement.

    ``yc_cb`` is refused on this token tier, and the yield phase sleeps 31s between
    calls by design. Retrying buys nothing and burns the run's wall clock, so the
    first refusal is cached without expiry and every later run short-circuits.
    """
    if cache.get(MACRO_YIELDS_DENIED_KEY):
        return RepairResult(
            units_done=0, units_remaining=0, estimated_calls_spent=0,
            outcome=CatchUpRun.Outcome.SKIPPED,
            cause=CatchUpRun.Cause.ENTITLEMENT,
            detail='yc_cb denied on this token tier; not retried',
        )
    try:
        summary = _run('backfill_macro_snapshots',
                       start_date=gap.missing_dates[0].isoformat(),
                       end_date=gap.missing_dates[-1].isoformat(),
                       resume_yields=True)
    except Exception as exc:
        if _is_permission_error(exc):
            cache.set(MACRO_YIELDS_DENIED_KEY, True, timeout=None)
            return RepairResult(
                units_done=0, units_remaining=0, estimated_calls_spent=0,
                outcome=CatchUpRun.Outcome.SKIPPED,
                cause=CatchUpRun.Cause.ENTITLEMENT, detail=str(exc)[:500],
            )
        raise
    budget.spend(12)
    return _ok(summary or 'yield curve refreshed', calls=12, dates=gap.missing_dates)


# --- Derived layer -------------------------------------------------------------

def _checkpoint_path(ctx, tag):
    return f'reports/ops_logs/catchup_{tag}_{ctx.trigger}.json'


def replay_technical_indicators(ctx, gap, budget):
    summary = _run('backfill_technical_indicators',
                   start_date=gap.missing_dates[0].isoformat(),
                   end_date=gap.missing_dates[-1].isoformat(),
                   chunk_size_days=120,
                   checkpoint_file=_checkpoint_path(ctx, 'ti'),
                   resume_from_checkpoint=True)
    return _ok(summary or 'technical indicators rebuilt', dates=gap.missing_dates)


def replay_model_data(ctx, gap, budget):
    """RS_SCORE is owned by backfill_model_data, not backfill_technical_indicators.

    runbook-stale-data.md §3.3 calls running the wrong one of these "the most common
    wasted hour", so the split is worth a comment at the call site.
    """
    summary = _run('backfill_model_data',
                   start_date=gap.missing_dates[0].isoformat(),
                   end_date=gap.missing_dates[-1].isoformat(),
                   checkpoint_file=_checkpoint_path(ctx, 'md'),
                   resume_from_checkpoint=True)
    return _ok(summary or 'model data rebuilt', dates=gap.missing_dates)


def replay_factor_scores(ctx, gap, budget):
    from apps.factors.tasks import calculate_factor_scores_for_date

    for day in gap.missing_dates:
        calculate_factor_scores_for_date(target_date=day.isoformat())
    return _ok(f'factor scores recomputed for {len(gap.missing_dates)} dates',
               dates=gap.missing_dates)


def replay_etf_features(ctx, gap, budget):
    from apps.etf_rotation.tasks import compute_etf_features_daily

    compute_etf_features_daily.run()
    return _ok('ETF features recomputed over the full feature window',
               dates=gap.missing_dates)


def delegate_predictions(ctx, gap, budget):
    """Hand the oldest hole to the reconcile task that already owns it.

    Never ``generate_predictions_for_date`` from here: that would double-run against
    the hourly ``reconcile-prediction-coverage-hourly`` entry, which applies its own
    per-run cap and is the mechanism designed for this.
    """
    from apps.prediction.tasks import reconcile_prediction_coverage

    day = gap.missing_dates[0]
    reconcile_prediction_coverage.delay(target_date=day.isoformat())
    remaining = max(len(gap.missing_dates) - 1, 0)
    return RepairResult(
        units_done=1, units_remaining=remaining, estimated_calls_spent=0,
        outcome=CatchUpRun.Outcome.PARTIAL if remaining else CatchUpRun.Outcome.OK,
        detail=f'queued prediction reconcile for {day}; {remaining} dates still behind',
        repaired_dates=(day,),
    )
