import json
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.date_floor import get_historical_data_floor
from apps.markets.tasks import DEFAULT_INDEX_CODES


class Command(BaseCommand):
    help = (
        'Run the safe CSI A500 rollout workflow: onboarding with pre/post benchmarks disabled, '
        'fixed-window LightGBM/LSTM retrains, and compact post-expansion benchmark suites.'
    )

    @staticmethod
    def _default_start_date():
        return get_historical_data_floor()

    @staticmethod
    def _default_end_date():
        return timezone.now().date() - timedelta(days=1)

    def add_arguments(self, parser):
        parser.add_argument('--start-date', default=self._default_start_date().isoformat())
        parser.add_argument('--end-date', default=self._default_end_date().isoformat())
        parser.add_argument('--index-codes', default=','.join(DEFAULT_INDEX_CODES))
        parser.add_argument('--report-label', default='')
        parser.add_argument('--report-root-dir', default='reports')
        parser.add_argument('--skip-onboarding', action='store_true')
        parser.add_argument('--skip-retrain', action='store_true')
        parser.add_argument('--skip-post-benchmarks', action='store_true')
        parser.add_argument('--skip-sentiment', action='store_true')
        parser.add_argument('--horizons', default='3,7,30')
        parser.add_argument('--retrain-start-date', default='2016-06-01')
        parser.add_argument('--retrain-end-date', default='2024-12-31')
        parser.add_argument('--lightgbm-version-tag', default='')
        parser.add_argument('--lightgbm-use-snapshot-pruning', action='store_true')
        parser.add_argument('--lstm-sequence-length', type=int, default=20)
        parser.add_argument('--lstm-asset-chunk-size', type=int, default=60)
        parser.add_argument('--lstm-max-samples-per-horizon', type=int, default=30000)
        parser.add_argument('--benchmark-sources', default='heuristic,lightgbm,lstm')
        parser.add_argument('--benchmark-top-n', type=int, default=3)
        parser.add_argument('--benchmark-entry-weekdays', default='1,3')
        parser.add_argument('--benchmark-holding-period-days', type=int, default=7)
        parser.add_argument('--benchmark-capital-fraction-per-entry', type=float, default=0.5)
        parser.add_argument('--benchmark-min-up-probability', type=float, default=0.0)
        parser.add_argument('--benchmark-name-prefix', default='post-a500-expansion-compact')
        parser.add_argument('--benchmark-launch-mode', choices=['queue', 'inline'], default='queue')
        parser.add_argument('--post-benchmark-train-start-date', default='2023-01-01')
        parser.add_argument('--post-benchmark-train-end-date', default='2024-12-31')
        parser.add_argument('--post-benchmark-test-start-date', default='2025-01-01')
        parser.add_argument('--post-benchmark-test-end-date', default='2025-12-31')

    def _parse_date(self, value, name):
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise CommandError(f'Invalid {name}: {value}. Expected YYYY-MM-DD.') from exc

    def _parse_horizons(self, value):
        horizons = []
        for raw in str(value).split(','):
            token = raw.strip()
            if not token:
                continue
            try:
                horizon = int(token)
            except ValueError as exc:
                raise CommandError(f'Invalid horizon value: {token}. Expected comma-separated integers.') from exc
            if horizon not in {3, 7, 30}:
                raise CommandError('horizons must be a comma-separated subset of 3,7,30.')
            horizons.append(horizon)
        if not horizons:
            raise CommandError('horizons must contain at least one value.')
        return list(dict.fromkeys(horizons))

    def _build_compact_suite_specs(self, horizons, options):
        families = [
            (
                'train',
                self._parse_date(options['post_benchmark_train_start_date'], 'post-benchmark-train-start-date'),
                self._parse_date(options['post_benchmark_train_end_date'], 'post-benchmark-train-end-date'),
            ),
            (
                'test',
                self._parse_date(options['post_benchmark_test_start_date'], 'post-benchmark-test-start-date'),
                self._parse_date(options['post_benchmark_test_end_date'], 'post-benchmark-test-end-date'),
            ),
        ]

        specs = []
        for family_label, family_start, family_end in families:
            if family_end < family_start:
                raise CommandError(f'{family_label} benchmark end date must be on or after its start date.')
            window_days = (family_end - family_start).days + 1
            for horizon in horizons:
                specs.append(
                    {
                        'label': f'{family_label}_h{horizon}',
                        'start_date': family_start,
                        'end_date': family_end,
                        'window_days': window_days,
                        'step_days': window_days,
                        'horizon_days': horizon,
                    }
                )
        return specs

    def _run_compact_post_benchmarks(self, *, report_root, report_label, suite_specs, options):
        post_root = report_root / 'post_expansion'
        launch_queue = options['benchmark_launch_mode'] == 'queue'

        for spec in suite_specs:
            output_dir = post_root / spec['label']
            call_command(
                'run_reference_benchmark_suite',
                start_date=spec['start_date'].isoformat(),
                end_date=spec['end_date'].isoformat(),
                window_days=spec['window_days'],
                step_days=spec['step_days'],
                sources=options['benchmark_sources'],
                top_n=options['benchmark_top_n'],
                horizon_days=spec['horizon_days'],
                entry_weekdays=options['benchmark_entry_weekdays'],
                holding_period_days=options['benchmark_holding_period_days'],
                capital_fraction_per_entry=options['benchmark_capital_fraction_per_entry'],
                min_up_probability=options['benchmark_min_up_probability'],
                name_prefix=f"{options['benchmark_name_prefix']}-{spec['label']}",
                output_dir=str(output_dir),
                suite_name=f'{report_label}_{spec["label"]}',
                queue=launch_queue,
                include_active_lightgbm_artifacts=True,
                stdout=self.stdout,
            )

    def handle(self, *args, **options):
        start_date = self._parse_date(options['start_date'], 'start-date')
        end_date = self._parse_date(options['end_date'], 'end-date')
        retrain_start_date = self._parse_date(options['retrain_start_date'], 'retrain-start-date')
        retrain_end_date = self._parse_date(options['retrain_end_date'], 'retrain-end-date')
        floor_date = get_historical_data_floor()
        if start_date < floor_date:
            raise CommandError(f'start-date cannot be earlier than HISTORICAL_DATA_FLOOR={floor_date}.')
        if end_date < start_date:
            raise CommandError('end-date must be on or after start-date.')
        if retrain_end_date < retrain_start_date:
            raise CommandError('retrain-end-date must be on or after retrain-start-date.')

        horizons = self._parse_horizons(options['horizons'])
        report_label = str(options['report_label'] or f"csi300_a500_safe_rollout_{timezone.now().strftime('%Y%m%d_%H%M%S')}")
        report_root = Path(options['report_root_dir']) / report_label
        report_root.mkdir(parents=True, exist_ok=True)

        suite_specs = self._build_compact_suite_specs(horizons, options)

        if not options['skip_onboarding']:
            self.stdout.write(self.style.NOTICE('Running onboarding phase with pre/post benchmarks and retrain disabled...'))
            onboarding_kwargs = {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
                'index_codes': options['index_codes'],
                'report_root_dir': str(report_root),
                'report_label': 'onboarding',
                'skip_pre_benchmarks': True,
                'skip_retrain': True,
                'skip_post_benchmarks': True,
                'stdout': self.stdout,
            }
            if options['skip_sentiment']:
                onboarding_kwargs['skip_sentiment'] = True
            call_command('onboard_csi_a500_universe', **onboarding_kwargs)
        else:
            self.stdout.write('Skipping onboarding phase by request.')

        if not options['skip_retrain']:
            self.stdout.write(self.style.NOTICE('Running fixed-window retraining phase...'))
            lightgbm_kwargs = {
                'start_date': retrain_start_date.isoformat(),
                'end_date': retrain_end_date.isoformat(),
                'horizons': ','.join(str(horizon) for horizon in horizons),
                'skip_backfill': True,
                'version_tag': options['lightgbm_version_tag'],
                'use_snapshot_pruning': options['lightgbm_use_snapshot_pruning'],
                'stdout': self.stdout,
            }
            if options['skip_sentiment']:
                lightgbm_kwargs['skip_sentiment'] = True
            call_command('rebuild_lightgbm_pipeline', **lightgbm_kwargs)

            lstm_kwargs = {
                'start_date': retrain_start_date.isoformat(),
                'end_date': retrain_end_date.isoformat(),
                'horizons': ','.join(str(horizon) for horizon in horizons),
                'sequence_length': options['lstm_sequence_length'],
                'asset_chunk_size': options['lstm_asset_chunk_size'],
                'max_samples_per_horizon': options['lstm_max_samples_per_horizon'],
                'skip_backfill': True,
                'stdout': self.stdout,
            }
            if options['skip_sentiment']:
                lstm_kwargs['skip_sentiment'] = True
            call_command('rebuild_lstm_pipeline', **lstm_kwargs)
        else:
            self.stdout.write('Skipping retraining phase by request.')

        if not options['skip_post_benchmarks']:
            self.stdout.write(self.style.NOTICE('Running compact post-expansion benchmark suites...'))
            self._run_compact_post_benchmarks(
                report_root=report_root,
                report_label=report_label,
                suite_specs=suite_specs,
                options=options,
            )
        else:
            self.stdout.write('Skipping post-expansion benchmark suites by request.')

        manifest = {
            'report_label': report_label,
            'report_root': str(report_root),
            'raw_backfill_window': {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
            },
            'retrain_window': {
                'start_date': retrain_start_date.isoformat(),
                'end_date': retrain_end_date.isoformat(),
            },
            'index_codes': [token.strip() for token in str(options['index_codes']).split(',') if token.strip()],
            'horizons': horizons,
            'benchmark_sources': [token.strip() for token in str(options['benchmark_sources']).split(',') if token.strip()],
            'benchmark_launch_mode': options['benchmark_launch_mode'],
            'benchmark_suites': [
                {
                    'label': spec['label'],
                    'start_date': spec['start_date'].isoformat(),
                    'end_date': spec['end_date'].isoformat(),
                    'window_days': spec['window_days'],
                    'step_days': spec['step_days'],
                    'horizon_days': spec['horizon_days'],
                    'output_dir': str(report_root / 'post_expansion' / spec['label']),
                }
                for spec in suite_specs
            ],
            'skip_onboarding': bool(options['skip_onboarding']),
            'skip_retrain': bool(options['skip_retrain']),
            'skip_post_benchmarks': bool(options['skip_post_benchmarks']),
            'skip_sentiment': bool(options['skip_sentiment']),
            'onboarding_report_root': str(report_root / 'onboarding'),
        }
        (report_root / 'rollout_manifest.json').write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding='utf-8',
        )

        self.stdout.write(self.style.SUCCESS(f'CSI A500 safe rollout workflow complete. report_root={report_root}'))