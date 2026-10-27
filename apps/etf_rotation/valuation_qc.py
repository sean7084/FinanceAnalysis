"""Quality rules for ``IndexValuationDaily`` (#66).

The valuation family was terminated because it covers six indices out of the whole mapped
universe, not because a value looked wrong. These rules make that visible: exactly four checks
-- PE/PB null rate, per-index date gaps, index coverage against the mapped universe, and
staleness -- plus a spot-check table so a human can compare the stored values against a public
source before repeating "12.1% coverage" as a reason.

Pure functions only: the caller supplies rows and expected dates, so the rules can be tested
without a database and reused by both the data-quality command and the filed report.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

VALUATION_FIELDS = ('pe_ttm', 'pb')
NULL_RATE_LIMIT = 0.05
MIN_INDEX_COVERAGE = 0.7
MAX_STALENESS_DAYS = 7
SPOT_CHECK_PER_INDEX = 10
SPOT_CHECK_INDICES = 3


def null_rates(rows, fields=VALUATION_FIELDS) -> dict:
    total = len(rows)
    rates = {}
    for field in fields:
        nulls = sum(1 for row in rows if row.get(field) is None)
        rates[field] = {
            'total': total, 'null': nulls, 'rate': (nulls / total) if total else None,
        }
    return rates


def missing_dates(observed, expected) -> list:
    """Expected dates in the observed range that have no row."""
    observed = sorted(observed)
    if not observed:
        return sorted(expected)
    first, last = observed[0], observed[-1]
    have = set(observed)
    return sorted(day for day in expected if first <= day <= last and day not in have)


def index_coverage(covered_indices, mapped_indices) -> dict:
    covered = set(covered_indices)
    mapped = set(mapped_indices)
    return {
        'covered': len(covered), 'mapped': len(mapped),
        'ratio': (len(covered & mapped) / len(mapped)) if mapped else None,
        'missing': sorted(mapped - covered),
    }


def staleness(latest, as_of, max_age_days: int = MAX_STALENESS_DAYS) -> dict:
    if latest is None:
        return {'latest': None, 'age_days': None, 'stale': True, 'max_age_days': max_age_days}
    age = (as_of - latest).days
    return {
        'latest': latest.isoformat(), 'age_days': age,
        'stale': age > max_age_days, 'max_age_days': max_age_days,
    }


def spot_check(rows, *, per_index: int = SPOT_CHECK_PER_INDEX,
               indices: int = SPOT_CHECK_INDICES) -> list:
    """The most-covered indices' latest rows, with a public plausibility column.

    TuShare publishes no second valuation feed on the entitlement we hold, so the reference
    column is a published *range* (PE 0-200, PB 0-50), not a second quote. It is enough to catch
    a unit or scale error; it is explicitly not a reconciliation against another source.
    """
    by_index = defaultdict(list)
    for row in rows:
        by_index[row['index_code']].append(row)
    ranked = sorted(by_index.items(), key=lambda item: (-len(item[1]), item[0]))[:indices]
    table = []
    for index_code, index_rows in ranked:
        ordered = sorted(index_rows, key=lambda row: row['date'])[-per_index:]
        for row in ordered:
            pe = row.get('pe_ttm')
            pb = row.get('pb')
            plausible = (
                pe is not None and pb is not None and 0 < float(pe) <= 200 and 0 < float(pb) <= 50
            )
            table.append({
                'index_code': index_code, 'date': row['date'].isoformat(),
                'pe_ttm': pe, 'pb': pb, 'turnover_rate': row.get('turnover_rate'),
                'reference': 'tushare.index_dailybasic; public range pe 0-200, pb 0-50',
                'plausible': plausible,
            })
    return table


def quality_report(rows, *, mapped_indices, expected_dates_by_index, as_of,
                   max_null_rate: float = NULL_RATE_LIMIT,
                   min_index_coverage: float = MIN_INDEX_COVERAGE,
                   max_age_days: int = MAX_STALENESS_DAYS,
                   per_index: int = SPOT_CHECK_PER_INDEX,
                   indices: int = SPOT_CHECK_INDICES) -> dict:
    """All four rules and the spot check in one dict; no database access."""
    total = len(rows)
    coverage = index_coverage({row['index_code'] for row in rows}, mapped_indices)
    issues = []
    if total == 0:
        issues.append({
            'rule': 'coverage', 'issue_type': 'etf_index_valuation_empty',
            'severity': 'critical',
            'details': 'IndexValuationDaily has no rows: the family is absent, not sparse.',
        })
        return {
            'total': 0, 'empty': True, 'null_rates': null_rates(rows),
            'coverage': coverage,
            'staleness': staleness(None, as_of, max_age_days),
            'missing_dates': {}, 'issues': issues, 'spot_check': [],
        }

    rates = null_rates(rows)
    for field, cell in rates.items():
        if cell['rate'] is not None and cell['rate'] > max_null_rate:
            issues.append({
                'rule': 'null_rate', 'issue_type': 'etf_index_valuation_null_rate',
                'severity': 'warning', 'field': field,
                'details': f'{field} is null in {cell["null"]}/{cell["total"]} rows '
                           f'({cell["rate"]:.2%}) > {max_null_rate:.0%}.',
            })

    observed = defaultdict(list)
    for row in rows:
        observed[row['index_code']].append(row['date'])
    missing = {
        code: missing_dates(dates, expected_dates_by_index.get(code, ()))
        for code, dates in observed.items()
    }
    missing = {code: gaps for code, gaps in missing.items() if gaps}
    for code, gaps in sorted(missing.items()):
        issues.append({
            'rule': 'date_gaps', 'issue_type': 'etf_index_valuation_date_gaps',
            'severity': 'warning', 'index_code': code,
            'details': f'{len(gaps)} expected trading date(s) missing '
                       f'({gaps[0]}..{gaps[-1]}).',
        })

    if coverage['ratio'] is not None and coverage['ratio'] < min_index_coverage:
        issues.append({
            'rule': 'coverage', 'issue_type': 'etf_index_valuation_index_coverage',
            'severity': 'critical',
            'details': f'{coverage["covered"]}/{coverage["mapped"]} mapped indices have '
                       f'valuation rows ({coverage["ratio"]:.2%}) < {min_index_coverage:.0%}.',
        })

    latest = max(row['date'] for row in rows)
    stale = staleness(latest, as_of, max_age_days)
    if stale['stale']:
        issues.append({
            'rule': 'staleness', 'issue_type': 'etf_index_valuation_stale',
            'severity': 'warning',
            'details': f'Latest row is {stale["latest"]}, {stale["age_days"]} day(s) old '
                       f'(limit {max_age_days}).',
        })

    return {
        'total': total, 'empty': False, 'null_rates': rates, 'coverage': coverage,
        'staleness': stale, 'missing_dates': missing, 'issues': issues,
        'spot_check': spot_check(rows, per_index=per_index, indices=indices),
    }
