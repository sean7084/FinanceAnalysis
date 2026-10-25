"""Run one ETF rotation backtest, or the 144-configuration RULE grid search.

Two modes, because the two questions have opposite costs.

**Single config** (`--model RULE|LIGHTGBM|LSTM|ENSEMBLE`) persists a named
``ETFRotationBacktest`` by default so the result is attributable afterwards instead of
evaporating into a terminal. ``--backtest-id`` makes the row's own columns the
configuration, mirroring ``apps.etf_rotation.tasks.run_etf_rotation_backtest`` exactly:
a run started here and a run started by the API must not disagree about the fee model.
``--queue`` hands that row to the 3600s-limit Celery task rather than executing inline.

**Grid** (`--grid`) sweeps ONLY the 144 RULE combinations (C25): 4 rebalance
frequencies x 3 max_positions x 2 absolute-momentum filters x 3 momentum lookbacks x 2
weightings. ML models are excluded on purpose -- 576 combinations times 6 walk-forward
folds is roughly 29 machine-hours, which is not a search anyone can run, so the grid
finds the parameter region and the ML runs are then evaluated *inside* it. The
``weighting`` dimension is the fifth because it is the one the engine already exposes on
``BacktestConfig`` (forwarded verbatim into ``RotationConfig``) and it lands the product
on exactly 144; it is both a column and part of the row's ``name``, so a grid row can be
filtered on it without reading JSON.

**Walk-forward** (`--grid --walk-forward-steps N`) answers the question the single-window
grid cannot: would the parameter *selection itself* have survived out of sample. The
window is cut into N expanding folds with ``walk_forward.expanding_folds`` (sessions as
``trade_dates``, a one-session label horizon, 250 training sessions); each fold runs the
same 144 grids on ``[train_start, train_end]`` only, takes the Sharpe winner (a dead heat
goes to the lower turnover), then trades that configuration on
``[valid_start, valid_end]``. Every run is ephemeral -- nothing is persisted, so this is
a CLI evaluation and never a resumable row. A fold with fewer than 20 rebalances is too
thin to read; the plan's answer is a rerun with ``--walk-forward-steps 4``, which the
command flags instead of silently lengthening the folds.

Every grid row is persisted (C24: ``momentum_lookback`` exists as a column precisely so a
swept horizon is recoverable), keyed by name so an interrupted grid resumes rather than
duplicates. That same key is why ``--grid-prefix`` exists: re-running a window under the
default stem *skips* all 144 combinations, and ``--grid-force`` would overwrite the first
generation with the second. After a change to the selection rules -- index-cluster dedup,
the category cap, anything else that lands in the ranking -- sweep under a fresh stem so
both generations survive to be compared.

Examples::

    python manage.py run_etf_rotation_backtest --start 2018-01-02 --end 2018-12-31 --model RULE
    python manage.py run_etf_rotation_backtest --backtest-id 12
    python manage.py run_etf_rotation_backtest --start 2019-01-01 --end 2024-12-31 --grid --grid-top 15
    python manage.py run_etf_rotation_backtest --start 2019-01-01 --end 2024-12-31 --grid --grid-plan
    python manage.py run_etf_rotation_backtest --start 2016-01-29 --end 2023-12-31 --grid --grid-prefix grid-dedup
"""

from __future__ import annotations

import importlib
import itertools
import math
import time
from datetime import date
from typing import Any

import pandas as pd
from django.core.management.base import BaseCommand, CommandError

from apps.core import finance_metrics
from apps.etf_rotation import rank_ic, tasks
from apps.etf_rotation.backtest_engine import (
    MODEL_TYPES,
    REBALANCE_FREQUENCIES,
    BacktestConfig,
    BacktestConfigError,
    BacktestDataUnavailable,
    DatabaseDataSource,
    PricePanel,
    RotationEngineUnavailable,
    defensive_asset_problem,
    run_rotation_backtest,
)
from apps.etf_rotation.models import ETFRotationBacktest
from apps.etf_rotation.rotation_engine import WEIGHTING_MODES, default_defensive_asset
from apps.etf_rotation.walk_forward import (
    WalkForwardError,
    audit_combinatorial_folds,
    combinatorial_purged_folds,
    expanding_folds,
)

# ``CELERY_TASK_ROUTES`` puts ``apps.etf_rotation.tasks.run_etf_rotation_backtest`` on this
# queue; the name is only printed, never used to route, so keep the two in step.
ETF_ROTATION_QUEUE = 'etf-rotation'

#: The C25 sweep. The product is asserted, not trusted: a dimension edited without the
#: count being re-checked is how a "144-combo grid" quietly becomes 120 or 240.
GRID_FREQUENCIES = ('WEEKLY', 'BIWEEKLY', 'MONTHLY', 'TRIGGER')
GRID_MAX_POSITIONS = (3, 5, 8)
GRID_ABSOLUTE_FILTERS = (True, False)
GRID_MOMENTUM_LOOKBACKS = (20, 60, 120)
# Pinned to the two modes the reference grid was built on, NOT widened onto
# ``WEIGHTING_MODES`` with ``invvol`` added: that would make the sweep 216 combinations
# for a 50% longer run to answer a question the grid was never asked. Inverse-volatility
# weighting is compared head-to-head against equal and score at the reference
# configuration, which is a three-run job. Widening this tuple means re-checking
# GRID_EXPECTED_COMBOS.
GRID_WEIGHTINGS = ('equal', 'score')
GRID_EXPECTED_COMBOS = 144

#: Cost/execution knobs the grid runs under a single value. Listed once so the grid and
#: the walk-forward branch cannot drift into rejecting different flags.
GRID_COST_FLAGS = (
    'commission_rate', 'min_commission', 'slippage',
    'liquidity_floor_multiple', 'min_trade_notional_fraction',
    'drift_band_fraction', 'liquidity_mode', 'adv_participation_cap',
)

#: Walk-forward knobs handed to ``walk_forward.expanding_folds``. The RULE ranker fits no
#: label, so a one-session horizon is enough to keep a fold's last training session from
#: reaching its validation block; 250 sessions is roughly a year of the short list's
#: three-year lookback. The rebalance floor is the plan's reading rule: below it a fold's
#: OOS number is too thin to interpret and the run should drop to fewer, longer folds.
WALK_FORWARD_LABEL_HORIZON = 1
WALK_FORWARD_MIN_TRAIN_SESSIONS = 250
WALK_FORWARD_MIN_FOLD_REBALANCES = 20

#: CPCV (combinatorial purged cross-validation) defaults. Six blocks choosing two gives
#: C(6,2)=15 splits, whose held-out blocks cover the panel several times over -- enough for
#: a distribution without pretending a decade of A-share ETFs contains dozens of independent
#: regimes. The embargo is in sessions and applies *after* each held-out block; the purge
#: before it is the label window, the same rule walk_forward already enforces.
CPCV_DEFAULT_BLOCKS = 6
CPCV_DEFAULT_TEST_BLOCKS = 2
CPCV_DEFAULT_EMBARGO_SESSIONS = 5
#: The RULE ranker fits no label, so a one-session purge is sufficient for its own stability
#: reading. A fitted model measuring the same splits should use its model horizon.
CPCV_LABEL_HORIZON = 1

#: The locked holdout (``docs/plans/etf-rotation-selection-sizing-2026-10-23.md`` §7.7) was
#: spent once and must not be queried again. CPCV is a tuning-window measurement, so a
#: window that reaches this date is refused rather than silently producing a second look.
LOCKED_HOLDOUT_START = date(2024, 1, 1)

#: The production reference (``ETFRotationBacktest`` row 256), used only to report how
#: often the walk-forward selection lands on it. Changing a default is NOT part of this.
REFERENCE_RULE_COMBO = {
    'rebalance_frequency': 'BIWEEKLY',
    'max_positions': 3,
    'absolute_momentum_filter': False,
    'momentum_lookback': 120,
    'weighting': 'equal',
}


#: Row columns the queued task reads back as its configuration. Same set as
#: ``tasks.run_etf_rotation_backtest``; a column added to the model belongs here too or a
#: queued run silently inherits the model default instead of what this command intended.
_QUEUE_KNOB_FIELDS = (
    'start_date', 'end_date', 'model_type', 'rebalance_frequency', 'max_positions',
    'momentum_lookback', 'absolute_momentum_filter', 'defensive_asset', 'initial_capital',
    'commission_rate', 'min_commission', 'slippage', 'liquidity_floor_multiple',
    'min_trade_notional_fraction', 'drift_band_fraction', 'liquidity_mode',
    'adv_participation_cap', 'kelly_fraction', 'kelly_cap', 'kelly_floor',
    'kelly_lookback_trades', 'cash_yield_annual', 'benchmark_code', 'walk_forward_steps',
    'weighting', 'dedup_track_index', 'max_per_category',
)

#: Bounds the cross-run snapshot memo. One session's eligible cross-section is a few
#: hundred rows, so this is tens of MB for a decade-long window and not unbounded.
SNAPSHOT_MEMO_SESSIONS = 512


def grid_combinations() -> list[dict[str, Any]]:
    """The 144 RULE configurations of the C25 sweep, in deterministic order."""
    combos = [
        {'rebalance_frequency': frequency, 'max_positions': positions,
         'absolute_momentum_filter': absolute, 'momentum_lookback': lookback,
         'weighting': weighting}
        for frequency, positions, absolute, lookback, weighting in itertools.product(
            GRID_FREQUENCIES, GRID_MAX_POSITIONS, GRID_ABSOLUTE_FILTERS,
            GRID_MOMENTUM_LOOKBACKS, GRID_WEIGHTINGS,
        )
    ]
    product = (len(GRID_FREQUENCIES) * len(GRID_MAX_POSITIONS) * len(GRID_ABSOLUTE_FILTERS)
               * len(GRID_MOMENTUM_LOOKBACKS) * len(GRID_WEIGHTINGS))
    if len(combos) != product or product != GRID_EXPECTED_COMBOS:
        raise CommandError(
            f'Grid dimension product is {product} ({len(combos)} combinations), not the '
            f'{GRID_EXPECTED_COMBOS} the plan specifies. Check the GRID_* constants.'
        )
    return combos


def grid_stem(combo: dict[str, Any]) -> str:
    """The configuration spelled out, without the leading name prefix."""
    return (
        f"rule-{combo['rebalance_frequency'].lower()}-p{combo['max_positions']}"
        f"-mom{combo['momentum_lookback']}-{'abs' if combo['absolute_momentum_filter'] else 'noabs'}"
        f"-{combo['weighting']}"
    )


