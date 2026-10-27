"""Render the measurement ledger and flag contexts that have been read more than once."""
from __future__ import annotations

import json
from datetime import date
from statistics import mean, pstdev

from django.core.management.base import BaseCommand

from apps.etf_rotation import evidence
from apps.etf_rotation.management.commands.run_etf_rotation_backtest import (
    default_execution_context,
)


class Command(BaseCommand):
    help = 'Render the ETF rotation measurement ledger, flagging repeated contexts.'

    def add_arguments(self, parser):
        parser.add_argument('--label-table', action='store_true',
                            help='Pivot rows that store per-block IC by label: mean IC, ICIR, '
                                 'median Sharpe. No run is repeated; this reads the ledger.')
        parser.add_argument('--ablation-table', action='store_true',
                            help='Pivot the same rows by (arm, label, feature set, schema) -- the '
                                 'ablation matrix, from measurements that already exist.')

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
                evidence.normalized_context(context, _defaults_for_context(context, defaults)),
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
        if options.get('label_table') or options.get('ablation_table'):
            rows = measurement_pivot(records)
            if not rows:
                self.stdout.write('no ledger row stores per-block IC yet; nothing to pivot.')
                return
            self.stdout.write('label' if options.get('label_table') else 'arm x label x features')
            self.stdout.write(
                f'{"key":<40} {"rows":>4} {"ic_mean":>9} {"icir":>7} {"sharpe":>8}'
            )
            for key, cell in sorted(rows.items()):
                icir = 'n/a' if cell['icir'] is None else f"{cell['icir']:.4f}"
                sharpe = 'n/a' if cell['sharpe'] is None else f"{cell['sharpe']:.4f}"
                self.stdout.write(
                    f'{key:<40} {cell["rows"]:>4} {cell["ic_mean"]:>9.4f} {icir:>7} {sharpe:>8}'
                )


def measurement_pivot(records) -> dict:
    """Aggregate rows that already store per-block IC; no run is repeated.

    ``label`` keys by label alone; the ablation view keys by arm, label and feature set.
    The feature-set axis is carried in the context when a run declares one, so a subset gets
    its own key (and its own schema hash) instead of sharing a row with the full set.
    """
    grouped: dict = {}
    for record in records:
        results = record.get('results') or {}
        blocks = results.get('blocks') or []
        ics = [
            float(block['mean_ic']) for block in blocks
            if isinstance(block, dict) and block.get('mean_ic') is not None
        ]
        if not ics:
            continue
        context = record.get('context') or {}
        arm = f"{context.get('model_type', '?')}{'+hybrid' if context.get('hybrid') else ''}"
        label = str(context.get('label') or '-')
        features = str(context.get('feature_set') or 'all')
        schema = str(context.get('feature_schema_version') or '-')
        key = f'{arm} | {label} | {features} | {schema}'
        cell = grouped.setdefault(key, {'ics': [], 'sharpes': []})
        cell['ics'].extend(ics)
        sharpe = (results.get('sharpe') or {}).get('median')
        if isinstance(sharpe, (int, float)):
            cell['sharpes'].append(float(sharpe))
    pivot = {}
    for key, cell in grouped.items():
        ics = cell['ics']
        spread = pstdev(ics) if len(ics) > 1 else 0.0
        pivot[key] = {
            'rows': len(ics),
            'ic_mean': mean(ics),
            'icir': (mean(ics) / spread) if spread else None,
            'sharpe': mean(cell['sharpes']) if cell['sharpes'] else None,
        }
    return pivot


def _defaults_for_context(context: dict, cache: dict) -> dict:
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
