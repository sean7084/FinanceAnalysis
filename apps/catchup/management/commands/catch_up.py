import json
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from apps.catchup import registry
from apps.catchup.models import CatchUpRun
from apps.catchup.service import run_catch_up


class Command(BaseCommand):
    help = (
        'Catch up on the data holes an outage left behind. Beat never replays a '
        'missed occurrence, so after the box has been off every series with a fixed '
        'trailing window is short by the length of the outage. --dry-run only runs '
        'the detectors and spends no TuShare quota.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Detect and report gaps without repairing anything.')
        parser.add_argument('--series', default='',
                            help=f'Comma-separated subset of: {",".join(registry.all_names())}')
        parser.add_argument('--max-calls', type=int, default=None,
                            help='TuShare call budget for this run.')
        parser.add_argument('--max-seconds', type=int, default=None,
                            help='Wall-clock budget for this run.')
        parser.add_argument('--trigger', default=CatchUpRun.Trigger.MANUAL,
                            choices=[value for value, _ in CatchUpRun.Trigger.choices])
        parser.add_argument('--as-of', default='',
                            help='Override today as YYYY-MM-DD. For testing.')
        parser.add_argument('--json', action='store_true', dest='as_json',
                            help='Machine-readable output.')

    def handle(self, *args, **options):
        names = tuple(
            name.strip() for name in options['series'].split(',') if name.strip()
        ) or None
        unknown = set(names or ()) - set(registry.all_names())
        if unknown:
            raise CommandError(
                f'unknown series: {", ".join(sorted(unknown))}. '
                f'Known: {", ".join(registry.all_names())}'
            )
        rows = run_catch_up(
            as_of=self._parse_as_of(options['as_of']),
            series=names,
            max_calls=options['max_calls'],
            max_seconds=options['max_seconds'],
            dry_run=options['dry_run'],
            trigger=options['trigger'],
        )
        if not rows:
            self.stdout.write('no work: another catch-up run holds the lock')
            return
        if options['as_json']:
            self.stdout.write(json.dumps([self._as_dict(row) for row in rows], indent=2))
            return
        self.stdout.write(f'{"series":<22}{"outcome":<18}{"gap":<28}'
                          f'{"calls":>6}  {"remaining":>9}  cause')
        for row in rows:
            gap = ('none' if not row.gap_start
                   else f'{row.gap_start}..{row.gap_end}')
            self.stdout.write(
                f'{row.series:<22}{row.outcome:<18}{gap:<28}'
                f'{row.estimated_calls:>6}  {row.units_remaining:>9}  {row.cause or "-"}'
            )
            if row.detail:
                self.stdout.write(f'{"":<22}{row.detail[:160]}')

    @staticmethod
    def _parse_as_of(raw):
        """Resolve --as-of to a date, or None to let the service pick the session date.

        The service does ``as_of = as_of or session_today()``, so a non-empty string
        survives it and reaches ``calendar_scan_start``, where ``as_of - timedelta``
        raises TypeError before any detector runs. Parse here so a bad value is a
        CommandError naming the flag rather than a traceback from the detectors.
        """
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            raise CommandError(
                f'--as-of must be a date in YYYY-MM-DD form, got {raw!r}.'
            ) from None

    @staticmethod
    def _as_dict(row):
        return {
            'series': row.series,
            'outcome': row.outcome,
            'cause': row.cause,
            'gap_start': row.gap_start.isoformat() if row.gap_start else None,
            'gap_end': row.gap_end.isoformat() if row.gap_end else None,
            'units_done': row.units_done,
            'units_remaining': row.units_remaining,
            'estimated_calls': row.estimated_calls,
            'detail': row.detail,
        }