def grid_name(combo: dict[str, Any], prefix: str = 'grid') -> str:
    """Name spelling out the whole configuration, including the two knobs a table row cannot show.

    ``prefix`` exists because the row's name is the grid's resume key: re-running the same
    window with the same prefix finds the rows that are already COMPLETED and skips them.
    That is right for an interrupted sweep and wrong for a re-run after the *selection rules*
    change -- the old rows would be skipped, or overwritten with ``--grid-force``, and the
    before/after comparison the re-run exists to produce would be gone. A second prefix keeps
    both generations side by side.
    """
    return f'{str(prefix or "").strip() or "grid"}-{grid_stem(combo)}'


def walk_forward_folds(sessions, n_folds: int) -> list:
    """Build expanding folds over the session calendar, or fewer when the panel is short.

    ``expanding_folds`` lays out ``n`` equal validation steps after
    ``WALK_FORWARD_MIN_TRAIN_SESSIONS`` and before the one-session label tail. When the
    validation region cannot supply ``n`` non-empty steps it can emit a fold whose
    ``valid_start`` is past its ``valid_end`` (the step arithmetic clamps the end, not the
    start); such a fold would be scored on nothing. This wrapper asks for the largest
    count, up to ``n_folds``, whose folds are all non-empty -- so a short panel silently
    loses folds but is never padded with invented boundaries. ``[]`` means the panel
    cannot even seat one fold, and the caller refuses rather than training on nothing.
    """
    ordered = sorted({value for value in sessions if value is not None})
    requested = int(n_folds)
    if requested < 1:
        raise ValueError(f'n_folds must be >= 1, got {requested}.')
    if not ordered:
        return []
    for candidate in range(requested, 0, -1):
        folds = expanding_folds(
            ordered[0], ordered[-1], candidate,
            horizon_days=WALK_FORWARD_LABEL_HORIZON,
            min_train_days=WALK_FORWARD_MIN_TRAIN_SESSIONS,
            trade_dates=ordered,
        )
        if not folds:
            return []
        if all(fold.valid_start <= fold.valid_end for fold in folds):
            return folds
    return []


