"""Sync ETF daily bars from TuShare over a date window or the trailing gap window.

Two decisions in here decide whether a ten-year backfill is affordable:

1. The default target list is ``tasks.missing_etf_daily_dates``, i.e. the open trading
   dates in the range that still hold no ``ETFDaily`` row. Re-running an already
   covered range then costs one calendar query instead of one ``fund_daily`` +
   ``fund_adj`` call pair per date, which is what makes an interrupted backfill
   resumable. ``--force`` opts back in to re-downloading every open date, which is
   what a known-bad day needs (restated ``fund_adj`` factor, a date written by an
   interrupted run): ``sync_etf_daily_by_date`` upserts on ``(etf, date)``, so
   re-syncing overwrites in place rather than appending.
2. Trading dates are read from the SSE calendar, but that is only the schedule
   source. ``fund_daily(trade_date=...)`` returns every exchange's funds in one call,
   so a single pass per date covers Shenzhen too and no per-exchange loop is needed.

``--recent N`` is the shorthand for the daily beat: it delegates to
``sync_etf_daily_recent``, so a manual gap fill and the Celery beat schedule run the
same code path.

``--dry-run`` makes no TuShare call and writes nothing -- it is how a backfill gets
sized, and how an unsynced trading calendar gets noticed, before quota is spent on it.
``--queue`` hands one ``sync_etf_daily_by_date`` task per date to a worker instead of
running them in this process.
"""

import time
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.date_floor import get_historical_data_floor
from apps.etf_rotation import tasks
from apps.etf_rotation.models import ETFDaily
from apps.markets.models import ExchangeTradingCalendar


# ``CELERY_TASK_ROUTES`` in config/settings/base.py is what actually puts these
# dispatches on that queue; keep the two in step, because printing a queue nobody
# consumes looks identical to a backfill that is merely slow.
ETF_ROTATION_QUEUE = 'etf-rotation'
CALENDAR_EXCHANGE_CODE = 'SSE'
# A decade-long dry run plans ~2400 dates; the terminal does not need all of them.
MAX_PLANNED_DATES_SHOWN = 30


