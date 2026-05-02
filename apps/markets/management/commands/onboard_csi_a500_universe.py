import json
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.date_floor import get_historical_data_floor
from apps.markets.models import Asset
from apps.markets.tasks import DEFAULT_INDEX_CODES


class Command(BaseCommand):
    help = (
        'Add CSI A500 alongside CSI 300, persist historical memberships, backfill A500-only raw data, '
        'recompute model inputs across the combined universe, retrain LightGBM/LSTM, and export pre/post benchmark suites.'
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
        parser.add_argument('--benchmark-start-date', default='')
        parser.add_argument('--benchmark-end-date', default='')
        parser.add_argument('--benchmark-window-days', type=int, default=180)
        parser.add_argument('--benchmark-step-days', type=int, default=30)
        parser.add_argument('--benchmark-sources', default='heuristic,lightgbm,lstm')
        parser.add_argument('--benchmark-top-n', type=int, default=3)
        parser.add_argument('--benchmark-horizon-days', type=int, default=7)
        parser.add_argument('--benchmark-entry-weekdays', default='1,3')
        parser.add_argument('--benchmark-holding-period-days', type=int, default=7)
        parser.add_argument('--benchmark-capital-fraction-per-entry', type=float, default=0.5)
        parser.add_argument('--benchmark-min-up-probability', type=float, default=0.0)
        parser.add_argument('--benchmark-name-prefix', default='csi300-a500')
        parser.add_argument('--report-label', default='')
        parser.add_argument('--report-root-dir', default='reports')
        parser.add_argument('--horizons', default='3,7,30')
        parser.add_argument('--skip-pre-benchmarks', action='store_true')
        parser.add_argument('--skip-post-benchmarks', action='store_true')
        parser.add_argument('--skip-raw-backfills', action='store_true')
        parser.add_argument('--skip-model-backfill', action='store_true')
        parser.add_argument('--skip-retrain', action='store_true')
        parser.add_argument('--skip-sentiment', action='store_true')
        parser.add_argument('--lightgbm-version-tag', default='')
        parser.add_argument('--lightgbm-use-snapshot-pruning', action='store_true')
        parser.add_argument('--lstm-sequence-length', type=int, default=20)
        parser.add_argument('--lstm-asset-chunk-size', type=int, default=60)
        parser.add_argument('--lstm-max-samples-per-horizon', type=int, default=30000)

    def _parse_date(self, value, name):
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise CommandError(f'Invalid {name}: {value}. Expected YYYY-MM-DD.') from exc

    def _resolve_benchmark_range(self, start_date, end_date, options):
        benchmark_start_raw = str(options['benchmark_start_date'] or '').strip()
        benchmark_end_raw = str(options['benchmark_end_date'] or '').strip()

        benchmark_end = self._parse_date(benchmark_end_raw, 'benchmark-end-date') if benchmark_end_raw else end_date
        benchmark_start = (
            self._parse_date(benchmark_start_raw, 'benchmark-start-date')
            if benchmark_start_raw
            else max(start_date, benchmark_end - timedelta(days=365 * 2))
        )
        if benchmark_end < benchmark_start:
            raise CommandError('benchmark-end-date must be on or after benchmark-start-date.')
        return benchmark_start, benchmark_end

    def _resolve_a500_only_symbols(self):
        symbols = []
        for asset in Asset.objects.filter(listing_status=Asset.ListingStatus.ACTIVE).order_by('ts_code'):
            tags = set(asset.membership_tags or [])
            if 'CSIA500' in tags and 'CSI300' not in tags:
                symbols.append(asset.symbol)
        return symbols

    def _run_benchmark_suite(self, *, output_dir, suite_name, name_prefix, benchmark_start, benchmark_end, options):
        call_command(
            'run_reference_benchmark_suite',
            start_date=benchmark_start.isoformat(),
            end_date=benchmark_end.isoformat(),
            window_days=options['benchmark_window_days'],
            step_days=options['benchmark_step_days'],
            sources=options['benchmark_sources'],
            top_n=options['benchmark_top_n'],
            horizon_days=options['benchmark_horizon_days'],
            entry_weekdays=options['benchmark_entry_weekdays'],
            holding_period_days=options['benchmark_holding_period_days'],
            capital_fraction_per_entry=options['benchmark_capital_fraction_per_entry'],
            min_up_probability=options['benchmark_min_up_probability'],
            name_prefix=name_prefix,
            output_dir=str(output_dir),
            suite_name=suite_name,
            include_active_lightgbm_artifacts=True,
        )

    def handle(self, *args, **options):
        start_date = self._parse_date(options['start_date'], 'start-date')
        end_date = self._parse_date(options['end_date'], 'end-date')
        floor_date = get_historical_data_floor()
        if start_date < floor_date:
            raise CommandError(f'start-date cannot be earlier than HISTORICAL_DATA_FLOOR={floor_date}.')
        if end_date < start_date:
            raise CommandError('end-date must be on or after start-date.')

        benchmark_start, benchmark_end = self._resolve_benchmark_range(start_date, end_date, options)
        report_label = str(options['report_label'] or f"csi300_a500_{timezone.now().strftime('%Y%m%d_%H%M%S')}")
        report_root = Path(options['report_root_dir']) / report_label
        report_root.mkdir(parents=True, exist_ok=True)
        pre_report_dir = report_root / 'pre_expansion'
        post_report_dir = report_root / 'post_expansion'

        if not options['skip_pre_benchmarks']:
            self.stdout.write(self.style.NOTICE('Running pre-expansion benchmark suite...'))
            self._run_benchmark_suite(
                output_dir=pre_report_dir,
                suite_name=f'{report_label}_pre',
                name_prefix=f"{options['benchmark_name_prefix']}-pre",
                benchmark_start=benchmark_start,
                benchmark_end=benchmark_end,
                options=options,
            )

        self.stdout.write(self.style.NOTICE('Syncing CSI 300 + CSI A500 memberships...'))
        call_command(
            'sync_index_constituents',
            index_codes=options['index_codes'],
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            skip_sync_dispatch=True,
            stdout=self.stdout,
        )

        a500_only_symbols = self._resolve_a500_only_symbols()
        self.stdout.write(
            self.style.NOTICE(
                f'Resolved {len(a500_only_symbols)} current A500-only active symbols for targeted raw backfills.'
            )
        )

        if a500_only_symbols and not options['skip_raw_backfills']:
            symbol_csv = ','.join(a500_only_symbols)
            call_command(
                'backfill_ohlcv_history',
                start_date=start_date.isoformat(),
                symbols=symbol_csv,
                stdout=self.stdout,
            )
            call_command(
                'backfill_fundamental_snapshots',
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                symbols=symbol_csv,
                stdout=self.stdout,
            )
            call_command(
                'backfill_capital_flow_snapshots',
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                symbols=symbol_csv,
                stdout=self.stdout,
            )
        elif options['skip_raw_backfills']:
            self.stdout.write('Skipping targeted raw backfills by request.')
        else:
            self.stdout.write('No current A500-only active symbols found; targeted raw backfills skipped.')

        self.stdout.write(self.style.NOTICE('Refreshing point-in-time union benchmark history...'))
        call_command(
            'build_pit_union_benchmark',
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            stdout=self.stdout,
        )

        if not options['skip_model_backfill']:
            model_backfill_kwargs = {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
                'stdout': self.stdout,
            }
            if options['skip_sentiment']:
                model_backfill_kwargs['skip_sentiment'] = True
            call_command('backfill_model_data', **model_backfill_kwargs)
        else:
            self.stdout.write('Skipping combined-universe model backfill by request.')

        if not options['skip_retrain']:
            lightgbm_kwargs = {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
                'horizons': options['horizons'],
                'skip_backfill': True,
                'version_tag': options['lightgbm_version_tag'],
                'use_snapshot_pruning': options['lightgbm_use_snapshot_pruning'],
                'stdout': self.stdout,
            }
            if options['skip_sentiment']:
                lightgbm_kwargs['skip_sentiment'] = True
            call_command('rebuild_lightgbm_pipeline', **lightgbm_kwargs)

            lstm_kwargs = {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
                'horizons': options['horizons'],
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
            self.stdout.write('Skipping model retraining by request.')

        if not options['skip_post_benchmarks']:
            self.stdout.write(self.style.NOTICE('Running post-expansion benchmark suite...'))
            self._run_benchmark_suite(
                output_dir=post_report_dir,
                suite_name=f'{report_label}_post',
                name_prefix=f"{options['benchmark_name_prefix']}-post",
                benchmark_start=benchmark_start,
                benchmark_end=benchmark_end,
                options=options,
            )

        manifest = {
            'report_label': report_label,
            'report_root': str(report_root),
            'index_codes': [token.strip() for token in str(options['index_codes']).split(',') if token.strip()],
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'benchmark_start_date': benchmark_start.isoformat(),
            'benchmark_end_date': benchmark_end.isoformat(),
            'benchmark_sources': [token.strip() for token in str(options['benchmark_sources']).split(',') if token.strip()],
            'a500_only_symbols': a500_only_symbols,
            'pit_benchmark_window': {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
            },
            'pre_benchmark_output_dir': str(pre_report_dir),
            'post_benchmark_output_dir': str(post_report_dir),
            'skipped_pre_benchmarks': bool(options['skip_pre_benchmarks']),
            'skipped_post_benchmarks': bool(options['skip_post_benchmarks']),
            'skipped_raw_backfills': bool(options['skip_raw_backfills']),
            'skipped_model_backfill': bool(options['skip_model_backfill']),
            'skipped_retrain': bool(options['skip_retrain']),
        }
        (report_root / 'rollout_manifest.json').write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding='utf-8',
        )

        self.stdout.write(self.style.SUCCESS(f'CSI A500 onboarding workflow complete. report_root={report_root}'))