"""Decision-gate arithmetic over ledger rows.

The P35 gate was pre-registered with two numbers -- mean per-fold IC >= 0.06 and
ICIR >= 0.5 -- and the first adjudication argued from replication counts instead. This
module computes the declared criteria from rows the ledger already stores, so the verdict
can be re-derived without spending the panel again. Nothing here fits anything; it only
reads ``results.blocks[].mean_ic`` and reports which criterion failed.
"""
from __future__ import annotations

from statistics import mean, pstdev

P35_MIN_MEAN_IC = 0.06
P35_MIN_ICIR = 0.5


def _block_ics(record: dict) -> list[float]:
    blocks = (record.get('results') or {}).get('blocks') or []
    return [
        float(block['mean_ic']) for block in blocks
        if isinstance(block, dict) and block.get('mean_ic') is not None
    ]


def _cell_key(record: dict) -> str:
    context = record.get('context') or {}
    arm = f"{context.get('model_type', '?')}{'+hybrid' if context.get('hybrid') else ''}"
    # The schema is part of the identity: pooling two feature sets would average numbers the
    # discipline forbids comparing in one table.
    schema = context.get('feature_schema_version') or '-'
    return (
        f"{arm} | {context.get('label') or '-'} | {context.get('fold_plan') or '?'} | {schema}"
    )


def p35_verdict(rows, *, min_mean_ic: float = P35_MIN_MEAN_IC,
                min_icir: float = P35_MIN_ICIR) -> list[dict]:
    """Per-cell pass/fail against the declared P35 criteria, naming what failed.

    All rows sharing a cell key are pooled: a repeat of the same context is another draw of
    the same measurement, and dropping it would let the verdict depend on which run happened
    to be printed. Cells with no per-block IC (RULE fits no scorer; pinned runs that skip the
    IC block) are omitted rather than counted as failures -- they were never eligible.
    """
    pooled: dict[str, list[float]] = {}
    seen: dict[str, int] = {}
    for record in rows:
        ics = _block_ics(record)
        if not ics:
            continue
        key = _cell_key(record)
        pooled.setdefault(key, []).extend(ics)
        seen[key] = seen.get(key, 0) + 1
    verdicts = []
    for key, ics in sorted(pooled.items()):
        ic_mean = mean(ics)
        spread = pstdev(ics) if len(ics) > 1 else 0.0
        icir = (ic_mean / spread) if spread else None
        failed = []
        if ic_mean < min_mean_ic:
            failed.append('mean_ic')
        if icir is None or icir < min_icir:
            failed.append('icir')
        verdicts.append({
            'key': key, 'rows': seen[key], 'blocks': len(ics),
            'ic_mean': ic_mean, 'icir': icir,
            'passed': not failed, 'failed': failed,
        })
    return verdicts


def seed_variance_table(rows) -> list[dict]:
    """Sharpe and IC spread per (cell, seeds): the table the seed-averaging claim lacked."""
    grouped: dict[tuple, dict] = {}
    for record in rows:
        context = record.get('context') or {}
        key = (_cell_key(record), int(context.get('seeds') or 1))
        cell = grouped.setdefault(key, {'sharpes': [], 'ics': []})
        sharpe = ((record.get('results') or {}).get('sharpe') or {}).get('median')
        if isinstance(sharpe, (int, float)):
            cell['sharpes'].append(float(sharpe))
        cell['ics'].extend(_block_ics(record))
    table = []
    for (key, seeds), cell in sorted(grouped.items()):
        table.append({
            'key': key, 'seeds': seeds, 'rows': len(cell['sharpes']) or len(cell['ics']),
            'sharpe_mean': mean(cell['sharpes']) if cell['sharpes'] else None,
            'sharpe_sd': pstdev(cell['sharpes']) if len(cell['sharpes']) > 1 else 0.0,
            'ic_mean': mean(cell['ics']) if cell['ics'] else None,
            'ic_sd': pstdev(cell['ics']) if len(cell['ics']) > 1 else 0.0,
        })
    return table


FOLD_SHAPE_MIN_ICIR = 0.5
FOLD_SHAPE_MIN_POSITIVE_SHARE = 2 / 3
FOLD_SHAPE_TIE_MARGIN = 0.01


def fold_shape_verdict(rows, *, min_icir: float = FOLD_SHAPE_MIN_ICIR,
                       min_positive_share: float = FOLD_SHAPE_MIN_POSITIVE_SHARE,
                       tie_margin: float = FOLD_SHAPE_TIE_MARGIN) -> dict:
    """#70: adjudicate fold shape on per-fold ICIR and the positive-IC fold share.

    The pre-registration deliberately excludes portfolio Sharpe: fold shape is a property of
    the measurement instrument, not of the strategy. Rows are pooled per (arm, label, feature
    set, schema, panel start, fold plan); a plan whose folds all carry the same IC has no
    spread, so its ICIR is ``None`` and it fails the main criterion rather than passing by
    division by zero.
    """
    grouped: dict[tuple, list[float]] = {}
    for record in rows:
        ics = _block_ics(record)
        if len(ics) < 2:
            continue
        context = record.get('context') or {}
        arm = f"{context.get('model_type', '?')}{'+hybrid' if context.get('hybrid') else ''}"
        key = (
            arm, str(context.get('label') or '-'), str(context.get('feature_set') or 'all'),
            str(context.get('feature_schema_version') or '-'),
            f"{context.get('panel_start')}..{context.get('panel_end')}",
            str(context.get('fold_plan') or '?'),
        )
        grouped.setdefault(key, []).extend(ics)

    cells = []
    for key, ics in sorted(grouped.items()):
        ic_mean = mean(ics)
        spread = pstdev(ics)
        icir = (ic_mean / spread) if spread else None
        positive_share = sum(1 for value in ics if value > 0) / len(ics)
        failed = []
        if icir is None or icir < min_icir:
            failed.append('icir')
        if positive_share < min_positive_share:
            failed.append('positive_ic_fold_share')
        cells.append({
            'arm': key[0], 'label': key[1], 'feature_set': key[2], 'schema': key[3],
            'panel': key[4], 'fold_plan': key[5], 'blocks': len(ics),
            'ic_mean': ic_mean, 'icir': icir, 'positive_share': positive_share,
            'passed': not failed, 'failed': failed,
        })

    comparisons = []
    families: dict[tuple, dict] = {}
    for cell in cells:
        family = (
            cell['arm'], cell['label'], cell['feature_set'], cell['schema'], cell['panel'],
        )
        families.setdefault(family, {})[cell['fold_plan']] = cell
    for family, plans in sorted(families.items()):
        if len(plans) < 2:
            continue
        first, second = sorted(plans.values(), key=lambda item: item['fold_plan'])[:2]
        if first['icir'] is None or second['icir'] is None:
            winner = 'undecidable'
        elif abs(first['icir'] - second['icir']) < tie_margin:
            if first['positive_share'] == second['positive_share']:
                winner = 'tie'
            else:
                winner = (
                    first['fold_plan']
                    if first['positive_share'] > second['positive_share']
                    else second['fold_plan']
                )
        else:
            winner = first['fold_plan'] if first['icir'] > second['icir'] else second['fold_plan']
        comparisons.append({
            'arm': family[0], 'label': family[1], 'feature_set': family[2],
            'schema': family[3], 'panel': family[4], 'plans': sorted(plans),
            'winner': winner,
        })
    return {'cells': cells, 'comparisons': comparisons}
