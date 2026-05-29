# python manage.py run_core_backtest_matrix --start-date 2025-01-01 --end-date 2025-12-31 --variants top-n,trade-score-limit --sources heuristic,lightgbm --name-prefix core18-2025 --queue --output-dir
# python manage.py run_core_backtest_matrix --start-date 2025-01-01 --end-date 2025-12-31 --variants top-n,trade-score-limit --sources heuristic,lightgbm --name-prefix core18-2025-20260526 --execute-inline --chunk-trading-days 60 --output-dir reports/core18-2025-inline-20250526
import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.utils import InterfaceError, OperationalError
from django.utils import timezone

from apps.backtest.models import BacktestRun
from apps.backtest.tasks import queue_backtest_run, run_backtest
from apps.prediction.models_lightgbm import LightGBMModelArtifact


CORE_PROFILES = {
    3: [
        {'profile': 'conservative', 'top_n': 3, 'up_threshold': 0.55, 'holding_period_days': 5},
        {'profile': 'base', 'top_n': 5, 'up_threshold': 0.45, 'holding_period_days': 5},
        {'profile': 'aggressive', 'top_n': 7, 'up_threshold': 0.35, 'holding_period_days': 5},
    ],
    7: [
        {'profile': 'conservative', 'top_n': 3, 'up_threshold': 0.55, 'holding_period_days': 10},
        {'profile': 'base', 'top_n': 5, 'up_threshold': 0.45, 'holding_period_days': 10},
        {'profile': 'aggressive', 'top_n': 7, 'up_threshold': 0.35, 'holding_period_days': 10},
    ],
    30: [
        {'profile': 'conservative', 'top_n': 2, 'up_threshold': 0.55, 'holding_period_days': 20},
        {'profile': 'base', 'top_n': 3, 'up_threshold': 0.45, 'holding_period_days': 20},
        {'profile': 'aggressive', 'top_n': 5, 'up_threshold': 0.35, 'holding_period_days': 20},
    ],
}

VARIANT_DEFINITIONS = {
    'top-n': {
        'short_name': 'top-n',
        'candidate_mode': 'top_n',
    },
    'trade-score-limit': {
        'short_name': 'ts-limit',
        'candidate_mode': 'trade_score',
        'top_n_metric': 'trade_score',
        'trade_score_scope': 'independent',
        'trade_score_threshold': 1.0,
    },
}

VALID_SOURCES = {'heuristic', 'lightgbm'}


def _parse_date(value, name):
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f'Invalid {name}: {value}. Expected YYYY-MM-DD.') from exc


def _parse_csv_tokens(raw_value, name):
    values = [token.strip().lower() for token in str(raw_value or '').split(',') if token.strip()]
    if not values:
        raise CommandError(f'{name} must contain at least one value.')
    return values


class _InlineDelayResult:
    def __init__(self, task_id):
        self.id = task_id