class Command(BaseCommand):
    help = 'Sync TuShare ETF daily bars into ETFDaily over a date window or the trailing gaps.'

    def add_arguments(self, parser):
        parser.add_argument('--start', help='YYYY-MM-DD, inclusive. Required unless --recent is given.')
        parser.add_argument('--end', default='', help='YYYY-MM-DD, inclusive. Defaults to today.')
        parser.add_argument(
            '--recent',
            type=int,
            default=0,
            help='Fill gaps in the trailing N calendar days via sync_etf_daily_recent. Replaces --start/--end.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Re-sync every open trading date, including dates that already have rows.',
        )
        parser.add_argument(
            '--queue',
            action='store_true',
            help='Dispatch one sync_etf_daily_by_date task per trading date instead of syncing inline.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print the planned dates and current counts; calls no provider and writes nothing.',
        )

    @staticmethod
    def _parse_date(value, name):
        try:
            return date.fromisoformat(str(value).strip())
        except ValueError as exc:
            raise CommandError(f'Invalid {name}: {value}. Expected YYYY-MM-DD.') from exc

    @staticmethod
    def _open_trading_dates(start_date, end_date):
        return list(
            ExchangeTradingCalendar.objects.filter(
                exchange_code=CALENDAR_EXCHANGE_CODE,
                is_open=True,
                trade_date__gte=start_date,
                trade_date__lte=end_date,
            ).values_list('trade_date', flat=True).order_by('trade_date'),
        )

    def _report_plan(self, start_date, end_date, target_dates, force):
        open_dates = self._open_trading_dates(start_date, end_date)
        covered_dates = set(
            ETFDaily.objects.filter(
                date__gte=start_date,
                date__lte=end_date,
            ).values_list('date', flat=True).distinct(),
        )
        self.stdout.write(
            f'{start_date}..{end_date} open_trading_days={len(open_dates)} '
            f'dates_with_rows={len(covered_dates)} dates_to_sync={len(target_dates)} '
            f'selector={"force" if force else "missing_dates_only"}'
        )
        if target_dates:
            shown = ', '.join(day.isoformat() for day in target_dates[:MAX_PLANNED_DATES_SHOWN])
            remaining = len(target_dates) - MAX_PLANNED_DATES_SHOWN
            self.stdout.write(f'plan: {shown}{f" ... (+{remaining} more)" if remaining > 0 else ""}')
        else:
            self.stdout.write('plan: nothing to sync')
        self.stdout.write(self.style.SUCCESS(
            f'Dry run: no TuShare calls, no rows written. etf_daily_rows={ETFDaily.objects.count()}'
        ))

    def _handle_recent(self, lookback_days, options):
        floor_date = get_historical_data_floor()
        end_date = timezone.localdate()
        start_date = end_date - timedelta(days=max(lookback_days, 1))
        # The task rejects a pre-floor date one date at a time; failing up front keeps
        # a wide --recent from dispatching a window that can only raise in the worker.
        if start_date < floor_date:
            raise CommandError(
                f'--recent {lookback_days} reaches {start_date}, '
                f'before HISTORICAL_DATA_FLOOR={floor_date}.'
            )

        if options['dry_run']:
            gap_dates = tasks.missing_etf_daily_dates(start_date, end_date, CALENDAR_EXCHANGE_CODE)
            return self._report_plan(start_date, end_date, gap_dates, force=False)

        if options['queue']:
            result = tasks.sync_etf_daily_recent.delay(lookback_days)
            self.stdout.write(self.style.SUCCESS(
                f'Queued sync_etf_daily_recent(lookback_days={lookback_days}) '
                f'task={result.id} queue={ETF_ROTATION_QUEUE}.'
            ))
            return

        started = time.monotonic()
        result = tasks.sync_etf_daily_recent(lookback_days)
        self.stdout.write(self.style.SUCCESS(
            f'ETF recent sync complete: window_days={lookback_days} '
            f'dates_synced={result["dates_synced"]} rows_written={result["rows_written"]} '
            f'etf_daily_rows={ETFDaily.objects.count()} elapsed={time.monotonic() - started:.1f}s'
        ))

    def handle(self, *args, **options):
        lookback_days = int(options['recent'] or 0)
        if lookback_days > 0:
            if options['start'] or str(options['end'] or '').strip():
                raise CommandError('--recent cannot be combined with --start/--end.')
            if options['force']:
                raise CommandError('--force has no effect with --recent, which only ever fills gaps.')
            return self._handle_recent(lookback_days, options)

        if not options['start']:
            raise CommandError('--start is required unless --recent is given.')

        floor_date = get_historical_data_floor()
        start_date = self._parse_date(options['start'], '--start')
        if start_date < floor_date:
            raise CommandError(f'--start cannot be earlier than HISTORICAL_DATA_FLOOR={floor_date}.')
        end_date = (
            self._parse_date(options['end'], '--end')
            if str(options['end'] or '').strip()
            else timezone.localdate()
        )
        if end_date < start_date:
            raise CommandError('--end must be on or after --start.')

        open_dates = self._open_trading_dates(start_date, end_date)
        if not open_dates:
            raise CommandError(
                f'No open trading dates for {CALENDAR_EXCHANGE_CODE} in {start_date}..{end_date}. '
                'Sync the calendar first (markets backfill_trading_calendar).'
            )

        if options['force']:
            target_dates = open_dates
        else:
            target_dates = tasks.missing_etf_daily_dates(start_date, end_date, CALENDAR_EXCHANGE_CODE)

        if options['dry_run']:
            return self._report_plan(start_date, end_date, target_dates, force=options['force'])

        if not target_dates:
            self.stdout.write(self.style.SUCCESS(
                f'Nothing to sync: all {len(open_dates)} open trading dates in '
                f'{start_date}..{end_date} already have ETFDaily rows.'
            ))
            return

        if options['queue']:
            # ISO strings keep the broker payload plain JSON; the task parses them back.
            for day in target_dates:
                tasks.sync_etf_daily_by_date.delay(day.isoformat())
            self.stdout.write(self.style.SUCCESS(
                f'Dispatched {len(target_dates)} sync_etf_daily_by_date tasks over '
                f'{start_date}..{end_date} to queue={ETF_ROTATION_QUEUE}. '
                'Row counts stay stale until the worker drains them.'
            ))
            return

        total = len(target_dates)
        rows_written = 0
        created_universe = 0
        empty_dates = 0
        started = time.monotonic()
        for index, day in enumerate(target_dates, start=1):
            # Called directly, not via .apply(): the task object is callable and this
            # is how tasks.py drives it from sync_etf_daily_recent, so inline and the
            # beat path execute identical code.
            result = tasks.sync_etf_daily_by_date(day)
            rows = int(result.get('rows_written') or 0)
            rows_written += rows
            created_universe += int(result.get('created_universe') or 0)
            # An open date that returns no bars at all is a market-wide halt or a
            # missing TuShare permission, and it must not read as a plain zero-row day.
            skipped = str(result.get('skipped') or '')
            if skipped:
                empty_dates += 1
            self.stdout.write(
                f'[{index}/{total}] {day.isoformat()}: rows={rows}'
                + (f' skipped={skipped}' if skipped else '')
            )

        self.stdout.write(self.style.SUCCESS(
            f'ETF daily sync complete: dates={total} rows_written={rows_written} '
            f'universe_stubs={created_universe} empty_dates={empty_dates} '
            f'etf_daily_rows={ETFDaily.objects.count()} elapsed={time.monotonic() - started:.1f}s'
        ))
        if created_universe:
            # Bars arrived for codes fund_basic(market='E') never described. They are
            # stored as unrankable stubs, so the pool silently shrinks unless someone
            # re-runs sync_etf_universe -- _ensure_universe_rows returns them to the
            # caller for exactly this message.
            self.stdout.write(self.style.WARNING(
                f'{created_universe} universe stub(s) created: run sync_etf_universe, '
                'then these funds become rankable.'
            ))
