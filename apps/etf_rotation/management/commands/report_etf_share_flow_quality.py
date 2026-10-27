"""File the share-flow backfill quality sheet.

The four share-flow columns are in ``FEATURE_COLUMNS``; this command answers the questions
that decision assumed: how much of the panel is covered, how many share jumps are mechanical
(splits / distributions) rather than real flow, and which months have no rows at all.
"""
from __future__ import annotations

import os
from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.etf_rotation.models import ETFDaily, ETFShareDaily
from apps.etf_rotation.share_flow_qc import classify_history


class Command(BaseCommand):
    help = 'Write docs/reports/etf-share-flow-quality.md from ETFShareDaily + adj factors.'

    def add_arguments(self, parser):
        parser.add_argument('--out', default=None,
                            help='Report path (default docs/reports/etf-share-flow-quality.md).')

    def handle(self, *args, **options):
        rows = list(
            ETFShareDaily.objects.order_by('etf_id', 'date')
            .values_list('etf_id', 'date', 'shares')
            .iterator(chunk_size=50_000)
        )
        if not rows:
            self.stdout.write('ETFShareDaily is empty; nothing to report.')
            return
        etf_ids = {row[0] for row in rows}
        first, last = rows[0][1], rows[-1][1]
        adj = {
            (etf_id, day): factor
            for etf_id, day, factor in ETFDaily.objects.filter(
                etf_id__in=etf_ids, date__gte=first, date__lte=last,
            ).values_list('etf_id', 'date', 'adj_factor')
            if factor is not None
        }
        labels = classify_history(rows, adj)
        histogram = Counter(labels)
        months: Counter = Counter()
        for _, day, _ in rows:
            months[(day.year, day.month)] += 1
        bar_dates = {
            (etf_id, day) for etf_id, day in ETFDaily.objects.filter(
                etf_id__in=etf_ids, date__gte=first, date__lte=last,
            ).values_list('etf_id', 'date')
        }
        share_dates = {(etf_id, day) for etf_id, day, _ in rows}
        missing = len(bar_dates - share_dates)
        lines = [
            '# ETF share-flow quality',
            '',
            f'Rows: {len(rows)} across {len(etf_ids)} funds, {first}..{last}.',
            f'Jump classification: split {histogram.get("split", 0)}, '
            f'flow {histogram.get("flow", 0)}, unexplained {histogram.get("unexplained", 0)}. '
            f'Unexplained share: {histogram.get("unexplained", 0) / len(rows):.4%}.',
            f'(Fund, session) pairs with a bar but no share row: {missing}.',
            '',
            '## Rows per month',
            '',
            '| month | rows |',
            '| :--- | ---: |',
        ]
        for (year, month), count in sorted(months.items()):
            lines.append(f'| {year:04d}-{month:02d} | {count} |')
        gaps = []
        cursor = (first.year, first.month)
        while cursor <= (last.year, last.month):
            if months.get(cursor, 0) == 0:
                gaps.append(f'{cursor[0]:04d}-{cursor[1]:02d}')
            year, month = cursor
            cursor = (year + 1, 1) if month == 12 else (year, month + 1)
        lines.extend(['', '## Months with no rows', ''])
        lines.extend([f'- {gap}' for gap in gaps] or ['- none'])
        report = '\n'.join(lines) + '\n'
        out = options['out'] or os.path.join(
            str(settings.BASE_DIR), 'docs', 'reports', 'etf-share-flow-quality.md',
        )
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'w', encoding='utf-8') as handle:
            handle.write(report)
        self.stdout.write(report)
        self.stdout.write(f'written: {out}')
