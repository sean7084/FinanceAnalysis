"""Measure whether the new data columns are a coverage bias in disguise.

The four share-flow columns and the (disabled) valuation/breadth columns exist only for a
subset of the pool. If the funds that have the data earn different forward returns than the
funds that do not, a model can learn "has data" instead of a signal -- and the improvement
would be an artefact of coverage. This command measures that spread from snapshots the panel
already stores and writes one evidence-ledger row. It writes no model, no backtest row.
"""
from __future__ import annotations

import os
from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.etf_rotation import evidence
from apps.etf_rotation.coverage_bias import book_fill_rate, flag_bias
from apps.etf_rotation.models import (
    ETFFeatureSnapshot, ETFRotationPosition, ETFRotationSignal, ETFUniverse,
)
from apps.etf_rotation.train_lightgbm import feature_schema_version
from apps.etf_rotation import features

FLAGS = ('has_share_flow', 'has_index_valuation', 'has_index_breadth')


class Command(BaseCommand):
    help = 'Measure coverage and the forward-return spread of the data-coverage flags.'

    def add_arguments(self, parser):
        parser.add_argument('--start', default='2016-01-29')
        parser.add_argument('--end', default='2023-12-29')
        parser.add_argument('--out', default=None)

    def handle(self, *args, **options):
        start = date.fromisoformat(options['start'])
        end = date.fromisoformat(options['end'])
        rows = list(
            ETFFeatureSnapshot.objects.filter(
                date__gte=start, date__lte=end, is_eligible=True,
            ).values(
                'date', 'etf_id', 'etf__ts_code', 'has_share_flow',
                'has_index_valuation', 'has_index_breadth', 'fwd_return',
            ).iterator(chunk_size=50_000)
        )
        if not rows:
            raise SystemExit('no eligible snapshots in the window; nothing to measure.')
        coverage = {}
        for flag in FLAGS:
            present = sum(1 for row in rows if row[flag] is not None)
            covered = sum(1 for row in rows if row[flag])
            coverage[flag] = {
                'rows': len(rows), 'present': present, 'covered': covered,
                'ratio': (covered / present) if present else None,
            }
        bias = {flag: flag_bias(rows, flag) for flag in FLAGS}
        book_codes = set(ETFRotationPosition.objects.values_list('etf__ts_code', flat=True))
        for selected in ETFRotationSignal.objects.values_list('selected_etfs', flat=True):
            for item in selected or []:
                if isinstance(item, dict) and item.get('ts_code'):
                    book_codes.add(str(item['ts_code']))
        covered_codes = set(
            ETFFeatureSnapshot.objects.filter(
                date__gte=start, date__lte=end, has_share_flow=True,
            ).values_list('etf__ts_code', flat=True).distinct()
        )
        book = book_fill_rate(book_codes, covered_codes)
        context = {
            'measurement': 'coverage_bias',
            'panel_start': start.isoformat(), 'panel_end': end.isoformat(),
            'feature_schema_version': feature_schema_version(features.FEATURE_COLUMNS),
            'flags': list(FLAGS),
        }
        record = evidence.record_measurement(
            context=context,
            results={'coverage': coverage, 'bias': bias, 'book_fill': book},
            verdict=(
                '; '.join(
                    f'{flag}: spread {bias[flag]["mean_spread"]:+.4%} on '
                    f'{bias[flag]["positive_share"]:.0%} of dates'
                    if bias[flag]['mean_spread'] is not None else f'{flag}: n/a'
                    for flag in FLAGS
                )
            ),
        )
        lines = [
            f'# Coverage bias ({start}..{end})',
            '',
            '| flag | present rows | covered | ratio | mean fwd spread | positive dates |',
            '| :--- | ---: | ---: | ---: | ---: | ---: |',
        ]
        for flag in FLAGS:
            cover = coverage[flag]
            spread = bias[flag]
            ratio = 'n/a' if cover['ratio'] is None else format(cover['ratio'], '.1%')
            mean_spread = (
                'n/a' if spread['mean_spread'] is None else format(spread['mean_spread'], '+.4%')
            )
            positive = (
                'n/a' if spread['positive_share'] is None
                else format(spread['positive_share'], '.0%')
            )
            lines.append(
                f'| {flag} | {cover["present"]} | {cover["covered"]} | {ratio} | '
                f'{mean_spread} | {positive} |'
            )

        lines.extend([
            '',
            f'Book names: {book["covered"]}/{book["total"]} have share flow'
            f' ({"n/a" if book["ratio"] is None else format(book["ratio"], ".1%")}).',
            '',
            f'Ledger row: `{record["id"]}`.',
        ])
        report = '\n'.join(lines) + '\n'
        self.stdout.write(report)
        out = options['out'] or os.path.join(
            str(settings.BASE_DIR), 'docs', 'reports', 'etf-coverage-bias.md',
        )
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'w', encoding='utf-8') as handle:
            handle.write(report)
        self.stdout.write(f'written: {out}')
