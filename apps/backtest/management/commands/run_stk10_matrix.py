"""Create (and optionally execute) the pre-registered STK-10 measurement matrix.

STK-10 quantifies how much of the existing CSI500 backtest reading is untradeable
today: the *permission price* (official benchmark minus executable benchmark) and
the *ST cost* (ex-board minus ex-board+ST). It is DESCRIPTIVE -- no acceptance
criteria, no verdict, and it must not change any default on its own.

Why this command exists instead of ``rerun_backtests_for_comparison``: that command
clones AND executes in a single invocation, so a clone's parameters cannot be edited
between creation and execution, and it has no flags for the three axes this matrix
varies. STK-10 needs 18 rows created up front with exact parameters, then executed
in a controlled order.

The cells are 3 horizons x 3 universe arms x 2 execution models. Within a horizon the
two execution models of one arm are created ADJACENT so they share a signal surface:
``execution_model`` changes when a fill happens, not what was predicted. Caches are
cleared between horizon groups, mirroring ``run_core_backtest_matrix``.

Creation is the default; execution requires ``--execute``. ``--dry-run`` prints the
matrix without touching the database.
"""
from datetime import date
from unittest.mock import patch

from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.utils import InterfaceError, OperationalError
from django.utils import timezone

from apps.backtest.models import BacktestRun
from apps.backtest.tasks import clear_backtest_process_caches, run_backtest
from apps.core.date_contract import (
    ASSET_STOCKS,
    STAGE_BACKTEST,
    STOCK_BACKTEST_START,
    enforce_date_contract,
)
from apps.markets.universe_policy import POLICY_DEFAULT, POLICY_RESEARCH_FULL
from apps.prediction.models_lightgbm import LightGBMModelArtifact

#: The pre-registered model pin. STK-10 measures the EXISTING reading, so the fleet
#: is fixed -- a retrain between registration and launch invalidates the measurement
#: rather than improving it. Refuse to create rows if the active artifact has moved.
PINNED_ARTIFACT_VERSIONS = {3: 'lgb-3d-2026-11-09', 7: 'lgb-7d-2026-11-09', 30: 'lgb-30d-2026-11-09'}

#: CORE_PROFILES "base" selection shape per horizon, matching the CMP pre-registration.
HORIZON_PROFILES = {
    3: {'top_n': 5, 'up_threshold': 0.45, 'holding_period_days': 5},
    7: {'top_n': 5, 'up_threshold': 0.45, 'holding_period_days': 10},
    30: {'top_n': 3, 'up_threshold': 0.45, 'holding_period_days': 20},
}

#: (universe_policy, st_layer) arms. There is no research_full+ST cell: research
#: presets force the ST exclusion layer off, so that combination is not expressible.
UNIVERSE_ARMS = (
    (POLICY_RESEARCH_FULL, False),
    (POLICY_DEFAULT, False),
    (POLICY_DEFAULT, True),
)

EXECUTION_MODELS = ('next_day_open', 'same_day_close')

PIT_INDEX_CODES = ['000905.SH']

#: 20 sessions at the measured ~71 s/session is ~1,420 s, under the 1,800 s Celery
#: soft limit. 60 would be ~4,260 s and a certain hard-limit kill on the queued path.
CHUNK_TRADING_DAYS = 20


def _cell_parameters(horizon, arm, execution_model, artifact, cache_scope):
    policy, st_layer = arm
    profile = HORIZON_PROFILES[horizon]
    return {
        'candidate_mode': 'top_n',
        'prediction_source': 'lightgbm',
        'horizon_days': horizon,
        'top_n': profile['top_n'],
        'up_threshold': profile['up_threshold'],
        'holding_period_days': profile['holding_period_days'],
        'top_n_metric': f'up_prob_{horizon}d',
        'capital_fraction_per_entry': 0.2,
        'use_macro_context': True,
        'enable_stop_target_exit': False,
        'lightgbm_model_artifact_id': artifact.id,
        'lightgbm_model_artifact_version': artifact.version,
        'lightgbm_inference_backend': 'cpu_serial',
        'lightgbm_batch_size': 256,
        'chunk_trading_days': CHUNK_TRADING_DAYS,
        'slippage_bps': 5,
        'pit_index_codes': list(PIT_INDEX_CODES),
        'universe_policy': policy,
        'st_layer': st_layer,
        'execution_model': execution_model,
        'matrix_signal_cache_key': cache_scope,
        # allow_pre_contract_history is deliberately ABSENT: the window starts exactly
        # at STOCK_BACKTEST_START, so the override is unnecessary and would label every
        # run research-only.
    }


