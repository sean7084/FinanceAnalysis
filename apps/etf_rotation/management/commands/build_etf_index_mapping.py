"""Resolve which index each ETF tracks, from TuShare ``fund_basic`` + ``index_basic``.

``ETFUniverse.track_index_key`` is prospectus prose, so valuation features could not join to
``index_dailybasic`` without a resolved code. ``fund_basic`` publishes a ``benchmark`` string
per fund ("沪深300指数×100%") and ``index_basic`` publishes the catalogue of index names, so
the mapping is a name match against those two, recorded with its evidence and a confidence.

The confidence is the point. A benchmark that names one index is ``exact``; a composite
benchmark ("中证800×70% + 中债综合×30%") resolves only its equity leg and is ``partial``,
because the ETF's returns are not that index's returns. Anything unmatched stays absent --
never guessed -- and the coverage report says how much of the pool that costs.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import tushare as ts
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.etf_rotation.coverage_bias import coverage_counts
from apps.etf_rotation.models import ETFIndexMapping, ETFUniverse

#: Strips the decorations funds put around an index name in their benchmark line.
NOISE = re.compile(r'(全收益|价格|收益率|指数|成分|×\s*\d+(\.\d+)?%|\s|（.*?）|\(.*?\))')
COMPOSITE_HINT = re.compile(r'[+＋]|、|\+|,')


def normalise(text: str) -> str:
    return NOISE.sub('', str(text or '')).strip().lower()


def match_index(benchmark: str, catalogue: dict) -> tuple[str, str, str] | None:
    """``(index_code, index_name, confidence)`` for the best name match, or ``None``.

    The longest catalogue name contained in the benchmark wins: "中证800银行" must beat
    "中证800" for a bank ETF. A composite benchmark is ``partial`` because only its equity
    leg is being mapped.
    """
    target = normalise(benchmark)
    if not target:
        return None
    best = None
    for name, code in catalogue.items():
        key = normalise(name)
        if key and key in target and (best is None or len(key) > len(normalise(best[1]))):
            best = (code, name)
    if best is None:
        return None
    confidence = ETFIndexMapping.Confidence.PARTIAL if COMPOSITE_HINT.search(benchmark) \
        else ETFIndexMapping.Confidence.EXACT
    return best[0], best[1], confidence


class Command(BaseCommand):
    help = 'Resolve ETF -> tracked index codes from fund_basic + index_basic.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Resolve and report; write nothing.')
        parser.add_argument('--report', default='docs/reports/etf-index-mapping-coverage.md',
                            help='Coverage report path (default docs/reports/...).')
        parser.add_argument('--pause', type=float, default=0.3,
                            help='Seconds between TuShare calls (default 0.3).')

    def handle(self, *args, **options):
        token = getattr(settings, 'TUSHARE_TOKEN', None)
        if not token:
            raise CommandError('TUSHARE_TOKEN is not configured.')
        pro = ts.pro_api(token)

        catalogue = {}
        for market in ('SSE', 'SZSE', 'CSI'):
            offset = 0
            while True:
                frame = pro.index_basic(market=market, limit=5000, offset=offset)
                if frame is None or frame.empty:
                    break
                for row in frame.itertuples(index=False):
                    catalogue[str(row.name)] = str(row.ts_code)
                offset += len(frame)
                if len(frame) < 5000:
                    break
                time.sleep(options['pause'])
        self.stdout.write(f'index catalogue: {len(catalogue)} name(s)')

        benchmarks = {}
        offset = 0
        while True:
            frame = pro.fund_basic(market='E', limit=5000, offset=offset, status='L')
            if frame is None or frame.empty:
                break
            for row in frame.itertuples(index=False):
                benchmarks[str(row.ts_code)] = str(getattr(row, 'benchmark', '') or '')
            offset += len(frame)
            if len(frame) < 5000:
                break
            time.sleep(options['pause'])
        self.stdout.write(f'ETF benchmarks: {len(benchmarks)} row(s)')

        universe = {str(code): pk for code, pk in ETFUniverse.objects.values_list('ts_code', 'id')}
        resolved, unresolved = [], []
        for code, benchmark in benchmarks.items():
            if code not in universe:
                continue
            matched = match_index(benchmark, catalogue)
            if matched is None:
                unresolved.append((code, benchmark))
                continue
            resolved.append((code, *matched, benchmark))

        exact = sum(1 for item in resolved if item[3] == ETFIndexMapping.Confidence.EXACT)
        if not options['dry_run']:
            with transaction.atomic():
                for code, index_code, index_name, confidence, benchmark in resolved:
                    ETFIndexMapping.objects.update_or_create(
                        etf_id=universe[code],
                        defaults={
                            'index_code': index_code, 'index_name': index_name,
                            'confidence': confidence, 'benchmark_text': benchmark,
                            'source': 'fund_basic+index_basic',
                        },
                    )

        rankable_codes = set(
            ETFUniverse.objects.filter(is_active=True, is_rankable=True)
            .values_list('ts_code', flat=True)
        )
        eligible_codes = set(
            ETFFeatureSnapshot.objects.filter(is_eligible=True)
            .values_list('etf__ts_code', flat=True).distinct()
        )
        book_codes = set(ETFRotationPosition.objects.values_list('etf__ts_code', flat=True))
        for selected in ETFRotationSignal.objects.values_list('selected_etfs', flat=True):
            for item in selected or []:
                if isinstance(item, dict) and item.get('ts_code'):
                    book_codes.add(str(item['ts_code']))
        mapped_codes = {code for code, *_rest in resolved}
        counts = coverage_counts(
            mapped=mapped_codes, eligible=eligible_codes,
            book=book_codes, rankable=rankable_codes,
        )
        pool_total = counts['eligible']['total']
        mapped_pool = counts['eligible']['mapped']
        lines = [
            '# ETF → 跟踪指数映射覆盖率',
            '',
            f'- 指数目录：{len(catalogue)} 个名称；ETF benchmark：{len(benchmarks)} 行。',
            f'- 在 ETFUniverse 内可匹配：{len(resolved)}（exact {exact} / partial {len(resolved) - exact}）。',
            f'- 未匹配：{len(unresolved)}。',
            '- 分母 (i) 曾 ADV-合格（预登记判定）：'
            + (f'{counts["eligible"]["mapped"]}/{pool_total} = {counts["eligible"]["ratio"]:.1%}'
               if pool_total else 'n/a'),
            '- 分母 (ii) 曾进过持仓书：'
            + (f'{counts["book"]["mapped"]}/{counts["book"]["total"]} = {counts["book"]["ratio"]:.1%}'
               if counts['book']['total'] else 'n/a'),
            '- 分母 (iii) 当前 rankable（仅作对照，不作判定）：'
            + (f'{counts["rankable"]["mapped"]}/{counts["rankable"]["total"]} = '
               f'{counts["rankable"]["ratio"]:.1%}' if counts['rankable']['total'] else 'n/a'),
            '',
            '## 未匹配样例（前 20 条）',
            '',
            '| ETF | benchmark |',
            '| :--- | :--- |',
            *[f'| {code} | {text} |' for code, text in unresolved[:20]],
        ]
        report = '\n'.join(lines) + '\n'
        path = Path(options['report'])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report, encoding='utf-8')
        self.stdout.write(report)
        self.stdout.write(f'written: {path}')

        threshold = float(getattr(settings, 'ETF_INDEX_MAPPING_MIN_COVERAGE', 0.7))
        if pool_total and mapped_pool / pool_total < threshold:
            self.stdout.write(self.style.WARNING(
                f'coverage {mapped_pool / pool_total:.1%} is below the pre-registered '
                f'{threshold:.0%}: valuation features are not representative enough to use.'
            ))
        elif not options['dry_run']:
            self.stdout.write(self.style.SUCCESS('coverage above the pre-registered threshold'))
