"""Coverage denominators and the coverage-bias measurement.

Two questions, one module: "is the mapping/feature coverage measured against the pool that
was pre-registered?" and "do the funds that *have* the data earn different forward returns
than the funds that do not?" The second is the confound that makes a data-rich subset look
like alpha, so it is measured, not assumed away.
"""
from __future__ import annotations


def coverage_counts(*, mapped, eligible, book, rankable) -> dict:
    """Three denominators side by side; the floor is judged on ``eligible`` only.

    ``eligible`` is "ever passed the ADV screen", ``book`` is "ever entered a live or
    backtest book", ``rankable`` is today's survivors. The last is kept only for continuity
    with the old report; it is not the pre-registered denominator.
    """
    def ratio(mapped_count, total):
        return (mapped_count / total) if total else None

    return {
        'eligible': {'mapped': len(mapped & eligible), 'total': len(eligible),
                     'ratio': ratio(len(mapped & eligible), len(eligible))},
        'book': {'mapped': len(mapped & book), 'total': len(book),
                 'ratio': ratio(len(mapped & book), len(book))},
        'rankable': {'mapped': len(mapped & rankable), 'total': len(rankable),
                     'ratio': ratio(len(mapped & rankable), len(rankable))},
    }


def flag_bias(rows, flag: str) -> dict:
    """Forward-return spread between rows that have ``flag`` and rows that do not.

    ``rows`` are dicts with ``date``, ``fwd_return`` and the flag. Per date the eligible
    cross-section is split on the flag; the table reports the mean spread (have minus
    have-not) and the share of dates where the spread is positive. A positive, consistent
    spread means the flag predicts returns -- a coverage bias, not validation of the signal.
    """
    by_date: dict = {}
    for row in rows:
        value = row.get(flag)
        if value is None or row.get('fwd_return') is None:
            continue
        by_date.setdefault(row['date'], {True: [], False: []})[bool(value)].append(
            float(row['fwd_return'])
        )
    spreads = []
    for groups in by_date.values():
        if groups[True] and groups[False]:
            spreads.append(
                sum(groups[True]) / len(groups[True]) - sum(groups[False]) / len(groups[False])
            )
    if not spreads:
        return {'flag': flag, 'dates': 0, 'mean_spread': None, 'positive_share': None}
    return {
        'flag': flag,
        'dates': len(spreads),
        'mean_spread': sum(spreads) / len(spreads),
        'positive_share': sum(1 for value in spreads if value > 0) / len(spreads),
    }


def book_fill_rate(book_codes, covered_codes) -> dict:
    """Share of names that ever entered a book and have the data."""
    total = len(book_codes)
    return {
        'total': total,
        'covered': len(book_codes & covered_codes),
        'ratio': (len(book_codes & covered_codes) / total) if total else None,
    }
