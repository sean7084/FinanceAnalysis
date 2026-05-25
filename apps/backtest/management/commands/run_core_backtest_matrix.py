# python manage.py run_core_backtest_matrix --start-date 2025-01-01 --end-date 2025-12-31 --variants original,weekdays,trade-score-limit --sources heuristic,lightgbm --name-prefix core18-2025 --queue --output-dir
import json
from datetime import date
from pathlib import Path

from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.backtest.models import BacktestRun
from apps.backtest.tasks import queue_backtest_run, run_backtest


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
    'original': {
        'short_name': 'orig',
        'entry_weekdays': ['TUE', 'THU'],
        'candidate_mode': 'top_n',
    },
    'weekdays': {
        'short_name': 'mon-fri',
        'entry_weekdays': ['MON', 'TUE', 'WED', 'THU', 'FRI'],
        'candidate_mode': 'top_n',
    },
    'trade-score-limit': {
        'short_name': 'ts-limit',
        'entry_weekdays': ['TUE', 'THU'],
        'candidate_mode': 'trade_score',
        'top_n_metric': 'trade_score',
        'trade_score_scope': 'independent',
        'trade_score_threshold': 1.0,
    },
        'trade-score-limit-weekdays': {
        'short_name': 'ts-limit-mon-fri',
        'entry_weekdays': ['MON', 'TUE', 'WED', 'THU', 'FRI'],
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


class Command(BaseCommand):
    help = 'Create and optionally queue the core 18-run heuristic/lightgbm backtest matrix across selected variants.'

    def add_arguments(self, parser):
        parser.add_argument('--start-date', required=True, help='Backtest start date (YYYY-MM-DD).')
        parser.add_argument('--end-date', required=True, help='Backtest end date (YYYY-MM-DD).')
        parser.add_argument(
            '--variants',
            default='original,weekdays,trade-score-limit,trade-score-limit-weekdays',
            help='Comma-separated matrix variants: original,weekdays,trade-score-limit,trade-score-limit-weekdays.',
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
                            'entry_weekdays': list(variant['entry_weekdays']),
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

    def handle(self, *args, **options):
        start_date = _parse_date(options['start_date'], 'start-date')
        end_date = _parse_date(options['end_date'], 'end-date')
        if end_date < start_date:
            raise CommandError('end-date must be on or after start-date.')

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
            else:
                run_backtest(run.id)
                launch_mode = 'executed'

            self.stdout.write(
                f"[{spec['variant']}] [{spec['source']}] [{spec['horizon_days']}d/{spec['profile']}] "
                f'run_id={run.id} ({launch_mode})'
            )

        timestamp_token = matrix_started_at.strftime('%Y%m%d_%H%M%S')
        output_dir = Path(options['output_dir']) if options['output_dir'] else Path('reports') / f"{options['name_prefix']}-{timestamp_token}"
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
            'name_prefix': str(options['name_prefix'] or '').strip() or 'core18',
            'output_dir': str(output_dir),
            'run_ids': created_run_ids,
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'variants': variant_names,
            'sources': source_names,
            'queued': bool(options['queue']),
        }
        (output_dir / 'matrix_manifest.json').write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding='utf-8',
        )

        self.stdout.write(self.style.SUCCESS(f'Created {len(created_run_ids)} matrix runs.'))
        self.stdout.write(self.style.SUCCESS(f'Core matrix exported to {output_dir}'))