def build_matrix(start_date, end_date, horizons, cache_scope, artifacts_by_horizon):
    """The 18 cells, ordered so each arm's two execution models are adjacent."""
    cells = []
    for horizon in horizons:
        for arm in UNIVERSE_ARMS:
            for execution_model in EXECUTION_MODELS:
                cells.append({
                    'horizon': horizon,
                    'arm': arm,
                    'execution_model': execution_model,
                    'parameters': _cell_parameters(
                        horizon, arm, execution_model,
                        artifacts_by_horizon[horizon], cache_scope,
                    ),
                })
    return cells


def _cell_name(prefix, cell):
    policy, st_layer = cell['arm']
    st_token = 'st' if st_layer else 'nost'
    return (
        f"{prefix}-h{cell['horizon']}-{policy}-{st_token}-{cell['execution_model']}"
    )


def resolve_pinned_artifacts(horizons):
    """The active READY artifact per horizon, refusing any that moved off the pin."""
    artifacts = {}
    for horizon in horizons:
        artifact = LightGBMModelArtifact.objects.filter(
            horizon_days=horizon,
            status=LightGBMModelArtifact.Status.READY,
            is_active=True,
        ).order_by('-trained_at', '-created_at').first()
        if artifact is None:
            raise CommandError(f'No active READY LightGBM artifact for horizon {horizon}.')
        expected = PINNED_ARTIFACT_VERSIONS[horizon]
        if artifact.version != expected:
            raise CommandError(
                f'horizon {horizon}: active artifact is {artifact.version!r} but the '
                f'pre-registration pins {expected!r}. STK-10 measures the existing '
                f'reading -- re-register with a new digest before using a new fleet.'
            )
        artifacts[horizon] = artifact
    return artifacts


