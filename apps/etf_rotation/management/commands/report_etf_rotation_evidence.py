"""Render the measurement ledger and flag contexts that have been read more than once."""
from __future__ import annotations

import json
from datetime import date

from django.core.management.base import BaseCommand

from apps.etf_rotation import evidence
from apps.etf_rotation.management.commands.run_etf_rotation_backtest import (
    default_execution_context,
)


class Command(BaseCommand):
    help = 'Render the ETF rotation measurement ledger, flagging repeated contexts.'

    def handle(self, *args, **options):
        records = evidence.read_measurements()
        if not records:
            self.stdout.write('no measurements recorded yet')
            return
        seen = {}
        defaults = {}
        self.stdout.write(
            'id            created_at            arm                  panel                 '
            'purge  folds  median_sharpe  prereg        repeat'
        )
        for record in records:
            context = record.get('context') or {}
            family = (
                f"{context.get('model_type', '?')}"
                f"{'+hybrid' if context.get('hybrid') else ''}"
                f"{'/' + str(context.get('model_version')) if context.get('model_version') else ''}"
            )
            panel = f"{context.get('panel_start')}..{context.get('panel_end')}"
            results = record.get('results') or {}
            sharpe = (results.get('sharpe') or {}).get('median')
            signature = json.dumps(
                evidence.normalized_context(context, self._defaults_for(context, defaults)),
                sort_keys=True, default=str,
            )
            seen[signature] = seen.get(signature, 0) + 1
            self.stdout.write(
                f"{record.get('id', '-'):<13} {str(record.get('created_at', ''))[:19]:<21} "
                f"{family[:20]:<20} {panel:<21} {str(context.get('purge', '-')):<6} "
                f"{str(context.get('fold_plan', '-')):<6} "
                f"{('%.4f' % sharpe) if isinstance(sharpe, (int, float)) else 'n/a':>13}  "
                f"{str(record.get('preregistration') or '-'):<13} "
                f"{seen[signature]}"
            )
        repeats = sum(1 for count in seen.values() if count > 1)
        panels = {}
        for record in records:
            context = record.get('context') or {}
            key = f"{context.get('panel_start')}..{context.get('panel_end')}"
            panels[key] = panels.get(key, 0) + 1
        self.stdout.write('reads per panel:')
        for panel, count in sorted(panels.items()):
            self.stdout.write(f'  {panel}  {count} read(s)')
        unregistered = sum(1 for record in records if not record.get('preregistration'))
        if repeats:
            self.stdout.write(self.style.WARNING(
                f'{repeats} context(s) have been measured more than once; the repeat column '
                'counts them in read order.'
            ))
        if unregistered:
            self.stdout.write(self.style.WARNING(
                f'{unregistered} of {len(records)} row(s) carry no pre-registration hash: they '
                'were measured before a criterion was declared, so they cannot support a gate.'
            ))

    @staticmethod
    def _defaults_for(context: dict, cache: dict) -> dict:
        """Engine defaults for this row's panel, so pre-``execution`` rows still group."""
        key = (str(context.get('panel_start')), str(context.get('panel_end')))
        if key not in cache:
            try:
                start = date.fromisoformat(key[0])
                end = date.fromisoformat(key[1])
            except ValueError:
                cache[key] = {}
            else:
                cache[key] = default_execution_context(start, end)
        return cache[key]
