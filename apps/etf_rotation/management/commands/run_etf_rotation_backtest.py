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

from apps.etf_rotation import tasks
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

#: Row columns the queued task reads back as its configuration. Same set as
#: ``tasks.run_etf_rotation_backtest``; a column added to the model belongs here too or a
#: queued run silently inherits the model default instead of what this command intended.
_QUEUE_KNOB_FIELDS = (
    'start_date', 'end_date', 'model_type', 'rebalance_frequency', 'max_positions',
    'momentum_lookback', 'absolute_momentum_filter', 'defensive_asset', 'initial_capital',
    'commission_rate', 'min_commission', 'slippage', 'liquidity_floor_multiple',
    'min_trade_notional_fraction', 'drift_band_fraction', 'liquidity_mode',
    'adv_participation_cap', 'benchmark_code', 'walk_forward_steps', 'weighting',
    'dedup_track_index', 'max_per_category',
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

    def handle(self, *args, **options):
        # Returns nothing on purpose: Django writes a truthy return value to stdout, and a
        # dict here would raise inside ``BaseCommand.execute``.
        if options['grid']:
            self._handle_grid(options)
        else:
            self._handle_single(options)

    # -- single configuration -------------------------------------------------
    def _handle_single(self, options):
        if options['backtest_id']:
            self._run_existing_row(options)
            return

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
            f'positions={options["max_positions"] or "5(default)"} '
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
            f"p{knobs['max_positions'] or 5}",
            f"mom{knobs['momentum_lookback'] or 60}",
            'abs' if knobs['absolute_momentum_filter'] is not False else 'noabs',
        ]
        if options['weighting'] and options['weighting'] != 'equal':
            parts.append(options['weighting'])
        return '-'.join(parts)[:120]

    # -- ML predictors --------------------------------------------------------
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
        cost_flags = [
            flag for flag in (
                'commission_rate', 'min_commission', 'slippage',
                'liquidity_floor_multiple', 'min_trade_notional_fraction',
                'drift_band_fraction', 'liquidity_mode', 'adv_participation_cap',
            ) if options.get(flag) is not None
        ]
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