class Command(BaseCommand):
    help = 'Create the pre-registered STK-10 executable-universe matrix (18 cells); --execute runs it inline.'

    def add_arguments(self, parser):
        parser.add_argument('--start-date', default=STOCK_BACKTEST_START.isoformat(),
                            help='Window start (YYYY-MM-DD). Defaults to the date contract boundary.')
        parser.add_argument('--end-date', required=True, help='Window end (YYYY-MM-DD).')
        parser.add_argument('--preregistration-digest', required=True,
                            help='sha256[:12] of the STK-10 pre-registration. Stamped into every run so '
                                 'no reading exists without its registration.')
        parser.add_argument('--horizons', default='3,7,30', help='Comma-separated horizons to measure.')
        parser.add_argument('--name-prefix', default='stk10', help='Prefix for BacktestRun names.')
        parser.add_argument('--cache-scope', default=None,
                            help='matrix_signal_cache_key scope. Defaults to a per-launch stamp.')
        parser.add_argument('--initial-capital', default='200000.00', help='Initial capital per run.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print the matrix without creating any rows.')
        parser.add_argument('--execute', action='store_true',
                            help='Execute the created runs inline after creation. Absent means create only.')

    def handle(self, *args, **options):
        start_date = date.fromisoformat(str(options['start_date']))
        end_date = date.fromisoformat(str(options['end_date']))
        if end_date < start_date:
            raise CommandError('end-date must be on or after start-date.')

        # No allow_pre_contract override: the contract must accept this window as-is.
        enforce_date_contract(
            asset_class=ASSET_STOCKS,
            stage=STAGE_BACKTEST,
            requested_start=start_date,
            allow_pre_contract=False,
        )

        digest = str(options['preregistration_digest'] or '').strip()
        if len(digest) != 12:
            raise CommandError('preregistration-digest must be the 12-char sha256 prefix.')

        horizons = []
        for token in str(options['horizons']).split(','):
            token = token.strip()
            if not token:
                continue
            value = int(token)
            if value not in HORIZON_PROFILES:
                raise CommandError(f'unsupported horizon {value}; valid: {sorted(HORIZON_PROFILES)}.')
            horizons.append(value)
        if not horizons:
            raise CommandError('horizons must name at least one horizon.')

        prefix = str(options['name_prefix'] or '').strip()
        if not prefix:
            raise CommandError('name-prefix cannot be blank.')

        if options['dry_run']:
            artifacts = {h: LightGBMModelArtifact(
                id=0, version=PINNED_ARTIFACT_VERSIONS[h], horizon_days=h,
            ) for h in horizons}
        else:
            artifacts = resolve_pinned_artifacts(horizons)

        cache_scope = str(options['cache_scope'] or '').strip() or (
            f"stk10-{digest}:{timezone.now().strftime('%Y%m%d%H%M%S')}"
            f":{start_date.isoformat()}:{end_date.isoformat()}"
        )

        cells = build_matrix(start_date, end_date, horizons, cache_scope, artifacts)

        if options['dry_run']:
            self.stdout.write(f'DRY RUN: {len(cells)} cells, window {start_date}..{end_date}, '
                              f'digest {digest}, cache scope {cache_scope}')
            for cell in cells:
                self.stdout.write(_cell_name(prefix, cell))
                for key in sorted(cell['parameters']):
                    self.stdout.write(f'    {key}={cell["parameters"][key]!r}')
            return

        created_ids = []
        for cell in cells:
            parameters = dict(cell['parameters'])
            parameters['preregistration_digest'] = digest
            run = BacktestRun.objects.create(
                name=_cell_name(prefix, cell),
                strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
                status=BacktestRun.Status.PENDING,
                start_date=start_date,
                end_date=end_date,
                initial_capital=options['initial_capital'],
                cash=options['initial_capital'],
                final_value=0,
                total_return=0,
                annualized_return=0,
                max_drawdown=0,
                sharpe_ratio=0,
                win_rate=0,
                total_trades=0,
                winning_trades=0,
                parameters=parameters,
                report={},
                error_message='',
                started_at=None,
                completed_at=None,
            )
            created_ids.append(run.id)
            self.stdout.write(f'created run_id={run.id} {_cell_name(prefix, cell)}')

        self.stdout.write(self.style.SUCCESS(
            f'Created {len(created_ids)} STK-10 cells ({start_date}..{end_date}, digest {digest}).'
        ))

        if not options['execute']:
            self.stdout.write('Create only. Re-run with --execute (inside tmux) to drive them inline.')
            return

        self._execute_inline(created_ids, horizons)

    def _execute_inline(self, run_ids, horizons):
        """Drive the runs inline, one horizon bucket at a time.

        Inline bypasses the Celery soft/hard time limits, which a ~43 h run cannot
        satisfy; chunking still persists report.runtime_state so a kill resumes from
        the last chunk boundary rather than restarting.
        """
        pending = {h: [] for h in horizons}
        for run_id in run_ids:
            run = BacktestRun.objects.filter(id=run_id).first()
            if run is None:
                continue
            horizon = int((run.parameters or {}).get('horizon_days') or 0)
            pending.setdefault(horizon, []).append(run_id)

        def _enqueue(run_id):
            queue.append(int(run_id))

        with patch('apps.backtest.tasks.run_backtest.delay', side_effect=_enqueue):
            clear_backtest_process_caches()
            try:
                for index, horizon in enumerate(horizons):
                    queue = list(pending.get(horizon) or [])
                    while queue:
                        current_id = queue.pop(0)
                        for attempt in range(2):
                            try:
                                run_backtest(current_id)
                                break
                            except (OperationalError, InterfaceError):
                                connections.close_all()
                                if attempt == 1:
                                    raise
                        current = BacktestRun.objects.filter(id=current_id).first()
                        if current is not None and current.status == BacktestRun.Status.RUNNING:
                            queue.append(current_id)
                    if index < len(horizons) - 1:
                        clear_backtest_process_caches()
            finally:
                clear_backtest_process_caches()

        self.stdout.write(self.style.SUCCESS('Executed STK-10 matrix inline to completion.'))
