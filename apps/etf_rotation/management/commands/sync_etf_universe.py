"""Refresh ``ETFUniverse`` from TuShare ``fund_basic(market='E')``.

A thin driver for ``tasks.sync_etf_universe``, with two properties worth stating
because they are easy to break from this layer:

1. The task upserts on ``ts_code`` and keeps delisted funds and non-ETF on-exchange
   vehicles. ``is_rankable`` is where the exclusion is recorded, so this command never
   filters on ``status`` -- doing that here would reintroduce the survivorship bias the
   schema exists to avoid. The upside is that re-running the sync is also the way to
   re-apply a changed ``etf_universe`` classifier to every stored row.
2. ``--refresh-first-trade-dates`` runs ``refresh_etf_first_trade_dates`` on its own
   instead of pulling ``fund_basic``. That step is what turns the unrankable stub rows
   ``sync_etf_daily`` creates for undescribed codes into dated, usable universe rows,
   and the moment it is needed is after a bar backfill -- by which time the universe is
   already current, and refetching a few thousand ``fund_basic`` rows to fix dates is
   the expensive way to do it.

``--dry-run`` makes no TuShare call and writes nothing; it reports the state the sync
would be judged against.
"""

import time

from django.core.management.base import BaseCommand

from apps.etf_rotation import tasks
from apps.etf_rotation.models import ETFDaily, ETFUniverse


# ``CELERY_TASK_ROUTES`` in config/settings/base.py is what actually puts these
# dispatches on that queue; keep the two in step.
ETF_ROTATION_QUEUE = 'etf-rotation'


class Command(BaseCommand):
    help = 'Refresh ETFUniverse from TuShare fund_basic(market="E").'

    def add_arguments(self, parser):
        parser.add_argument(
            '--queue',
            action='store_true',
            help='Dispatch the sync to Celery instead of running inline.',
        )
        parser.add_argument(
            '--refresh-first-trade-dates',
            action='store_true',
            help='Only re-derive first_trade_date and repair blank list_date from stored bars; skip fund_basic.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print the current universe counts; calls no provider and writes nothing.',
        )

    @staticmethod
    def _state_report():
        return (
            f'universe_total={ETFUniverse.objects.count()} '
            f'rankable={ETFUniverse.objects.filter(is_rankable=True).count()} '
            f'active={ETFUniverse.objects.filter(is_active=True).count()} '
            f'delisted={ETFUniverse.objects.filter(status=ETFUniverse.Status.DELISTED).count()} '
            f'null_list_date={ETFUniverse.objects.filter(list_date__isnull=True).count()} '
            f'null_first_trade_date={ETFUniverse.objects.filter(first_trade_date__isnull=True).count()} '
            f'etf_daily_rows={ETFDaily.objects.count()}'
        )

    def handle(self, *args, **options):
        refresh_only = options['refresh_first_trade_dates']
        task = tasks.refresh_etf_first_trade_dates if refresh_only else tasks.sync_etf_universe
        label = 'refresh_etf_first_trade_dates' if refresh_only else 'sync_etf_universe'

        # Checked before --queue: dispatching a writing task is not a dry run.
        if options['dry_run']:
            self.stdout.write(f'would run {label}')
            self.stdout.write(self._state_report())
            self.stdout.write(self.style.SUCCESS('Dry run: no TuShare calls, no rows written.'))
            return

        if options['queue']:
            result = task.delay()
            self.stdout.write(self.style.SUCCESS(
                f'Queued {label} task={result.id} queue={ETF_ROTATION_QUEUE}.'
            ))
            return

        started = time.monotonic()
        result = task()
        elapsed = time.monotonic() - started
        if refresh_only:
            # The task returns a bare count, not a dict.
            self.stdout.write(self.style.SUCCESS(
                f'ETF first-trade-date refresh complete: updated={result} '
                f'{self._state_report()} elapsed={elapsed:.1f}s'
            ))
            return

        self.stdout.write(
            f'fetched={result["fetched"]} universe_total={result["universe_total"]} '
            f'rankable_total={result["rankable_total"]} '
            f'first_trade_dates_updated={result["first_trade_dates_updated"]}'
        )
        self.stdout.write(self.style.SUCCESS(
            f'ETF universe sync complete: {self._state_report()} elapsed={elapsed:.1f}s'
        ))
