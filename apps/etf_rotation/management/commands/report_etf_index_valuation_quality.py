"""File the #66 evidence: four valuation rules plus the spot-check table.

This is the archivable version of the block that ``validate_data_quality`` writes to a
timestamped, untracked directory. It reads ``IndexValuationDaily``, runs the same pure rules,
and writes a markdown report under ``docs/reports`` so the reason for terminating the family
("6 indices out of the mapped universe") is a checked number, not a remembered one.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.etf_rotation import valuation_qc
from apps.etf_rotation.models import ETFDaily, ETFIndexMapping, IndexValuationDaily


class Command(BaseCommand):
    help = 'Report IndexValuationDaily quality and file the spot-check table (#66).'

    def add_arguments(self, parser):
        parser.add_argument('--start', default=None, help='Panel start (ISO). Default: earliest row.')
        parser.add_argument('--end', default=None, help='Panel end (ISO). Default: latest row.')
        parser.add_argument('--as-of', default=None, help='Staleness reference date (ISO).')
        parser.add_argument('--out', default=None,
                            help='Markdown path. Default: '
                                 'docs/reports/etf-index-valuation-quality-<date>.md')

    def handle(self, *args, **options):
        rows = list(
            IndexValuationDaily.objects.order_by('index_code', 'date').values(
                'index_code', 'date', 'pe_ttm', 'pb', 'turnover_rate',
            )
        )
        start = date.fromisoformat(options['start']) if options['start'] else (
            min(row['date'] for row in rows) if rows else None
        )
        end = date.fromisoformat(options['end']) if options['end'] else (
            max(row['date'] for row in rows) if rows else None
        )
        if start and end:
            rows = [row for row in rows if start <= row['date'] <= end]
        expected_dates = sorted(set(
            ETFDaily.objects.filter(date__gte=start, date__lte=end)
            .values_list('date', flat=True)
        )) if start and end else []
        mapped = set(ETFIndexMapping.objects.values_list('index_code', flat=True))
        as_of = date.fromisoformat(options['as_of']) if options['as_of'] else date.today()

        report = valuation_qc.quality_report(
            rows, mapped_indices=mapped,
            expected_dates_by_index={code: expected_dates for code in mapped},
            as_of=as_of,
        )
        text = _render(report, start=start, end=end, as_of=as_of)
        out = options['out'] or os.path.join(
            str(settings.BASE_DIR), 'docs/reports',
            f'etf-index-valuation-quality-{date.today().isoformat()}.md',
        )
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding='utf-8')
        self.stdout.write(text)
        self.stdout.write(f'written: {out}')


def _render(report, *, start, end, as_of) -> str:
    coverage = report['coverage']
    null_rates = report['null_rates']
    lines = [
        '# IndexValuationDaily quality (#66)',
        '',
        f'Panel: {start}..{end}. Staleness reference: {as_of}. Rows: {report["total"]}.',
        '',
        '## Four rules',
        '',
        '| rule | reading | verdict |',
        '| :--- | :--- | :--- |',
        f'| PE null rate | {null_rates["pe_ttm"]["null"]}/{null_rates["pe_ttm"]["total"]} '
        f'({_pct(null_rates["pe_ttm"]["rate"])}) | '
        f'{"fail" if any(i["rule"] == "null_rate" and i.get("field") == "pe_ttm" for i in report["issues"]) else "pass"} |',
        f'| PB null rate | {null_rates["pb"]["null"]}/{null_rates["pb"]["total"]} '
        f'({_pct(null_rates["pb"]["rate"])}) | '
        f'{"fail" if any(i["rule"] == "null_rate" and i.get("field") == "pb" for i in report["issues"]) else "pass"} |',
        f'| date gaps | {len(report["missing_dates"])} index(es) with gaps | '
        f'{"fail" if report["missing_dates"] else "pass"} |',
        f'| index coverage | {coverage["covered"]}/{coverage["mapped"]} '
        f'({_pct(coverage["ratio"])}) | '
        f'{"fail" if any(i["rule"] == "coverage" for i in report["issues"]) else "pass"} |',
        f'| staleness | latest {report["staleness"]["latest"]}, '
        f'{report["staleness"]["age_days"]} day(s) | '
        f'{"fail" if report["staleness"]["stale"] else "pass"} |',
        '',
        '## Issues',
        '',
    ]
    if not report['issues']:
        lines.append('- none')
    for issue in report['issues']:
        lines.append(
            f'- **{issue["severity"]}** `{issue["issue_type"]}`: {issue["details"]}'
        )
    lines.extend([
        '',
        '## Spot check',
        '',
        'The reference column is a published plausibility range, not a second feed: '
        'TuShare publishes no second valuation quote on this entitlement.',
        '',
        '| index | date | PE TTM | PB | turnover | plausible | reference |',
        '| :--- | :--- | ---: | ---: | ---: | :--- | :--- |',
    ])
    for row in report['spot_check']:
        lines.append(
            f'| {row["index_code"]} | {row["date"]} | {_num(row["pe_ttm"])} | '
            f'{_num(row["pb"])} | {_num(row["turnover_rate"])} | '
            f'{"yes" if row["plausible"] else "no"} | {row["reference"]} |'
        )
    lines.extend([
        '',
        '## Why the family was terminated',
        '',
        f'Index coverage against the mapped universe is {coverage["covered"]}/{coverage["mapped"]} '
        f'({_pct(coverage["ratio"])}); the feature therefore cannot rank most of the pool, and a '
        'cross-sectional model would treat "no valuation" as a systematic characteristic of the '
        'uncovered names. That is a coverage failure, not a tuning problem.',
        '',
    ])
    return '\n'.join(lines)


def _pct(value):
    return 'n/a' if value is None else f'{value:.2%}'


def _num(value):
    return 'n/a' if value is None else f'{float(value):.4f}'
