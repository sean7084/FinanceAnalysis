"""Compute ETF feature snapshots over a date window.

Wraps :func:`apps.etf_rotation.features.compute_features`, the only writer of
``ETFFeatureSnapshot``. The command exists so a multi-year recomputation can be started,
interrupted and restarted from a shell (the upsert is keyed on ``(etf, date)``, so a
re-run is idempotent) and so the per-year-chunk timings land in a terminal instead of a
worker log.

``--dry-run`` runs the whole pandas/TA-Lib pipeline and reports what it *would* write.
That is the cheap way to size a backfill, and the way to see what a pool-parameters change
does before the write that would do it for real: ``--min-amount`` changes the eligible
cross-section, which changes every ``rank_pct_20d``/``rank_pct_60d``/``label_rank_pct`` in
the training set.

``--recompute-labels-only`` re-derives just the label columns of rows that already exist,
for the case where the label horizon changed but the features did not.
"""

from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.date_floor import get_historical_data_floor
from apps.etf_rotation.features import compute_features
from apps.etf_rotation.models import ETFFeatureSnapshot


class Command(BaseCommand):
    help = 'Compute point-in-time ETF features and labels into ETFFeatureSnapshot.'

    def add_arguments(self, parser):
        parser.add_argument('--start', help='YYYY-MM-DD, inclusive.')
        parser.add_argument('--end', default='', help='YYYY-MM-DD, inclusive. Defaults to today.')
        parser.add_argument(
            '--years',
            type=int,
            default=0,
            help='Trailing N calendar years ending today; replaces --start/--end.',
        )
        parser.add_argument('--horizon', type=int, default=0, help='Label horizon in trading sessions.')
        parser.add_argument(
            '--min-amount',
            type=float,
            default=0.0,
            help='Pool entry: 20-session average turnover in yuan (ETFDaily.amount is yuan, not thousands).',
        )
        parser.add_argument('--min-age-days', type=int, default=0, help='Pool entry: minimum days since listing.')
        parser.add_argument('--batch-size', type=int, default=0, help='Rows per upsert batch.')
        parser.add_argument('--dry-run', action='store_true', help='Compute and report; write nothing.')
        parser.add_argument(
            '--recompute-labels-only',
            action='store_true',
            help='Update the label columns of existing rows only; never insert, never touch a feature.',
        )

    def handle(self, *args, **options):
        start_day, end_day = self._resolve_window(options)

        overrides = {}
        for kwarg, key in (
            ('label_horizon_days', 'horizon'),
            ('min_avg_amount_yuan', 'min_amount'),
            ('min_listing_age_days', 'min_age_days'),
            ('batch_size', 'batch_size'),
        ):
            if options[key]:
                overrides[kwarg] = options[key]

        if options['verbosity'] > 0:
            tuned = ', '.join(f'{key}={value}' for key, value in overrides.items()) or 'module defaults'
            self.stdout.write(f'Computing ETF features {start_day}..{end_day} ({tuned})')

        try:
            summary = compute_features(
                start_day,
                end_day,
                dry_run=options['dry_run'],
                labels_only=options['recompute_labels_only'],
                progress=self._report_chunk if options['verbosity'] > 0 else None,
                **overrides,
            )
        except ValueError as error:
            raise CommandError(str(error)) from error

        if options['verbosity'] > 0:
            self._report_table(end_day)

        written = '0 (dry-run)' if options['dry_run'] else f'{summary["rows_written"]}'
        self.stdout.write(self.style.SUCCESS(
            f'compute_etf_features {start_day}..{end_day}: '
            f'wrote {written} of {summary["rows_computed"]} computed rows '
            f'({summary["eligible_rows"]} eligible, {summary["labeled_rows"]} labeled) '
            f'across {summary["dates"]} sessions / {summary["etfs"]} ETFs '
            f'in {summary["chunks"]} chunk(s), {summary["elapsed_seconds"]}s. '
            f'benchmark_missing_sessions={summary["benchmark_missing_sessions"]} '
            f'skipped_missing_snapshot={summary["rows_skipped_missing_snapshot"]}'
        ))
        # BaseCommand.execute() prints whatever handle() returns, so this must stay a
        # string: a dict return value raises AttributeError long after the work succeeded.
        return ''

    def _resolve_window(self, options) -> tuple[date, date]:
        today = timezone.localdate()
        if options['years']:
            if options['start'] or options['end']:
                raise CommandError('--years replaces --start/--end; give one or the other.')
            return self._first_day_of_trailing_years(int(options['years'])), today

        if not options['start']:
            raise CommandError('--start YYYY-MM-DD is required (or --years N).')

        start_day = self._parse_date(options['start'], 'start')
        end_day = self._parse_date(options['end'], 'end') if options['end'] else today

        # The floor bounds what may be *written*; the pipeline still reads ~250 sessions of
        # warm-up history before it, because those bars are never stored, only consumed.
        floor_date = get_historical_data_floor()
        if start_day < floor_date:
            raise CommandError(f'start cannot be earlier than HISTORICAL_DATA_FLOOR={floor_date}.')
        if end_day < start_day:
            raise CommandError('end must be on or after start.')
        return start_day, end_day

    def _parse_date(self, value, name):
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise CommandError(f'Invalid {name}: {value}. Expected YYYY-MM-DD.') from exc

    def _first_day_of_trailing_years(self, years: int) -> date:
        """The day after the N-th anniversary, so ``--years 1`` is a full year of sessions."""
        today = timezone.localdate()
        try:
            boundary = today.replace(year=today.year - years)
        except ValueError:  # February 29 has no anniversary in a common year.
            boundary = today.replace(year=today.year - years, day=28)
        return boundary + timedelta(days=1)

    def _report_chunk(self, chunk: dict) -> None:
        index = chunk['chunk_index']
        total = chunk['chunk_count']
        suffix = ' (dry-run)' if chunk['dry_run'] else ''
        rows = chunk['rows'] if chunk['rows'] == chunk['rows_computed'] else f"{chunk['rows']}/{chunk['rows_computed']}"
        self.stdout.write(
            f'  [{index}/{total}] {chunk["start"]}..{chunk["end"]}: '
            f'rows={rows} dates={chunk["dates"]} funds={chunk["funds"]} '
            f'benchmark_missing_sessions={chunk["benchmark_missing_sessions"]}{suffix}'
        )

    def _report_table(self, end_day: date) -> None:
        """The tail of the table, because that is where short-history features (``mom_250d``)
        and not-yet-labeled sessions pile up and where a window looks most unlike the rest."""
        latest_day = (
            ETFFeatureSnapshot.objects.filter(date__lte=end_day)
            .order_by('-date')
            .values_list('date', flat=True)
            .first()
        )
        if latest_day is None:
            self.stdout.write(self.style.WARNING('ETFFeatureSnapshot holds no rows on or before this window.'))
            return
        self.stdout.write(
            f'latest snapshot date {latest_day}; rows on it='
            f'{ETFFeatureSnapshot.objects.filter(date=latest_day).count()}'
        )
