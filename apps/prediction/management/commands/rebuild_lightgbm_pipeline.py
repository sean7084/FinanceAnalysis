import json
from datetime import date, timedelta

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.date_floor import get_historical_data_floor
from apps.prediction.tasks_lightgbm import train_lightgbm_models


class Command(BaseCommand):
    help = 'Backfill features and retrain 3/7/30-day LightGBM models end-to-end.'

    @staticmethod
    def _default_start_date():
        return get_historical_data_floor()

    def add_arguments(self, parser):
        parser.add_argument(
            '--start-date',
            default=self._default_start_date().isoformat(),
            help='Training start date (YYYY-MM-DD). Defaults to HISTORICAL_DATA_FLOOR.',
        )
        parser.add_argument(
            '--end-date',
            default=(timezone.now().date() - timedelta(days=1)).isoformat(),
            help='Training end date (YYYY-MM-DD). Defaults to yesterday.',
        )
        parser.add_argument(
            '--horizons',
            default='3,7,30',
            help='Comma-separated horizons to train (subset of 3,7,30).',
        )
        parser.add_argument(
            '--skip-backfill',
            action='store_true',
            help='Skip model data backfill and retrain directly.',
        )
        parser.add_argument(
            '--skip-sentiment',
            action='store_true',
            help='Pass through to backfill_model_data to skip sentiment recomputation.',
        )
        parser.add_argument(
            '--version-tag',
            default='',
            help='Optional suffix added to the LightGBM model version to preserve existing artifact families.',
        )
        parser.add_argument(
            '--use-snapshot-pruning',
            action='store_true',
            help='Prune features from the latest active FeatureImportanceSnapshot before retraining.',
        )

    def _parse_date(self, value, name):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise CommandError(f'Invalid {name}: {value}. Expected YYYY-MM-DD.') from exc

    def _parse_horizons(self, raw):
        allowed = {3, 7, 30}
        values = []
        for token in (raw or '').split(','):
            token = token.strip()
            if not token:
                continue
            try:
                horizon = int(token)
            except ValueError as exc:
                raise CommandError(f'Invalid horizon value: {token}') from exc
            if horizon not in allowed:
                raise CommandError('Only horizons 3, 7, and 30 are supported.')
            if horizon not in values:
                values.append(horizon)
        if not values:
            raise CommandError('At least one horizon must be provided.')
        return values

    def handle(self, *args, **options):
        start_date = self._parse_date(options['start_date'], 'start-date')
        end_date = self._parse_date(options['end_date'], 'end-date')
        floor_date = get_historical_data_floor()
        if start_date < floor_date:
            raise CommandError(f'start-date cannot be earlier than HISTORICAL_DATA_FLOOR={floor_date}.')
        if end_date < start_date:
            raise CommandError('end-date must be on or after start-date.')

        horizons = self._parse_horizons(options['horizons'])

        if not options['skip_backfill']:
            self.stdout.write('Running model data backfill...')
            backfill_kwargs = {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
            }
            if options['skip_sentiment']:
                backfill_kwargs['skip_sentiment'] = True
            call_command('backfill_model_data', **backfill_kwargs)

        version_tag = str(options.get('version_tag') or '').strip()
        tag_suffix = f' version_tag={version_tag}' if version_tag else ''
        pruning_suffix = ' snapshot_pruning=on' if options['use_snapshot_pruning'] else ''
        self.stdout.write(
            f'Retraining LightGBM horizons={horizons} for window {start_date.isoformat()} -> {end_date.isoformat()}...{tag_suffix}{pruning_suffix}'
        )

        results = train_lightgbm_models(
            training_start_date=start_date.isoformat(),
            training_end_date=end_date.isoformat(),
            horizons=horizons,
            version_tag=version_tag,
            use_snapshot_pruning=options['use_snapshot_pruning'],
        )

        self.stdout.write(json.dumps(results, ensure_ascii=True, indent=2, sort_keys=True))

        failed = [
            horizon for horizon, payload in results.items()
            if payload.get('status') not in {'success', 'insufficient_data'}
        ]
        if failed:
            raise CommandError(f'Retrain failed for horizons: {failed}')

        self.stdout.write(self.style.SUCCESS('LightGBM rebuild completed.'))