def _measured(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def walk_forward_rank_key(row: dict[str, Any]) -> tuple:
    """Declared selection order: Sharpe first, a dead heat to the lower turnover.

    An unmeasured Sharpe sorts after every measured one; the configuration name last so
    the same grid always picks the same winner instead of depending on dict order.
    """
    metrics = row.get('metrics') or {}
    sharpe = _measured(metrics.get('sharpe_ratio'))
    turnover = _measured(metrics.get('turnover_annual'))
    return (
        sharpe is None,
        -sharpe if sharpe is not None else 0.0,
        turnover if turnover is not None else math.inf,
        str(row.get('name') or grid_stem(row)),
    )


def select_walk_forward_winner(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The one row the declared criterion selects; refuses a fold with nothing measured."""
    if not rows:
        raise ValueError('no configuration was scored, so no winner can be selected.')
    winner = min(rows, key=walk_forward_rank_key)
    if _measured((winner.get('metrics') or {}).get('sharpe_ratio')) is None:
        raise ValueError('no configuration produced a finite Sharpe ratio.')
    return winner


def is_reference_combo(combo: dict[str, Any]) -> bool:
    """Whether a selected configuration is exactly the production reference (row 256)."""
    return all(combo.get(key) == value for key, value in REFERENCE_RULE_COMBO.items())


def walk_forward_table(outcomes: list[dict[str, Any]]) -> list[str]:
    """One line per fold: the selected configuration, its training score, and OOS evidence.

    Validation metrics are the fold's own run (each fold starts from the configured
    initial capital), so the table is a series of independent OOS readings, not a
    compounded curve. The rebalance count is printed per fold because it is the reading
    floor the plan sets: a fold whose winner trades rarely cannot support a conclusion.
    """
    lines = [
        f'{"fold":>4} {"train":<22} {"valid":<22} {"config":<36} '
        f'{"tr_sharpe":>9} {"tr_reb":>6} {"return":>9} {"annual":>9} {"maxDD":>8} '
        f'{"sharpe":>7} {"alpha":>9} {"turnover":>8} {"reb":>5} {"ref":>3}'
    ]
    for item in outcomes:
        fold = item['fold']
        train = item.get('train_metrics') or {}
        valid = item.get('valid_metrics') or {}
        window = f'{fold["train_start"]}..{fold["train_end"]}'
        valid_window = f'{fold["valid_start"]}..{fold["valid_end"]}'
        lines.append(
            f'{int(fold["fold_index"]) + 1:>4} '
            f'{window:<22} '
            f'{valid_window:<22} '
            f'{str(item.get("winner_name") or ""):<36} '
            f'{_num(train.get("sharpe_ratio")):>9} '
            f'{int(item.get("train_rebalances") or 0):>6} '
            f'{_pct(valid.get("total_return")):>9} '
            f'{_pct(valid.get("annualized_return")):>9} '
            f'{_pct(valid.get("max_drawdown"), signed=False):>8} '
            f'{_num(valid.get("sharpe_ratio")):>7} '
            f'{_pct(valid.get("alpha_vs_benchmark")):>9} '
            f'{_num(valid.get("turnover_annual")):>8} '
            f'{int(item.get("valid_rebalances") or 0):>5} '
            f'{"yes" if item.get("reference") else "no":>3}'
        )
    return lines



def parse_iso_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise CommandError(f'{label} must be YYYY-MM-DD, got {value!r}.') from exc


def control_halt_message(result: dict[str, Any]) -> str:
    """Describe a run the engine stopped early because the row asked it to.

    A halted result carries no ``metrics``, so every caller must branch on ``control`` before
    reporting; the CLI is the only consumer that can turn it into plain text rather than a
    task-layer hand-off. ``RESTART`` is left pending on the row -- wiping it and re-queuing is
    ``tasks.run_etf_rotation_backtest``'s job, not something an inline run should do.
    """
    processed = f"{result.get('cursor')}/{result.get('total_sessions')} trading sessions"
    control = str(result.get('control') or '')
    if control == 'PAUSED':
        return f'row paused by request after {processed}; resume it to continue from the cursor'
    if control == 'RESTART':
        return (
            f'restart requested after {processed}; the inline run stopped and left the request '
            'for the next worker pickup'
        )
    return f'row deleted by request after {processed}'


class CachedDataSource:
    """Memoising wrapper over the engine's read-only data seam, for the 144 grid runs.

    ``DatabaseDataSource`` caches ~32 sessions of snapshots and nothing else, and
    ``PriceBook`` is rebuilt per run, so 144 configs over one window would re-query the
    same ``ETFDaily`` panels and the same cross-sections 144 times -- against a database
    that is simultaneously being backfilled. This wrapper adds no engine logic: it
    delegates every query and remembers only what came back. Duck-typed against the
    documented ``data_source`` seam (``sessions``/``snapshots``/``prices``/
    ``benchmark_close``).
    """

    def __init__(self, inner: DatabaseDataSource) -> None:
        self.inner = inner
        self._sessions: dict[tuple[date, date], list[date]] = {}
        self._benchmarks: dict[tuple[str, date, date], pd.Series] = {}
        self._panels: dict[tuple[date, date], dict[str, dict[str, pd.Series]]] = {}
        self._snapshots: dict[date, pd.DataFrame] = {}
        self.db_queries = 0

    def sessions(self, start_date: date, end_date: date) -> list[date]:
        key = (start_date, end_date)
        if key not in self._sessions:
            self.db_queries += 1
            self._sessions[key] = list(self.inner.sessions(start_date, end_date))
        return self._sessions[key]

    def benchmark_close(self, index_code: str, start_date: date, end_date: date) -> pd.Series:
        key = (index_code, start_date, end_date)
        if key not in self._benchmarks:
            self.db_queries += 1
            self._benchmarks[key] = self.inner.benchmark_close(index_code, start_date, end_date)
        return self._benchmarks[key]

    def snapshots(self, session: date) -> pd.DataFrame:
        if session not in self._snapshots:
            self.db_queries += 1
            self._snapshots[session] = self.inner.snapshots(session)
            while len(self._snapshots) > SNAPSHOT_MEMO_SESSIONS:
                oldest = next(iter(self._snapshots))
                del self._snapshots[oldest]
        return self._snapshots[session]

    def prices(self, ts_codes, start_date: date, end_date: date) -> PricePanel:
        wanted = sorted({str(code) for code in ts_codes if code})
        if not wanted:
            return self.inner.prices(wanted, start_date, end_date)
        store = self._panels.setdefault((start_date, end_date), {})
        missing = [code for code in wanted if code not in store]
        if missing:
            self.db_queries += 1
            panel = self.inner.prices(missing, start_date, end_date)
            for code in missing:
                # A code with no bar on any column is remembered as an empty entry so the
                # same unanswered ts_code is not re-queried on every rebalance.
                store[code] = {
                    column: getattr(panel, column)[code].astype('float64')
                    for column in ('close', 'adj_close', 'amount', 'volume')
                    if code in getattr(panel, column).columns
                }
        columns = {column: {} for column in ('close', 'adj_close', 'amount', 'volume')}
        for code in wanted:
            for column, series in store.get(code, {}).items():
                columns[column][code] = series
        return PricePanel(
            **{
                column: (
                    pd.DataFrame(columns[column]).sort_index()
                    if columns[column] else pd.DataFrame()
                )
                for column in ('close', 'adj_close', 'amount', 'volume')
            }
        )


class Command(BaseCommand):
    help = 'Run a single ETF rotation backtest, or the 144-combination RULE grid search.'

    def add_arguments(self, parser):
        parser.add_argument('--start', help='YYYY-MM-DD, inclusive.')
        parser.add_argument('--end', help='YYYY-MM-DD, inclusive. Required with --start.')
        parser.add_argument('--model', default=None, choices=list(MODEL_TYPES),
                            help='Ranker to use. Default: RULE.')
        parser.add_argument('--frequency', default=None, choices=list(REBALANCE_FREQUENCIES),
                            help='Rebalance frequency. Default: BacktestConfig (MONTHLY).')
        parser.add_argument('--max-positions', type=int, default=None,
                            help='Holding count. Default: BacktestConfig (5).')
        parser.add_argument('--momentum-lookback', type=int, default=None,
                            help='Ranking horizon in sessions. Default: BacktestConfig (60).')
        parser.add_argument('--no-absolute-momentum-filter', dest='absolute_momentum_filter',
                            action='store_false', default=None,
                            help='Drop the risk-on/risk-off absolute momentum gate (default: on).')
        parser.add_argument('--weighting', default=None, choices=list(WEIGHTING_MODES),
                            help='Position sizing across the selected legs. Default: settings.ETF_WEIGHTING.')
        parser.add_argument('--no-dedup-track-index', dest='dedup_track_index', action='store_false',
                            default=None,
                            help='Let several funds tracking the same index into one book. '
                                 'Default: on (one fund per tracked index).')
        parser.add_argument('--max-per-category', type=int, default=None,
                            help='Cap on names one category may contribute; 0 disables the cap. '
                                 'Default: settings.ETF_MAX_PER_CATEGORY (2).')
        parser.add_argument('--kelly-fraction', type=float, default=None,
                            help='Fraction of the Kelly-optimal exposure to use as a portfolio-level '
                                 'cap on the risk budget. 0 (default) disables the governor entirely.')
        parser.add_argument('--kelly-cap', type=float, default=None,
                            help='Upper bound on the Kelly exposure scale (default 1.0, i.e. never lever).')
        parser.add_argument('--kelly-floor', type=float, default=None,
                            help='Lower bound on the Kelly exposure scale (default 0.0).')
        parser.add_argument('--kelly-lookback-trades', type=int, default=None,
                            help='Closed trades the win/payoff estimate is taken over (default 60).')
        parser.add_argument('--cash-yield-annual', type=float, default=None,
                            help='Annualised rate credited to cash the liquidity floor left idle '
                                 '(default 0.0, which reproduces every pre-existing run).')
        parser.add_argument('--defensive-asset', default=None,
                            help='TS code of the risk-off sleeve. Default: settings.ETF_DEFENSIVE_ASSET.')
        parser.add_argument('--initial-capital', type=float, default=None,
                            help='Starting equity in yuan. Default: settings.ETF_INITIAL_CAPITAL. '
                                 'A small value is how the min-commission floor gets exercised.')
        parser.add_argument('--commission-rate', type=float, default=None,
                            help='Two-way broker commission rate. Default: settings.ETF_COMMISSION_RATE (0.0003).')
        parser.add_argument('--min-commission', type=float, default=None,
                            help='Yuan floor on one fill. Default: settings.ETF_MIN_COMMISSION (5).')
        parser.add_argument('--slippage', type=float, default=None,
                            help='Fraction applied to the execution price. Default: settings.ETF_SLIPPAGE (0.001).')
        parser.add_argument('--liquidity-floor-multiple', type=float, default=None,
                            help='Required trailing daily turnover as a multiple of the leg notional; '
                                 '0 disables the screen. Default: settings.ETF_LIQUIDITY_FLOOR_MULTIPLE (10).')
        parser.add_argument('--min-trade-notional-fraction', type=float, default=None,
                            help='Skip rebalance deltas below this share of equity (default 0.005).')
        parser.add_argument('--drift-band-fraction', type=float, default=None,
                            help='Leave a delta below this share of its target notional to drift (default 0 = off).')
        parser.add_argument('--liquidity-mode', choices=('reject', 'scale'), default=None,
                            help='Reject an oversized leg (default) or scale it to the ADV cap.')
        parser.add_argument('--adv-participation-cap', type=float, default=None,
                            help='Scale-mode ceiling as a multiple of trailing daily turnover (default 0 = off).')
        parser.add_argument('--name', default='', help='Name of the persisted row.')
        parser.add_argument('--persist', dest='persist', action='store_true', default=True,
                            help='Persist an ETFRotationBacktest row (the default).')
        parser.add_argument('--no-persist', dest='persist', action='store_false',
                            help='Run without writing anything; metrics go to stdout only.')
        parser.add_argument('--backtest-id', type=int, default=None,
                            help='Re-run an existing ETFRotationBacktest, using its columns as the config.')
        parser.add_argument('--queue', action='store_true',
                            help=f'Enqueue tasks.run_etf_rotation_backtest on {ETF_ROTATION_QUEUE} '
                                 'instead of running inline.')
        parser.add_argument('--sample-fills', type=int, default=5,
                            help='Filled trades to reprint with recomputed fees for the hand check (default 5).')
        parser.add_argument('--grid', action='store_true',
                            help=f'Sweep the {GRID_EXPECTED_COMBOS} RULE combinations (C25). Overrides --model.')
        parser.add_argument('--grid-top', type=int, default=10,
                            help='Rows of the ranked grid table (default 10).')
        parser.add_argument('--grid-limit', type=int, default=0,
                            help='Run only the first N combinations (0 = all). Cheap smoke of a long sweep.')
        parser.add_argument('--grid-plan', action='store_true',
                            help='Print the combinations the grid would run and execute nothing.')
        parser.add_argument('--grid-prefix', default='grid', metavar='NAME',
                            help='Name stem for the persisted grid rows (default: grid). The row name is the '
                                 'resume key, so a re-run after a selection-rule change needs a fresh stem to '
                                 'keep both generations side by side.')
        parser.add_argument('--grid-force', action='store_true',
                            help='Re-run grid rows that already COMPLETED under the same name (default: skip them).')
        parser.add_argument('--walk-forward-steps', type=int, default=None, metavar='N',
                            help='With --grid: split the window into N expanding folds, select the RULE '
                                 'configuration on each training window by Sharpe (lower turnover breaks a '
                                 'tie), and trade it on that fold\'s validation window. Ephemeral: no row '
                                 'is written. A fold with fewer than 20 rebalances is too thin; rerun '
                                 'with N=4, which gives longer validation blocks.')
        parser.add_argument('--cpcv-blocks', type=int, default=None, metavar='N',
                            help='Combinatorial purged cross-validation: cut the window into N contiguous '
                                 'blocks and measure the named configuration on every held-out block. '
                                 'Ephemeral, and it will not accept a window reaching 2024-01-01.')
        parser.add_argument('--cpcv-test-blocks', type=int, default=CPCV_DEFAULT_TEST_BLOCKS, metavar='K',
                            help=f'Blocks held out per split (default {CPCV_DEFAULT_TEST_BLOCKS}); blocks the '
                                 'other blocks train on after a label-window purge and an embargo.')
        parser.add_argument('--cpcv-embargo', type=int, default=CPCV_DEFAULT_EMBARGO_SESSIONS, metavar='D',
                            help=f'Sessions excluded after each held-out block (default {CPCV_DEFAULT_EMBARGO_SESSIONS}).')
        parser.add_argument('--model-version', default=None, metavar='VERSION',
                            help='With --cpcv-blocks and an ML --model: pin the READY ModelVersion to score '
                                 'with, without activating it. ENSEMBLE takes "lgb=<version>,lstm=<version>".')

    def handle(self, *args, **options):
        # Returns nothing on purpose: Django writes a truthy return value to stdout, and a
        # dict here would raise inside ``BaseCommand.execute``.
        if options['cpcv_blocks'] is not None:
            self._handle_cpcv(options)
        elif options['walk_forward_steps'] is not None:
            if not options['grid']:
                raise CommandError(
                    '--walk-forward-steps evaluates the RULE grid out of sample and needs --grid.'
                )
            self._handle_walk_forward(options)
        elif options['grid']:
            self._handle_grid(options)
        else:
            self._handle_single(options)

    # -- single configuration -------------------------------------------------
    def _handle_single(self, options):
        if options['backtest_id']:
            self._run_existing_row(options)
            return
        if options['model_version']:
            raise CommandError(
                '--model-version is a CPCV-only pin (it loads a non-serving artifact); '
                'add --cpcv-blocks or drop the flag.'
            )

        if not options['start'] or not options['end']:
            raise CommandError('--start and --end are required (or --backtest-id).')
        start = parse_iso_date(options['start'], '--start')
        end = parse_iso_date(options['end'], '--end')
        if end < start:
            raise CommandError('--end must be on or after --start.')

        knobs = {
            # Explicit rather than None, so a default RULE run and a --backtest-id run whose
            # row says RULE produce the same engine arguments.
            'model_type': str(options['model'] or 'RULE').upper(),
            'rebalance_frequency': options['frequency'],
            'max_positions': options['max_positions'],
            'momentum_lookback': options['momentum_lookback'],
            'absolute_momentum_filter': options['absolute_momentum_filter'],
            'defensive_asset': options['defensive_asset'],
            'initial_capital': options['initial_capital'],
            'commission_rate': options['commission_rate'],
            'min_commission': options['min_commission'],
            'slippage': options['slippage'],
            'liquidity_floor_multiple': options['liquidity_floor_multiple'],
            'min_trade_notional_fraction': options['min_trade_notional_fraction'],
            'drift_band_fraction': options['drift_band_fraction'],
            'liquidity_mode': options['liquidity_mode'],
            'adv_participation_cap': options['adv_participation_cap'],
        }
        overrides = {
            key: options[key]
            for key in ('weighting', 'kelly_fraction', 'kelly_cap', 'kelly_floor',
                         'kelly_lookback_trades', 'cash_yield_annual')
            if options.get(key) is not None
        }
        # ``dedup_track_index`` and ``max_per_category`` are rotation-layer knobs and reach
        # the engine through ``config_overrides`` like the rest of them: ``run_rotation_backtest``
        # takes an argument per *cost/identity* knob and reads the rest off
        # ``BacktestConfig``. Passing them as top-level arguments was a TypeError on the first
        # `--no-dedup-track-index` invocation, and the grid never noticed because it has always
        # routed ``weighting`` through ``config_overrides``.
        #
        # Both flags are tri-state: ``default=None`` means "not asked for", and the override must
        # stay absent so ``BacktestConfig`` decides. ``bool(options[...])`` looked equivalent and
        # was not -- ``bool(None)`` is False, so every plain CLI run silently selected the
        # dedup-off, cap-unset book instead of the production one, and a CLI number was not
        # comparable to a grid number of the same configuration.
        if options['dedup_track_index'] is not None:
            overrides['dedup_track_index'] = bool(options['dedup_track_index'])
        if options['max_per_category'] is not None:
            overrides['max_per_category'] = int(options['max_per_category'])
        model_type = knobs['model_type']
        name = options['name'] or self._default_name(knobs, options)

        # Resolve the sleeve to the code that will actually be used and prove it can carry
        # the cash leg over THIS window before any of the work starts. Passing the resolved
        # value back into ``knobs`` also means the row, the digest and the engine all see the
        # same string instead of three different readings of an empty flag.
        sleeve = str(knobs['defensive_asset'] or default_defensive_asset()).strip().upper()
        sleeve_problem = defensive_asset_problem(sleeve, start_date=start, end_date=end)
        if sleeve_problem:
            raise CommandError(sleeve_problem)
        knobs['defensive_asset'] = sleeve

        predictors = self._active_predictors(model_type)
        self.stdout.write(
            f'run: model={model_type} frequency={options["frequency"] or "MONTHLY(default)"} '
            f'positions={options["max_positions"] or "3(default)"} '
            f'lookback={options["momentum_lookback"] or "60(default)"} '
            f'absolute_filter={"off" if options["absolute_momentum_filter"] is False else "on(default)"} '
            f'weighting={options["weighting"] or "score(default)"} '
            f'dedup={"off" if overrides.get("dedup_track_index") is False else "on(default)"} '
            f'cap={overrides.get("max_per_category", "config-default")} '
            f'window={start}..{end} name={name if options["persist"] else "(not persisted)"}'
        )

        if options['queue']:
            if not options['persist']:
                raise CommandError(
                    '--queue hands a row to the worker, so it cannot be combined with --no-persist.'
                )
            self._enqueue(start, end, knobs, overrides, name)
            return

        result = self._call(
            run_kwargs={'start_date': start, 'end_date': end, 'name': name if options['persist'] else None,
                        **knobs, 'config_overrides': overrides},
            predictors=predictors,
        )
        self._report(result, title=name if options['persist'] else '(ephemeral run)',
                     sample_fills=int(options['sample_fills']))

    def _run_existing_row(self, options):
        """--backtest-id: the row's columns are the configuration, exactly as the Celery task reads them."""
        mixed = [flag for flag, key in (
            ('--start', 'start'), ('--end', 'end'), ('--model', 'model'), ('--frequency', 'frequency'),
            ('--max-positions', 'max_positions'), ('--momentum-lookback', 'momentum_lookback'),
            ('--weighting', 'weighting'), ('--defensive-asset', 'defensive_asset'),
            ('--initial-capital', 'initial_capital'),
            ('--commission-rate', 'commission_rate'), ('--min-commission', 'min_commission'),
            ('--slippage', 'slippage'), ('--liquidity-floor-multiple', 'liquidity_floor_multiple'),
            ('--min-trade-notional-fraction', 'min_trade_notional_fraction'),
            ('--drift-band-fraction', 'drift_band_fraction'),
            ('--liquidity-mode', 'liquidity_mode'),
            ('--adv-participation-cap', 'adv_participation_cap'),
            ('--no-absolute-momentum-filter', 'absolute_momentum_filter'),
        ) if options[key] not in (None, '')]
        if mixed:
            raise CommandError(
                '--backtest-id takes its configuration from the row; drop ' + ', '.join(mixed) + '.'
            )
        row = ETFRotationBacktest.objects.filter(pk=options['backtest_id']).first()
        if row is None:
            raise CommandError(f'ETFRotationBacktest {options["backtest_id"]} does not exist.')

        if options['queue']:
            task = tasks.queue_etf_rotation_run(row)
            self.stdout.write(self.style.SUCCESS(
                f'Queued run_etf_rotation_backtest(backtest_id={row.pk}) task={task.id} '
                f'queue={ETF_ROTATION_QUEUE}.'
            ))
            return

        predictors = self._active_predictors(str(row.model_type).upper())
        self.stdout.write(f're-running ETFRotationBacktest {row.pk} "{row.name}" from its own columns')
        result = self._call(run_kwargs={
            'start_date': row.start_date, 'end_date': row.end_date,
            'model_type': row.model_type, 'rebalance_frequency': row.rebalance_frequency,
            'max_positions': row.max_positions, 'momentum_lookback': row.momentum_lookback,
            'absolute_momentum_filter': row.absolute_momentum_filter,
            'defensive_asset': row.defensive_asset, 'initial_capital': float(row.initial_capital),
            'commission_rate': row.commission_rate, 'min_commission': float(row.min_commission),
            'slippage': row.slippage, 'liquidity_floor_multiple': row.liquidity_floor_multiple,
            'min_trade_notional_fraction': float(row.min_trade_notional_fraction),
            'drift_band_fraction': float(row.drift_band_fraction),
            'liquidity_mode': row.liquidity_mode,
            'adv_participation_cap': float(row.adv_participation_cap),
            'benchmark_code': row.benchmark_code,
            'config_overrides': {
                'walk_forward_steps': row.walk_forward_steps, 'weighting': row.weighting,
                'dedup_track_index': row.dedup_track_index, 'max_per_category': row.max_per_category,
            },
            'persist_to': row,
        }, predictors=predictors)
        self._report(result, title=f'{row.name} (id={row.pk})',
                     sample_fills=int(options['sample_fills']))

    def _enqueue(self, start, end, knobs, overrides, name):
        """Create the row from the requested knobs, then hand it to the 3600s task.

        Every knob has to be expressible as a column: the task rebuilds its config from the row,
        so a knob that lives only in ``overrides`` would re-default in the worker. ``weighting``
        joined ``_QUEUE_KNOB_FIELDS`` for exactly that reason.
        """
        unqueueable = sorted(set(overrides) - set(_QUEUE_KNOB_FIELDS))
        if unqueueable:
            raise CommandError(
                f'--queue cannot carry {", ".join(unqueueable)}: ETFRotationBacktest has no '
                'column for them, so the worker would read back its own default and run a '
                'different configuration than the one this command was asked to queue. Run '
                'inline (drop --queue) to use them.'
            )
        try:
            config = BacktestConfig(start_date=start, end_date=end, **{
                key: value for key, value in knobs.items() if value is not None
            })
            for key, value in overrides.items():
                setattr(config, key, value)
            config.validate()
        except BacktestConfigError as exc:
            raise CommandError(str(exc)) from exc

        values = {field: getattr(config, field) for field in _QUEUE_KNOB_FIELDS}
        values['model_type'] = config.model_type
        row = ETFRotationBacktest(name=name, parameters=config.to_parameters(), **values)
        row.save()
        task = tasks.queue_etf_rotation_run(row)
        self.stdout.write(self.style.SUCCESS(
            f'Created ETFRotationBacktest {row.pk} "{name}" (status={row.status}) and queued '
            f'tasks.run_etf_rotation_backtest({row.pk}) task={task.id} queue={ETF_ROTATION_QUEUE}.'
        ))

    @staticmethod
    def _default_name(knobs, options) -> str:
        """Config-derived name so an un-named run is still findable afterwards."""
        parts = [
            'rotation', str(knobs['model_type']).lower(),
            str(knobs['rebalance_frequency'] or 'monthly').lower(),
            f"p{knobs['max_positions'] or 3}",
            f"mom{knobs['momentum_lookback'] or 60}",
            'abs' if knobs['absolute_momentum_filter'] is not False else 'noabs',
        ]
        if options['weighting'] and options['weighting'] != 'equal':
            parts.append(options['weighting'])
        return '-'.join(parts)[:120]

    # -- ML predictors --------------------------------------------------------
    @staticmethod
    def _pinned_predictors(model_type: str, version: str, stdout) -> dict[str, Any]:
        """Resolve the *pinned* ML predictors for a CPCV rematch; never activates anything.

        Unlike ``_active_predictors`` this reads a specific READY row and deliberately
        ignores ``is_active``, so a candidate can be measured without being deployed. A
        version typo is a hard error (the loader raises) rather than a silent fallback to
        the serving model, which would grade a different artifact than the report names.
        """
        if model_type == 'RULE':
            return {}
        from apps.etf_rotation import train_lightgbm, train_lstm

        try:
            if model_type == 'LIGHTGBM':
                predictor = train_lightgbm.load_predictor_for_version(version)
                stdout.write(f'pinned predictor: ETF_LIGHTGBM <- {version} (is_active ignored)')
                return {'lgb_predictor': predictor}
            if model_type == 'LSTM':
                predictor = train_lstm.load_predictor_for_version(version)
                stdout.write(f'pinned predictor: ETF_LSTM <- {version} (is_active ignored)')
                return {'lstm_predictor': predictor}
            if model_type == 'ENSEMBLE':
                parts: dict[str, str] = {}
                for item in str(version).split(','):
                    if '=' in item:
                        key, value = item.split('=', 1)
                        parts[key.strip().lower()] = value.strip()
                lgb_version = parts.get('lgb', '')
                lstm_version = parts.get('lstm', '')
                if not lgb_version or not lstm_version:
                    raise ValueError(
                        'ENSEMBLE needs --model-version "lgb=<version>,lstm=<version>".'
                    )
                lgb = train_lightgbm.load_predictor_for_version(lgb_version)
                lstm = train_lstm.load_predictor_for_version(lstm_version)
                stdout.write(
                    f'pinned predictor: ENSEMBLE <- lgb={lgb_version} lstm={lstm_version} '
                    '(is_active ignored)'
                )
                return {'lgb_predictor': lgb, 'lstm_predictor': lstm}
        except (ImportError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        raise CommandError(f'no pinned-loader branch for model type {model_type!r}.')

    def _active_predictors(self, model_type: str) -> dict[str, Any]:
        """Resolve the active ``ModelVersion`` predictors up front, or fail.

        Reuses the training modules' own loaders (``train_lightgbm`` / ``train_lstm``) and
        passes what they return into the engine's ``lgb_predictor``/``lstm_predictor``
        slots. The engine would also resolve them lazily per session, but that defers the
        "nothing is trained" error until after the first chunk of a long run, and for
        ENSEMBLE a missing member is by design skipped and renormalised -- which would turn
        an untrained stack into a silent RULE backtest.
        """
        if model_type == 'RULE':
            return {}
        loaders = {
            'LIGHTGBM': ('apps.etf_rotation.train_lightgbm', 'ETF_LIGHTGBM', 'lgb_predictor'),
            'LSTM': ('apps.etf_rotation.train_lstm', 'ETF_LSTM', 'lstm_predictor'),
        }
        requested = ['LIGHTGBM', 'LSTM'] if model_type == 'ENSEMBLE' else [model_type]
        resolved: dict[str, Any] = {}
        missing: list[str] = []
        for name in requested:
            module_path, label, slot = loaders[name]
            try:
                module = importlib.import_module(module_path)
                predictor = module.load_active_predictor()
            except ImportError as exc:
                raise CommandError(f'{module_path} is not importable: {exc}') from exc
            if predictor is None:
                # A single absent ENSEMBLE member is a composition the engine handles by
                # renormalising, so refuse only when nothing ML is left to renormalise over.
                missing.append(label)
                continue
            resolved[slot] = predictor
            self.stdout.write(f'active predictor: {label} <- {module_path}.load_active_predictor()')
        if missing and model_type != 'ENSEMBLE':
            raise CommandError(
                f'--model {model_type} needs an active {", ".join(missing)}: no ModelVersion row '
                'with is_active=True and status=READY. Train it with '
                'manage.py train_etf_rotation_model --type lightgbm (or --type lstm), '
                'or run --model RULE.'
            )
        if len(resolved) != len(requested):
            raise CommandError(
                f'--model {model_type} has no ML leg to score with: no active READY '
                f'ModelVersion exists for '
                f'{", ".join(loaders[name][1] for name in requested)}, so the blend would '
                'degenerate to the RULE leg. Train a member with '
                'manage.py train_etf_rotation_model --type lightgbm or --type lstm.'
            )
        if model_type == 'ENSEMBLE' and missing:
            self.stdout.write(self.style.WARNING(
                f'ENSEMBLE member(s) {", ".join(missing)} have no active model; '
                'rotation_engine renormalises the blend over the remaining legs.'
            ))
        if model_type == 'ENSEMBLE':
            weights = self._ensemble_registry_weights()
            if weights is None:
                self.stdout.write(self.style.WARNING(
                    'No active ETF_ENSEMBLE ModelVersion row: the engine blends its members with '
                    'rotation_engine.DEFAULT_ENSEMBLE_WEIGHTS, not with registered rank-IC weights. '
                    'Run manage.py train_etf_rotation_model --type ensemble to register them.'
                ))
            else:
                self.stdout.write('ensemble registry weights: ' + ', '.join(
                    f'{label}={weight}' for label, weight in sorted(weights.items())
                ))
        return resolved

    @staticmethod
    def _ensemble_registry_weights() -> dict[str, Any] | None:
        from apps.etf_rotation.train_lightgbm import registered_versions

        for row in registered_versions(('ETF_ENSEMBLE',)):
            if row['is_active'] and row['status'] == 'READY' and row.get('weights'):
                return row['weights']
        return None

    # -- engine call ----------------------------------------------------------
    def _call(self, run_kwargs: dict[str, Any], predictors: dict[str, Any], data_source=None):
        try:
            started = time.monotonic()
            result = run_rotation_backtest(
                **run_kwargs, data_source=data_source, **predictors,
            )
        except (BacktestConfigError, BacktestDataUnavailable, RotationEngineUnavailable) as exc:
            raise CommandError(f'{type(exc).__name__}: {exc}') from exc
        elapsed = time.monotonic() - started
        if (result or {}).get('control'):
            raise CommandError(control_halt_message(result))
        result['_elapsed_seconds'] = elapsed
        return result

    # -- reporting ------------------------------------------------------------
    def _report(self, result: dict[str, Any], title: str, *, sample_fills: int = 5) -> None:
        metrics = result['metrics']
        parameters = result['parameters']
        report = result['report']
        curve = result['equity_curve']
        counters = report['counters']
        self.stdout.write(self.style.SUCCESS(f'COMPLETED {title} in {result.get("_elapsed_seconds", 0):.1f}s'))
        self.stdout.write(
            f'total_return={_pct(metrics["total_return"])} '
            f'annualized={_pct(metrics["annualized_return"])} '
            f'max_drawdown={_pct(metrics["max_drawdown"], signed=False)} '
            f'sharpe={_num(metrics["sharpe_ratio"])} calmar={_num(metrics["calmar_ratio"])} '
            f'win_rate={_pct(metrics["win_rate"])} '
            f'turnover={_num(metrics["turnover_annual"])}x/yr'
        )
        self.stdout.write(
            f'alpha_vs_{parameters["benchmark_code"]}={_pct(metrics["alpha_vs_benchmark"])} '
            f'beta={_num(metrics["beta_vs_benchmark"])} '
            f'info_ratio={_num(metrics["information_ratio"])} '
            f'benchmark_return={_pct(metrics["benchmark_return"])}'
        )
        self.stdout.write(
            f'total_fees={_yuan(metrics["total_fees"])} '
            f'slippage_cost={_yuan(metrics["total_slippage_cost"])} '
            f'total_cost={_yuan(metrics["total_cost"])} '
            f'final_equity={_yuan(metrics["final_equity"])}'
        )
        self.stdout.write(
            f"sessions={counters['sessions']} signals={counters['signals_generated']} "
            f"rebalances={counters.get('rebalances', 0)} "
            f"trades={counters['trades']} filled={counters['filled_trades']} "
            f"liquidity_rejected={counters['rejected_liquidity_fills']} "
            f"skipped_no_price={counters['skipped_no_price_fills']} "
            f"min_commission_bound={counters.get('min_commission_bound_fills', 0)} "
            f"band_suppressed={counters.get('band_suppressed_deltas', 0)} "
            f"capacity_scaled={counters.get('capacity_scaled_legs', 0)} "
            f"dividends={_yuan(counters['dividend_cash_yuan'])}"
        )
        turnover = report.get('turnover') or {}
        share = turnover.get('share') or {}
        self.stdout.write(
            f"turnover_split=drift {_pct(share.get('drift_share'))} / "
            f"discretionary {_pct(share.get('discretionary_share'))} / "
            f"other {_pct(share.get('other_share'))} "
            f"model_versions={report.get('model_versions')}"
        )
        for note in report.get('errors') or []:
            self.stdout.write(self.style.WARNING(f'engine note: {note}'))

        self._check_curve(curve, parameters, metrics)
        self._check_fills(result, parameters, limit=sample_fills)

    def _check_curve(self, curve, parameters, metrics) -> None:
        """Value conservation of the ledger, asserted on the printed output rather than in prose."""
        if not curve:
            self.stdout.write(self.style.ERROR('equity curve is empty -- nothing to check.'))
            return
        initial = float(parameters['initial_capital'])
        first = float(curve[0]['equity'])
        last = float(curve[-1]['equity'])
        final_equity = float(metrics['final_equity'] or 0.0)
        fee_model = parameters.get('fee_model') or {}
        stamp = fee_model.get('stamp_duty')
        self.stdout.write(
            f'curve first={first:.2f} (initial_capital={initial:.2f}, '
            f'{"MATCH" if math.isclose(first, initial, abs_tol=1e-6) else "MISMATCH"}) '
            f'curve last={last:.2f} final_equity={final_equity:.2f} '
            f'{"MATCH" if math.isclose(last, final_equity, abs_tol=1e-6) else "MISMATCH"} '
            f'fee_model.stamp_duty={stamp!r} '
            f'{"MATCH" if stamp == 0.0 else "MISMATCH (C15: ETFs pay no stamp duty)"}'
        )

    def _check_fills(self, result, parameters, *, limit: int) -> None:
        """Recompute a sample of fills from their own ledger numbers -- the C15 hand check."""
        fills = [item for item in result['trade_log'] if item.get('status') == 'FILLED']
        if not fills or limit <= 0:
            self.stdout.write(f'(no filled trades to sample; FILLED rows={len(fills)})')
            return
        rate = float(parameters['commission_rate'])
        minimum = float(parameters['min_commission'])
        slippage = float(parameters['slippage'])
        # Evenly spaced rather than the first N: an opening buy and a closing sell have
        # different fee shapes (the minimum bites only on small notionals) and a sample of
        # five from one end of a year would show only one of them.
        step = max(len(fills) // limit, 1)
        sample = [fills[index * step] for index in range(limit) if index * step < len(fills)]
        if fills[-1] not in sample:
            sample[-1] = fills[-1]
        self.stdout.write(f'--- {len(sample)} sample fills: fee hand check '
                          f'(commission = max(notional*{rate}, {minimum}), '
                          f'slippage_cost = |shares*raw_price*{slippage}|) ---')
        bad = 0
        for item in sample:
            notional = float(item['notional'])
            expected_commission = max(notional * rate, minimum)
            expected_slippage = abs(int(item['shares']) * float(item['raw_price']) * slippage)
            commission_delta = abs(float(item['commission']) - expected_commission)
            slippage_delta = abs(float(item['slippage_cost']) - expected_slippage)
            ok = commission_delta <= 0.01 and slippage_delta <= 0.01
            bad += 0 if ok else 1
            self.stdout.write(
                f"{'ok  ' if ok else 'BAD '} {item['date']} {item['side']:<4} {item['ts_code']:<12} "
                f"shares={item['shares']:>8} raw={float(item['raw_price']):>8.4f} "
                f"exec={float(item['exec_price']):>8.4f} notional={notional:>12.2f} "
                f"commission={float(item['commission']):>8.4f} (exp {expected_commission:>8.4f}) "
                f"slippage={float(item['slippage_cost']):>8.4f} (exp {expected_slippage:>8.4f}) "
                f"stamp={float(item['stamp_duty']):.1f} reason={item['reason']}"
            )
        self.stdout.write(
            self.style.SUCCESS(f'fee hand check: {len(sample) - bad}/{len(sample)} fills match')
            if bad == 0 else self.style.ERROR(f'fee hand check FAILED on {bad} of {len(sample)} fills')
        )

    # -- grid -----------------------------------------------------------------
    def _handle_grid(self, options):
        if options['model'] and str(options['model']).upper() != 'RULE':
            raise CommandError('--grid sweeps RULE only (C25); drop --model or run a single ML config.')
        if options['backtest_id']:
            raise CommandError('--grid builds one row per configuration; it cannot take --backtest-id.')
        if not options['start'] or not options['end']:
            raise CommandError('--grid requires --start and --end.')
        cost_flags = [flag for flag in GRID_COST_FLAGS if options.get(flag) is not None]
        if cost_flags:
            raise CommandError(
                '--grid runs every configuration under one execution/cost model; drop '
                + ', '.join('--' + flag.replace('_', '-') for flag in cost_flags)
                + ' or run the configuration once without --grid.'
            )
        prefix = str(options['grid_prefix'] or 'grid').strip() or 'grid'
        start = parse_iso_date(options['start'], '--start')
        end = parse_iso_date(options['end'], '--end')
        if end < start:
            raise CommandError('--end must be on or after --start.')

        combos = grid_combinations()
        self.stdout.write(
            f'planned RULE configurations: {len(combos)} = '
            f'{len(GRID_FREQUENCIES)} frequencies x {len(GRID_MAX_POSITIONS)} max_positions x '
            f'{len(GRID_ABSOLUTE_FILTERS)} absolute-momentum filters x '
            f'{len(GRID_MOMENTUM_LOOKBACKS)} momentum lookbacks x {len(GRID_WEIGHTINGS)} weightings'
        )
        if options['grid_limit'] and options['grid_limit'] < len(combos):
            combos = combos[:options['grid_limit']]
            self.stdout.write(self.style.WARNING(
                f'--grid-limit {options["grid_limit"]}: running a subset, not the full {GRID_EXPECTED_COMBOS}.'
            ))

        if options['grid_plan']:
            for index, combo in enumerate(combos, start=1):
                self.stdout.write(f'[{index:>3}/{len(combos)}] {grid_name(combo, prefix)} {combo}')
            self.stdout.write(self.style.SUCCESS(f'--grid-plan: {len(combos)} combinations listed, none executed.'))
            return

        source = CachedDataSource(DatabaseDataSource())
        rows: list[dict[str, Any]] = []
        failures: list[str] = []
        skipped = 0
        started = time.monotonic()
        for index, combo in enumerate(combos, start=1):
            name = grid_name(combo, prefix)
            row, created = ETFRotationBacktest.objects.get_or_create(
                name=name, defaults={'start_date': start, 'end_date': end},
            )
            if not created and row.status == ETFRotationBacktest.Status.COMPLETED and not options['grid_force']:
                skipped += 1
                rows.append(_row_result(row, combo))
                self.stdout.write(f'[{index}/{len(combos)}] {name}: already COMPLETED (id={row.pk}), skipped')
                continue
            try:
                result = run_rotation_backtest(
                    start_date=start, end_date=end, model_type='RULE',
                    rebalance_frequency=combo['rebalance_frequency'],
                    max_positions=combo['max_positions'],
                    momentum_lookback=combo['momentum_lookback'],
                    absolute_momentum_filter=combo['absolute_momentum_filter'],
                    config_overrides={'weighting': combo['weighting']},
                    persist_to=row, data_source=source,
                )
            except Exception as exc:
                # One unsellable universe or missing-panel config must not cost the other
                # 143; the engine has already marked the row FAILED with the message.
                failures.append(f'{name}: {type(exc).__name__}: {exc}')
                self.stdout.write(self.style.ERROR(f'[{index}/{len(combos)}] {name} FAILED: {exc}'))
                continue
            if result.get('control'):
                # The grid itself never requests a halt, but an operator can post one to a row
                # this command is running. There are no metrics to rank, so it is a failed
                # combination -- and indexing result['metrics'] here would be a KeyError.
                halt = control_halt_message(result)
                failures.append(f'{name}: {halt}')
                self.stdout.write(self.style.ERROR(f'[{index}/{len(combos)}] {name} HALTED: {halt}'))
                continue
            metrics = result['metrics']
            rows.append({**combo, 'name': name, 'id': row.pk, 'metrics': metrics,
                         'final_equity': metrics.get('final_equity'),
                         'trades': result['report']['counters']['filled_trades']})
            self.stdout.write(
                f"[{index}/{len(combos)}] {name}: id={row.pk} "
                f"alpha={_pct(metrics['alpha_vs_benchmark'])} sharpe={_num(metrics['sharpe_ratio'])} "
                f"total_return={_pct(metrics['total_return'])}"
            )

        self.stdout.write(self.style.SUCCESS(
            f'grid executed {len(rows) - skipped}/{len(combos)} configurations in '
            f'{time.monotonic() - started:.1f}s ({skipped} reused from persisted rows); '
            f'database queries behind the shared data seam: {source.db_queries}'
        ))
        if failures:
            self.stdout.write(self.style.ERROR(
                f'{len(failures)} grid run(s) failed: ' + '; '.join(failures[:10])
            ))
        self._render_grid_table(rows, top=max(int(options['grid_top'] or 10), 1), start=start, end=end)

    # -- RULE walk-forward ----------------------------------------------------
    def _handle_walk_forward(self, options):
        """Select RULE parameters inside each training window, then trade them OOS.

        Nothing here is persisted. A persisted grid row is a resumable configuration; a
        walk-forward fold is a one-shot measurement of the *selection process*, and
        writing 144 rows per fold would both pollute the grid's resume keys and invite a
        later reader to treat a training-window run as an OOS result.
        """
        if options['model'] and str(options['model']).upper() != 'RULE':
            raise CommandError('--walk-forward-steps sweeps RULE only; drop --model or use --model RULE.')
        if options['backtest_id']:
            raise CommandError('--walk-forward-steps runs ephemeral folds; it cannot take --backtest-id.')
        if options['queue']:
            raise CommandError('--walk-forward-steps is a CLI evaluation; --queue has no row to hand to a worker.')
        cost_flags = [flag for flag in GRID_COST_FLAGS if options.get(flag) is not None]
        if cost_flags:
            raise CommandError(
                '--walk-forward-steps runs every configuration under one execution/cost model; drop '
                + ', '.join('--' + flag.replace('_', '-') for flag in cost_flags)
                + ' or run the configuration once without --grid.'
            )
        if not options['start'] or not options['end']:
            raise CommandError('--walk-forward-steps requires --start and --end.')
        requested = int(options['walk_forward_steps'])
        if requested < 2:
            raise CommandError(
                f'--walk-forward-steps must be at least 2, got {requested}: one fold has nothing '
                'to be out of sample against.'
            )
        start = parse_iso_date(options['start'], '--start')
        end = parse_iso_date(options['end'], '--end')
        if end < start:
            raise CommandError('--end must be on or after --start.')

        source = CachedDataSource(DatabaseDataSource())
        try:
            folds = walk_forward_folds(source.sessions(start, end), requested)
        except WalkForwardError as exc:
            raise CommandError(f'walk-forward fold plan is invalid: {exc}') from exc
        if not folds:
            raise CommandError(
                f'the session calendar in {start}..{end} cannot seat one fold: '
                f'{WALK_FORWARD_MIN_TRAIN_SESSIONS} training sessions plus a '
                f'{WALK_FORWARD_LABEL_HORIZON}-session label tail plus one validation session are needed.'
            )
        if len(folds) < requested:
            self.stdout.write(self.style.WARNING(
                f'the panel supports {len(folds)} fold(s), not the {requested} requested; '
                'the plan is reported as-is rather than padded.'
            ))
        self.stdout.write(
            f'walk-forward plan: {len(folds)} expanding fold(s), '
            f'min_train={WALK_FORWARD_MIN_TRAIN_SESSIONS} sessions, '
            f'label_tail={WALK_FORWARD_LABEL_HORIZON} session'
        )
        for fold in folds:
            self.stdout.write(
                f'  fold {fold.fold_index + 1}: train {fold.train_start}..{fold.train_end} '
                f'({fold.purged_train_dates} date(s) purged), '
                f'validate {fold.valid_start}..{fold.valid_end}'
            )

        combos = grid_combinations()
        if options['grid_limit'] and options['grid_limit'] < len(combos):
            combos = combos[:options['grid_limit']]
            self.stdout.write(self.style.WARNING(
                f'--grid-limit {options["grid_limit"]}: selection runs on a subset of the '
                f'{GRID_EXPECTED_COMBOS} configurations, not the full sweep.'
            ))
        if options['grid_plan']:
            self.stdout.write(self.style.SUCCESS(
                f'--grid-plan: {len(folds)} fold(s) x {len(combos)} configuration(s), none executed.'
            ))
            return

        outcomes: list[dict[str, Any]] = []
        started = time.monotonic()
        for fold in folds:
            label = fold.fold_index + 1
            self.stdout.write(
                f'--- fold {label}/{len(folds)}: select on {fold.train_start}..{fold.train_end}, '
                f'trade {fold.valid_start}..{fold.valid_end} ---'
            )
            selection_rows, failures = self._select_fold_winner(fold, combos, source, label)
            try:
                winner = select_walk_forward_winner(selection_rows)
            except ValueError as exc:
                raise CommandError(f'fold {label}: {exc}') from exc
            self.stdout.write(
                f'  winner: {winner["name"]} train_sharpe={_num(winner["metrics"].get("sharpe_ratio"))} '
                f'train_rebalances={winner["rebalances"]}'
                + (f' ({len(failures)} configuration(s) failed)' if failures else '')
            )
            try:
                result = run_rotation_backtest(
                    start_date=fold.valid_start, end_date=fold.valid_end, model_type='RULE',
                    rebalance_frequency=winner['rebalance_frequency'],
                    max_positions=winner['max_positions'],
                    momentum_lookback=winner['momentum_lookback'],
                    absolute_momentum_filter=winner['absolute_momentum_filter'],
                    config_overrides={'weighting': winner['weighting']}, data_source=source,
                )
            except Exception as exc:
                raise CommandError(
                    f'fold {label}: the selected configuration failed out of sample: '
                    f'{type(exc).__name__}: {exc}'
                ) from exc
            counters = result['report']['counters']
            metrics = result['metrics']
            outcomes.append({
                'fold': fold.as_dict(),
                'winner': {key: winner[key] for key in REFERENCE_RULE_COMBO},
                'winner_name': winner['name'],
                'train_metrics': winner['metrics'],
                'train_rebalances': winner['rebalances'],
                'valid_metrics': metrics,
                'valid_rebalances': int(counters.get('rebalances', 0) or 0),
                'reference': is_reference_combo(winner),
            })
            self.stdout.write(
                f'  out of sample: return={_pct(metrics.get("total_return"))} '
                f'maxDD={_pct(metrics.get("max_drawdown"), signed=False)} '
                f'sharpe={_num(metrics.get("sharpe_ratio"))} '
                f'alpha={_pct(metrics.get("alpha_vs_benchmark"))} '
                f'rebalances={outcomes[-1]["valid_rebalances"]}'
            )

        self.stdout.write(
            f'--- RULE walk-forward: {len(folds)} fold(s), winner = highest Sharpe '
            '(lower turnover breaks a tie) ---'
        )
        for line in walk_forward_table(outcomes):
            self.stdout.write(line)
        hits = sum(1 for item in outcomes if item['reference'])
        self.stdout.write(
            f'reference configuration {grid_stem(REFERENCE_RULE_COMBO)} (row 256) was selected in '
            f'{hits}/{len(outcomes)} fold(s)'
        )
        thin = [
            (item['fold']['fold_index'] + 1, item['valid_rebalances'])
            for item in outcomes
            if item['valid_rebalances'] < WALK_FORWARD_MIN_FOLD_REBALANCES
        ]
        if thin:
            self.stdout.write(self.style.WARNING(
                'guardrail: fold(s) '
                + ', '.join(f'{index} ({count} rebalances)' for index, count in thin)
                + f' fall below {WALK_FORWARD_MIN_FOLD_REBALANCES}; rerun with '
                '--walk-forward-steps 4 for longer validation blocks.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'guardrail: every fold has at least {WALK_FORWARD_MIN_FOLD_REBALANCES} rebalances.'
            ))
        self.stdout.write(
            f'walk-forward executed {len(folds)} fold(s) in {time.monotonic() - started:.1f}s; '
            'no ETFRotationBacktest row was written.'
        )

    # -- combinatorial purged CV ------------------------------------------------
    def _handle_cpcv(self, options):
        """Measure one fixed configuration on combinatorial purged splits. Nothing is persisted.

        For RULE the reading is one out-of-sample number per held-out block, sliced off a
        single continuous run: no fitting happens, so the purge/embargo plan exists for the
        *fitted* consumer (``train_etf_rotation_model``) and printing its counts keeps the
        two paths from drifting.

        An ML arm is allowed only with ``--model-version``, which loads a READY artifact by
        name and never touches ``is_active``. The backtest then scores with that pinned
        artifact, and the per-block rank IC is printed next to the portfolio metrics. This
        is the 10a entry point, not a deployment path.

        Two constraints are enforced rather than documented: the window may not reach the
        locked holdout (``2024-01-01``), and the command refuses to rank configurations --
        it measures whatever configuration the flags describe, and the caller must not run
        it per-configuration and pick the best block reading. That would turn the tuning
        window into a second holdout, which is exactly what §7.7 closed.
        """
        if options['grid'] or options['walk_forward_steps'] is not None:
            raise CommandError(
                '--cpcv-blocks measures one fixed configuration; drop --grid and --walk-forward-steps.'
            )
        if options['backtest_id']:
            raise CommandError('--cpcv-blocks runs ephemeral splits; it cannot take --backtest-id.')
        if options['queue']:
            raise CommandError(
                '--cpcv-blocks is a CLI measurement; --queue has no row to hand to a worker.'
            )
        model_type = str(options['model'] or 'RULE').upper()
        pinned_version = str(options['model_version'] or '').strip()
        if model_type != 'RULE':
            # ML is allowed only with a pinned version: resolving "the active model" would
            # make the report name whatever happens to be active when it re-runs, and the
            # gate forbids activating a candidate just to measure it.
            if not pinned_version:
                raise CommandError(
                    f'--model {model_type} needs --model-version: an ML rematch must pin the '
                    'READY artifact it scores with, not resolve "the currently active one".'
                )
        elif pinned_version:
            raise CommandError('--model-version only applies to an ML --model with --cpcv-blocks.')
        if options['name']:
            raise CommandError('--cpcv-blocks is ephemeral; drop --name.')
        if not options['start'] or not options['end']:
            raise CommandError('--cpcv-blocks requires --start and --end.')
        start = parse_iso_date(options['start'], '--start')
        end = parse_iso_date(options['end'], '--end')
        if end < start:
            raise CommandError('--end must be on or after --start.')
        if end >= LOCKED_HOLDOUT_START:
            raise CommandError(
                f'--cpcv-blocks refuses a window reaching {LOCKED_HOLDOUT_START}: the holdout was spent '
                'once (plan §7.7) and is closed to further queries.'
            )
        pinned_predictors = self._pinned_predictors(model_type, pinned_version, self.stdout)

        n_blocks = int(options['cpcv_blocks'])
        test_blocks = int(options['cpcv_test_blocks'])
        embargo = int(options['cpcv_embargo'])
        source = CachedDataSource(DatabaseDataSource())
        sessions = source.sessions(start, end)
        if not sessions:
            raise CommandError(f'no ETF bars exist in {start}..{end}.')
        # A requested boundary is a calendar date and may not be a session: 2023-12-31 is a
        # Sunday, so the panel ends 2023-12-29. Build the plan over the sessions that exist
        # (the walk-forward path does the same) and print both windows, so a report never
        # claims a window it did not measure.
        panel_start, panel_end = sessions[0], sessions[-1]
        if (panel_start, panel_end) != (start, end):
            self.stdout.write(
                f'panel: {panel_start}..{panel_end} (requested {start}..{end}); the fold '
                'boundaries follow the sessions that exist.'
            )
        try:
            folds = combinatorial_purged_folds(
                panel_start, panel_end, n_blocks, test_blocks,
                horizon_days=CPCV_LABEL_HORIZON, embargo_days=embargo, trade_dates=sessions,
            )
            audit = audit_combinatorial_folds(folds, sessions, CPCV_LABEL_HORIZON, embargo)
        except WalkForwardError as exc:
            raise CommandError(f'CPCV fold plan is invalid: {exc}') from exc
        if not folds:
            raise CommandError(
                f'the session calendar in {start}..{end} cannot seat {n_blocks} blocks; widen the window '
                'or lower --cpcv-blocks.'
            )

        blocks = sorted({block for fold in folds for block in fold.validation_blocks})
        self.stdout.write(
            f'CPCV plan: {n_blocks} blocks x {test_blocks} held out = {len(folds)} split(s), '
            f'{len(blocks)} distinct block(s), purge={CPCV_LABEL_HORIZON} session(s), embargo={embargo}; '
            f'window {panel_start}..{panel_end}'
        )
        for item in audit[:3]:
            self.stdout.write(
                f"  fold {item['fold_index']}: valid blocks {item['validation_blocks']} "
                f"train={item['train_dates_kept']} purged={item['purged_train_dates']} "
                f"embargoed={item['embargoed_train_dates']}"
            )
        if len(audit) > 3:
            self.stdout.write(f'  ... {len(audit) - 3} more split(s) audited')

        knobs = {
            'model_type': model_type,
            'rebalance_frequency': options['frequency'],
            'max_positions': options['max_positions'],
            'momentum_lookback': options['momentum_lookback'],
            'absolute_momentum_filter': options['absolute_momentum_filter'],
            'defensive_asset': options['defensive_asset'],
            'initial_capital': options['initial_capital'],
            'commission_rate': options['commission_rate'],
            'min_commission': options['min_commission'],
            'slippage': options['slippage'],
            'liquidity_floor_multiple': options['liquidity_floor_multiple'],
            'min_trade_notional_fraction': options['min_trade_notional_fraction'],
            'drift_band_fraction': options['drift_band_fraction'],
            'liquidity_mode': options['liquidity_mode'],
            'adv_participation_cap': options['adv_participation_cap'],
        }
        overrides = {
            key: options[key]
            for key in ('weighting', 'kelly_fraction', 'kelly_cap', 'kelly_floor',
                        'kelly_lookback_trades', 'cash_yield_annual')
            if options.get(key) is not None
        }
        if options['dedup_track_index'] is not None:
            overrides['dedup_track_index'] = bool(options['dedup_track_index'])
        if options['max_per_category'] is not None:
            overrides['max_per_category'] = int(options['max_per_category'])
        sleeve = str(knobs['defensive_asset'] or default_defensive_asset()).strip().upper()
        sleeve_problem = defensive_asset_problem(sleeve, start_date=start, end_date=end)
        if sleeve_problem:
            raise CommandError(sleeve_problem)
        knobs['defensive_asset'] = sleeve

        started = time.monotonic()
        result = self._call(
            run_kwargs={
                'start_date': start, 'end_date': end, 'name': None,
                **knobs, 'config_overrides': overrides,
            },
            predictors=pinned_predictors,
            data_source=source,
        )
        points = result.get('equity_curve') or []
        rebalance_log = list((result.get('report') or {}).get('rebalances') or [])
        facets = []
        for ordinal, (block_start, block_end) in enumerate(blocks, start=1):
            facet = self._cpcv_block_facet(
                points, block_start, block_end, ordinal, rebalance_log,
            )
            if facet is not None:
                facets.append(facet)
        if not facets:
            raise CommandError('the run produced no equity points inside any held-out block.')

        self.stdout.write(
            f'--- CPCV: one fixed configuration read off {len(facets)} held-out block(s) ---'
        )
        self.stdout.write(
            '  block  start        end          sessions  rebal  return     maxDD      sharpe'
        )
        for facet in facets:
            thin = ' thin' if facet['rebalances'] < WALK_FORWARD_MIN_FOLD_REBALANCES else ''
            self.stdout.write(
                f"  {facet['block']:<5}  {facet['start']}  {facet['end']}  "
                f"{facet['sessions']:>8}  {facet['rebalances']:>5}  "
                f"{_pct(facet['total_return']):>9}  "
                f"{_pct(facet['max_drawdown'], signed=False):>8}  {_num(facet['sharpe_ratio']):>7}"
                f'{thin}'
            )
        thin_blocks = [
            facet['block'] for facet in facets
            if facet['rebalances'] < WALK_FORWARD_MIN_FOLD_REBALANCES
        ]
        if thin_blocks:
            self.stdout.write(self.style.WARNING(
                'guardrail: block(s) '
                + ', '.join(str(block) for block in thin_blocks)
                + f' fall below {WALK_FORWARD_MIN_FOLD_REBALANCES} rebalances; their readings '
                'are thin and are not evidence on their own.'
            ))
        sharpe_dist = _distribution([item['sharpe_ratio'] for item in facets])
        return_dist = _distribution([item['total_return'] for item in facets])
        drawdown_dist = _distribution([item['max_drawdown'] for item in facets])
        self.stdout.write(
            f"  sharpe  min={_num(sharpe_dist.get('min'))} q1={_num(sharpe_dist.get('q1'))} "
            f"median={_num(sharpe_dist.get('median'))} q3={_num(sharpe_dist.get('q3'))} "
            f"max={_num(sharpe_dist.get('max'))} positive={_pct(sharpe_dist.get('positive_share'))}"
        )
        self.stdout.write(
            f"  return  min={_pct(return_dist.get('min'))} q1={_pct(return_dist.get('q1'))} "
            f"median={_pct(return_dist.get('median'))} q3={_pct(return_dist.get('q3'))} "
            f"max={_pct(return_dist.get('max'))} positive={_pct(return_dist.get('positive_share'))}"
        )
        self.stdout.write(
            f"  maxDD   min={_pct(drawdown_dist.get('min'), signed=False)} "
            f"median={_pct(drawdown_dist.get('median'), signed=False)} "
            f"max={_pct(drawdown_dist.get('max'), signed=False)}"
        )
        if model_type in ('LIGHTGBM', 'LSTM'):
            ic_predictor = (
                pinned_predictors.get('lgb_predictor')
                or pinned_predictors.get('lstm_predictor')
            )
            ic_rows = []
            self.stdout.write('  rank IC (signal quality, not alpha):')
            for ordinal, (block_start, block_end) in enumerate(blocks, start=1):
                try:
                    ic = rank_ic.block_rank_ic(
                        ic_predictor, block_start=block_start, block_end=block_end,
                    )
                except Exception as exc:  # noqa: BLE001 - a measurement aid must not kill the run
                    self.stdout.write(self.style.WARNING(
                        f'    block {ordinal:<2} rank IC failed: {type(exc).__name__}: {exc}'
                    ))
                    continue
                ic_rows.append((ordinal, ic))
                self.stdout.write(
                    f"    block {ordinal:<2} dates={ic['dates']:>4} mean_ic={_num(ic['mean_ic'])}"
                )
            if ic_rows:
                ic_dist = _distribution([ic['mean_ic'] for _, ic in ic_rows])
                self.stdout.write(
                    f"    mean_ic min={_num(ic_dist.get('min'))} "
                    f"median={_num(ic_dist.get('median'))} max={_num(ic_dist.get('max'))} "
                    f"positive={_pct(ic_dist.get('positive_share'))}"
                )
        elif model_type == 'ENSEMBLE':
            self.stdout.write(
                '  rank IC: not measured for ENSEMBLE -- the blend is composed inside the '
                'engine, so there is no single scorer to correlate.'
            )
        self.stdout.write(
            'reading rule: this is a distribution of block readings for ONE configuration, not a '
            'ranking. Do not run it per configuration and pick the best block -- that spends the '
            'tuning window like a holdout.'
        )
        self.stdout.write(
            f'CPCV measured {len(facets)} block(s) in {time.monotonic() - started:.1f}s; '
            'no ETFRotationBacktest row was written and no ModelVersion was activated.'
        )

    @staticmethod
    def _cpcv_block_facet(points, block_start, block_end, ordinal: int, rebalances=None):
        """One held-out block's curve slice, measured from the mark before it opens.

        The base point is the last equity before ``block_start``, so the block's first
        session return is counted instead of being lost to a reset. The block is otherwise
        self-contained: its max drawdown peaks at its own start, which is what makes the
        readings comparable across blocks. ``rebalances`` is the run's per-plan log; a plan
        is attributed to the block its ``signal_date`` sits in, which is when the selection
        layer made the decision.
        """
        start_iso, end_iso = block_start.isoformat(), block_end.isoformat()
        inside = [point for point in points if start_iso <= str(point.get('date')) <= end_iso]
        if not inside:
            return None
        before = [point for point in points if str(point.get('date')) < start_iso]
        base_equity = float((before[-1] if before else inside[0])['equity'])
        equity = [base_equity] + [float(point['equity']) for point in inside]
        rebalance_count = sum(
            1 for item in (rebalances or [])
            if start_iso <= str(item.get('signal_date')) <= end_iso
        )
        return {
            'block': ordinal,
            'start': inside[0]['date'],
            'end': inside[-1]['date'],
            'sessions': len(inside),
            'rebalances': rebalance_count,
            'total_return': (equity[-1] / equity[0] - 1.0) if equity[0] > 0 else None,
            'max_drawdown': finance_metrics.max_drawdown(equity),
            'sharpe_ratio': finance_metrics.sharpe_ratio(finance_metrics.returns_from_equity(equity)),
        }

    def _select_fold_winner(self, fold, combos, source, label):
        """Run the grid on the fold's training window only and return its scored rows."""
        selection_rows: list[dict[str, Any]] = []
        failures: list[str] = []
        for index, combo in enumerate(combos, start=1):
            name = grid_stem(combo)
            try:
                result = run_rotation_backtest(
                    start_date=fold.train_start, end_date=fold.train_end, model_type='RULE',
                    rebalance_frequency=combo['rebalance_frequency'],
                    max_positions=combo['max_positions'],
                    momentum_lookback=combo['momentum_lookback'],
                    absolute_momentum_filter=combo['absolute_momentum_filter'],
                    config_overrides={'weighting': combo['weighting']}, data_source=source,
                )
            except Exception as exc:
                # One config a short training window cannot score must not cost the fold;
                # it simply is not a candidate. If nothing survives, the caller refuses.
                failures.append(f'{name}: {type(exc).__name__}: {exc}')
                self.stdout.write(self.style.ERROR(f'  [{index}/{len(combos)}] {name} FAILED: {exc}'))
                continue
            counters = result['report']['counters']
            selection_rows.append({
                **combo, 'name': name, 'metrics': result['metrics'],
                'rebalances': int(counters.get('rebalances', 0) or 0),
            })
            if index % 24 == 0 or index == len(combos):
                best = min(selection_rows, key=walk_forward_rank_key)
                self.stdout.write(
                    f'  [{index}/{len(combos)}] best so far: {best["name"]} '
                    f'sharpe={_num(best["metrics"].get("sharpe_ratio"))} '
                    f'turnover={_num(best["metrics"].get("turnover_annual"))}'
                )
        if not selection_rows:
            detail = '; '.join(failures[:3]) or 'no configuration produced metrics'
            raise CommandError(f'fold {label}: no configuration could be scored on the training window: {detail}')
        return selection_rows, failures

    def _render_grid_table(self, rows: list[dict[str, Any]], *, top: int, start: date, end: date) -> None:
        """Ranked by alpha vs the benchmark, then Sharpe -- the plan's two decision columns."""
        if not rows:
            self.stdout.write(self.style.WARNING('no grid row produced metrics; nothing to rank.'))
            return
        ranked = sorted(rows, key=_sort_key, reverse=True)
        self.stdout.write(
            f'--- top {min(top, len(ranked))} of {len(ranked)} RULE configs, '
            'ranked by alpha vs benchmark then Sharpe ---'
        )
        self.stdout.write(
            f'{"#":>3} {"frequency":<9} {"pos":>3} {"lookback":>8} {"abs":>4} {"weighting":<9} '
            f'{"total":>9} {"annual":>9} {"maxDD":>8} {"sharpe":>7} {"calmar":>7} '
            f'{"alpha":>9} {"IR":>7} {"turnover":>9} {"fees":>10} {"id":>5}'
        )
        for index, row in enumerate(ranked[:top], start=1):
            metrics = row['metrics']
            self.stdout.write(
                f'{index:>3} {row["rebalance_frequency"]:<9} {row["max_positions"]:>3} '
                f'{row["momentum_lookback"]:>8} {"on" if row["absolute_momentum_filter"] else "off":>4} '
                f'{row["weighting"]:<9} {_pct(metrics.get("total_return")):>9} '
                f'{_pct(metrics.get("annualized_return")):>9} {_pct(metrics.get("max_drawdown"), signed=False):>8} '
                f'{_num(metrics.get("sharpe_ratio")):>7} {_num(metrics.get("calmar_ratio")):>7} '
                f'{_pct(metrics.get("alpha_vs_benchmark")):>9} {_num(metrics.get("information_ratio")):>7} '
                f'{_num(metrics.get("turnover_annual")):>9} {_yuan(metrics.get("total_fees")):>10} '
                f'{row.get("id") or 0:>5}'
            )
        self.stdout.write('--- top 3 configurations to evaluate the ML rankers on (C25) ---')
        for row in ranked[:3]:
            self.stdout.write(_command_line(row, start, end))
        self.stdout.write(
            '# same three lines with --model RULE replaced by --model LIGHTGBM / LSTM / ENSEMBLE '
            'and --name changed accordingly; --grid never sweeps ML.'
        )


def _row_result(row: ETFRotationBacktest, combo: dict[str, Any]) -> dict[str, Any]:
    """Grid table entry rebuilt from an already-persisted row (resumed run)."""
    return {
        **combo, 'name': row.name, 'id': row.pk,
        'metrics': {
            'alpha_vs_benchmark': row.alpha_vs_benchmark, 'sharpe_ratio': row.sharpe_ratio,
            'total_return': row.total_return, 'annualized_return': row.annualized_return,
            'max_drawdown': row.max_drawdown, 'calmar_ratio': row.calmar_ratio,
            'turnover_annual': row.turnover_annual, 'total_fees': row.total_fees,
            'final_equity': (row.equity_curve or [{}])[-1].get('equity'),
            'information_ratio': row.information_ratio,
        },
        'final_equity': (row.equity_curve or [{}])[-1].get('equity'),
        'trades': len([item for item in (row.trade_log or []) if item.get('status') == 'FILLED']),
    }


def _sort_key(row: dict[str, Any]):
    """Alpha vs benchmark, then Sharpe; a run with neither sorts last rather than first."""
    def value(key: str) -> float:
        raw = row['metrics'].get(key)
        return float(raw) if raw is not None and math.isfinite(float(raw)) else -math.inf
    return (value('alpha_vs_benchmark'), value('sharpe_ratio'))


def _command_line(combo: dict[str, Any], start: date, end: date) -> str:
    """A grid winner as a single-config command, ready to paste for the ML follow-up."""
    flags = ' '.join(filter(None, (
        f'--frequency {combo["rebalance_frequency"]}',
        f'--max-positions {combo["max_positions"]}',
        f'--momentum-lookback {combo["momentum_lookback"]}',
        '' if combo['absolute_momentum_filter'] else '--no-absolute-momentum-filter',
        f'--weighting {combo["weighting"]}',
    )))
    return (
        'DJANGO_READ_DOT_ENV_FILE=True python manage.py run_etf_rotation_backtest '
        f'--start {start.isoformat()} --end {end.isoformat()} {flags} '
        f'--model RULE --name "ml-{grid_stem(combo)}"'
    )


def _pct(value: Any, signed: bool = True) -> str:
    """Ratios are stored as fractions (``finance_metrics`` never multiplies by 100).

    ``signed=False`` for magnitudes like drawdown, where a leading ``+`` reads as a gain.
    """
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return 'n/a'
    return f'{float(value) * 100:{"+" if signed else ""}.2f}%'


def _num(value: Any, digits: int = 4) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return 'n/a'
    return f'{float(value):.{digits}f}'


def _yuan(value: Any) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return 'n/a'
    return f'{float(value):,.2f}'


def _distribution(values: list) -> dict:
    """Min/quartiles/median/max and the positive share of a block-reading sample.

    Quantiles are taken at the nearest rank of the sorted sample rather than interpolated:
    with six or fifteen block readings, an interpolated quartile reads as precision the
    data does not have. ``None`` readings are dropped (a block too short to score) rather
    than counted as zero, which would make an unscoreable split look flat.
    """
    clean = sorted(float(value) for value in values if value is not None)
    if not clean:
        return {}
    count = len(clean)

    def rank(fraction: float) -> float:
        return clean[min(count - 1, max(0, int(round(fraction * (count - 1)))))]

    return {
        'count': count,
        'min': clean[0],
        'q1': rank(0.25),
        'median': rank(0.5),
        'q3': rank(0.75),
        'max': clean[-1],
        'positive_share': sum(1 for value in clean if value > 0) / count,
    }