class Command(BaseCommand):
    help = 'Create and optionally queue the heuristic/lightgbm core-profile backtest matrix across selected variants.'

    def add_arguments(self, parser):
        parser.add_argument('--start-date', required=True, help='Backtest start date (YYYY-MM-DD).')
        parser.add_argument('--end-date', required=True, help='Backtest end date (YYYY-MM-DD).')
        parser.add_argument(
            '--variants',
            default='top-n,trade-score-limit',
            help='Comma-separated matrix variants: top-n,trade-score-limit.',
        )
        parser.add_argument(
            '--sources',
            default='heuristic,lightgbm',
            help='Comma-separated prediction sources: heuristic,lightgbm.',
        )
        parser.add_argument(
            '--name-prefix',
            default='core18',
            help='Prefix used for BacktestRun names.',
        )
        parser.add_argument(
            '--user-email',
            default='',
            help='Optional user email to attribute created runs.',
        )
        parser.add_argument(
            '--queue',
            action='store_true',
            help='Queue created runs asynchronously instead of executing the first chunk inline.',
        )
        parser.add_argument(
            '--execute-inline',
            action='store_true',
            help='Execute all matrix runs to completion in this process, round-robin by queued chunk while preserving the in-memory matrix signal cache.',
        )
        parser.add_argument(
            '--chunk-trading-days',
            type=int,
            default=60,
            help='Per-run chunk size stamped into matrix BacktestRun parameters. Defaults to 60.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print the planned matrix without creating BacktestRun rows.',
        )
        parser.add_argument(
            '--output-dir',
            default='',
            help='Optional compact export directory. Defaults to reports/<name-prefix>-<timestamp>.',
        )
        parser.add_argument(
            '--include-active-lightgbm-artifacts',
            action='store_true',
            help='Also export active LightGBM artifact metadata in the compact bundle.',
        )

    def _resolve_user(self, user_email):
        user_model = get_user_model()
        if user_email:
            user = user_model.objects.filter(email=user_email).first()
            if user is None:
                raise CommandError(f'No user found for email: {user_email}')
            return user

        user = user_model.objects.filter(is_superuser=True).order_by('id').first()
        if user is not None:
            return user
        return user_model.objects.order_by('id').first()

    def _build_run_specs(self, start_date, end_date, variants, sources, name_prefix):
        specs = []
        date_token = f'{start_date.isoformat()}-{end_date.isoformat()}'
        for variant_name in variants:
            variant = VARIANT_DEFINITIONS[variant_name]
            for source in sources:
                for horizon_days, profiles in CORE_PROFILES.items():
                    for profile in profiles:
                        top_n = int(profile['top_n'])
                        params = {
                            'top_n': top_n,
                            'horizon_days': int(horizon_days),
                            'up_threshold': float(profile['up_threshold']),
                            'prediction_source': source,
                            'holding_period_days': int(profile['holding_period_days']),
                            'capital_fraction_per_entry': 0.2,
                            'use_macro_context': True,
                            'enable_stop_target_exit': False,
                            'candidate_mode': variant['candidate_mode'],
                        }
                        if variant['candidate_mode'] == 'trade_score':
                            params.update({
                                'top_n_metric': variant['top_n_metric'],
                                'trade_score_scope': variant['trade_score_scope'],
                                'trade_score_threshold': float(variant['trade_score_threshold']),
                                'max_positions': top_n,
                            })
                        else:
                            params['top_n_metric'] = f'up_prob_{int(horizon_days)}d'

                        run_name = (
                            f"{name_prefix}-{variant['short_name']}-{source}-"
                            f"{int(horizon_days)}d-{profile['profile']}-{date_token}"
                        )
                        specs.append({
                            'name': run_name,
                            'start_date': start_date,
                            'end_date': end_date,
                            'parameters': params,
                            'variant': variant_name,
                            'source': source,
                            'horizon_days': int(horizon_days),
                            'profile': profile['profile'],
                        })
        return specs

    def _active_lightgbm_artifacts_by_horizon(self, specs):
        required_horizons = sorted({spec['horizon_days'] for spec in specs if spec['source'] == 'lightgbm'})
        if not required_horizons:
            return {}

        artifacts = {}
        for horizon_days in required_horizons:
            artifact = (
                LightGBMModelArtifact.objects.filter(
                    horizon_days=horizon_days,
                    status=LightGBMModelArtifact.Status.READY,
                    is_active=True,
                )
                .order_by('-trained_at', '-created_at')
                .first()
            )
            if artifact is None:
                raise CommandError(f'No active READY LightGBM artifact found for horizon {horizon_days}.')
            artifacts[horizon_days] = artifact
        return artifacts

    def _apply_matrix_runtime_parameters(self, specs, matrix_cache_key, chunk_trading_days, lightgbm_artifacts):
        for spec in specs:
            params = dict(spec['parameters'])
            params['matrix_signal_cache_key'] = matrix_cache_key
            params['chunk_trading_days'] = int(chunk_trading_days)
            if spec['source'] == 'lightgbm' and lightgbm_artifacts:
                artifact = lightgbm_artifacts[spec['horizon_days']]
                params['lightgbm_model_artifact_id'] = artifact.id
                params['lightgbm_model_artifact_version'] = artifact.version
            spec['parameters'] = params

    def _run_backtests_inline_to_completion(self, root_run_ids):
        pending_run_ids = [int(run_id) for run_id in root_run_ids]
        queued_count = 0

        def _enqueue(run_id):
            nonlocal queued_count
            queued_count += 1
            pending_run_ids.append(int(run_id))
            return _InlineDelayResult(f'inline-matrix-{queued_count}')

        with patch('apps.backtest.tasks.run_backtest.delay', side_effect=_enqueue):
            while pending_run_ids:
                current_run_id = pending_run_ids.pop(0)
                for attempt in range(2):
                    try:
                        run_backtest(current_run_id)
                        break
                    except (OperationalError, InterfaceError):
                        connections.close_all()
                        if attempt == 1:
                            raise

    def handle(self, *args, **options):
        start_date = _parse_date(options['start_date'], 'start-date')
        end_date = _parse_date(options['end_date'], 'end-date')
        if end_date < start_date:
            raise CommandError('end-date must be on or after start-date.')
        if options['queue'] and options['execute_inline']:
            raise CommandError('--queue and --execute-inline cannot be used together.')
        if int(options['chunk_trading_days']) <= 0:
            raise CommandError('--chunk-trading-days must be greater than 0.')

        matrix_started_at = timezone.now()

        variant_names = _parse_csv_tokens(options['variants'], 'variants')
        invalid_variants = [name for name in variant_names if name not in VARIANT_DEFINITIONS]
        if invalid_variants:
            raise CommandError(
                'Unsupported variants: ' + ', '.join(invalid_variants)
                + '. Expected subset of: ' + ', '.join(VARIANT_DEFINITIONS.keys())
            )

        source_names = _parse_csv_tokens(options['sources'], 'sources')
        invalid_sources = [name for name in source_names if name not in VALID_SOURCES]
        if invalid_sources:
            raise CommandError(
                'Unsupported sources: ' + ', '.join(invalid_sources)
                + '. Expected subset of: ' + ', '.join(sorted(VALID_SOURCES))
            )

        specs = self._build_run_specs(
            start_date,
            end_date,
            variant_names,
            source_names,
            str(options['name_prefix'] or '').strip() or 'core18',
        )
        if not specs:
            raise CommandError('No backtest specifications were generated.')

        name_prefix = str(options['name_prefix'] or '').strip() or 'core18'
        matrix_cache_key = (
            f"{name_prefix}:{matrix_started_at.strftime('%Y%m%d%H%M%S')}:{start_date.isoformat()}:{end_date.isoformat()}"
        )
        lightgbm_artifacts = {} if options['dry_run'] else self._active_lightgbm_artifacts_by_horizon(specs)
        self._apply_matrix_runtime_parameters(
            specs,
            matrix_cache_key,
            int(options['chunk_trading_days']),
            lightgbm_artifacts,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f'Prepared {len(specs)} runs: {len(variant_names)} variants x {len(source_names)} sources x 9 core configs.'
            )
        )

        if options['dry_run']:
            for spec in specs:
                self.stdout.write(
                    f"[dry-run] {spec['name']} :: variant={spec['variant']} source={spec['source']} "
                    f"horizon={spec['horizon_days']} profile={spec['profile']} params={spec['parameters']}"
                )
            return

        user = self._resolve_user(options['user_email'])
        created_run_ids = []
        for spec in specs:
            run = BacktestRun.objects.create(
                user=user,
                name=spec['name'],
                strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
                start_date=spec['start_date'],
                end_date=spec['end_date'],
                initial_capital=200000.00,
                status=BacktestRun.Status.PENDING,
                parameters=spec['parameters'],
            )
            created_run_ids.append(run.id)

            if options['queue']:
                queue_backtest_run(run)
                launch_mode = 'queued'
            elif options['execute_inline']:
                launch_mode = 'prepared-inline'
            else:
                run_backtest(run.id)
                launch_mode = 'executed-first-chunk'

            self.stdout.write(
                f"[{spec['variant']}] [{spec['source']}] [{spec['horizon_days']}d/{spec['profile']}] "
                f'run_id={run.id} ({launch_mode})'
            )

        if options['execute_inline']:
            self._run_backtests_inline_to_completion(created_run_ids)
            self.stdout.write(self.style.SUCCESS('Executed inline matrix to completion.'))

        timestamp_token = matrix_started_at.strftime('%Y%m%d_%H%M%S')
        output_dir = Path(options['output_dir']) if options['output_dir'] else Path('reports') / f"{name_prefix}-{timestamp_token}"
        output_dir.mkdir(parents=True, exist_ok=True)

        call_command(
            'export_backtest_runs',
            start_id=min(created_run_ids),
            end_id=max(created_run_ids),
            output_dir=str(output_dir),
            detail_export=False,
            include_active_lightgbm_artifacts=options['include_active_lightgbm_artifacts'],
            stdout=self.stdout,
        )

        manifest = {
            'name_prefix': name_prefix,
            'output_dir': str(output_dir),
            'run_ids': created_run_ids,
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'variants': variant_names,
            'sources': source_names,
            'queued': bool(options['queue']),
            'execute_inline': bool(options['execute_inline']),
            'chunk_trading_days': int(options['chunk_trading_days']),
            'matrix_signal_cache_key': matrix_cache_key,
        }
        (output_dir / 'matrix_manifest.json').write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding='utf-8',
        )

        self.stdout.write(self.style.SUCCESS(f'Created {len(created_run_ids)} matrix runs.'))
        self.stdout.write(self.style.SUCCESS(f'Core matrix exported to {output_dir}'))