"""Universe arm row-masks over the board-level feature matrix (H2 / plan D7).

The harness builds ONE board-level matrix per (index, panel_start) at the serving
default policy (ex-board). Arms are row masks over it, NOT separate matrix builds:

  board_only : every row (the ex-board universe as built)
  board_st   : drop rows whose asset is ST as-of the row date
  cap        : drop the bottom ``cap_fraction`` of circulating market value per date

Auxiliary columns (st_flag_asof, circ_mv_asof) live in a PARALLEL frame keyed by the
matrix index; they are NEVER feature columns (ST must never enter the model). The
mask is pure over the aux frame so it can be equivalence-tested against a policy
rebuild (apply_universe_policy) without touching the production feature matrix.
"""
from bisect import bisect_right
from decimal import Decimal

import pandas as pd

from apps.factors.models import FundamentalFactorSnapshot
from apps.markets.st_history import st_asset_ids_by_dates

ARM_BOARD_ONLY = 'board_only'
ARM_BOARD_ST = 'board_st'
ARM_CAP = 'cap'
KNOWN_ARMS = (ARM_BOARD_ONLY, ARM_BOARD_ST, ARM_CAP)
DEFAULT_CAP_FRACTION = Decimal('0.30')
AUX_COLUMNS = ('st_flag_asof', 'circ_mv_asof')


def circ_mv_asof_for_index(matrix_index):
    """Latest FundamentalFactorSnapshot.circ_mv <= row date, per (date, asset_id).

    Loads snapshots once up to the max date and bisects per asset (mirrors the
    membership / ST as-of mechanism). A missing snapshot is NaN (fail-closed under
    the cap arm, which cannot rank an unknown market value).
    """
    pairs = list(matrix_index)
    if not pairs:
        return {}
    asset_ids = sorted({asset_id for _, asset_id in pairs})
    max_date = max(date for date, _ in pairs)
    rows = list(
        FundamentalFactorSnapshot.objects.filter(asset_id__in=asset_ids, date__lte=max_date)
        .values_list('asset_id', 'date', 'circ_mv')
        .order_by('asset_id', 'date')
    )
    by_asset = {}
    for asset_id, snap_date, circ_mv in rows:
        by_asset.setdefault(asset_id, []).append((snap_date, circ_mv))
    result = {}
    for date, asset_id in pairs:
        snapshots = by_asset.get(asset_id)
        if not snapshots:
            result[(date, asset_id)] = None
            continue
        starts = [snap_date for snap_date, _ in snapshots]
        idx = bisect_right(starts, date) - 1
        result[(date, asset_id)] = snapshots[idx][1] if idx >= 0 else None
    return result


def build_aux_frame(matrix_index):
    """st_flag_asof + circ_mv_asof aligned to a (date, asset_id) matrix index.

    The returned frame carries ONLY the two auxiliary columns — never features.
    """
    pairs = list(matrix_index)
    dates = sorted({date for date, _ in pairs})
    st_by_date = st_asset_ids_by_dates(dates)
    circ_mv = circ_mv_asof_for_index(pairs)
    records = []
    for date, asset_id in pairs:
        records.append({
            'date': date,
            'asset_id': asset_id,
            'st_flag_asof': asset_id in st_by_date.get(date, set()),
            'circ_mv_asof': circ_mv.get((date, asset_id)),
        })
    frame = pd.DataFrame(records, columns=['date', 'asset_id', *AUX_COLUMNS])
    return frame.set_index(['date', 'asset_id'])


def arm_row_mask(aux_frame, *, arm, cap_fraction=DEFAULT_CAP_FRACTION):
    """Boolean row mask selecting the arm's universe over the board-level matrix.

    board_only keeps every row; board_st drops as-of ST rows; cap drops the bottom
    ``cap_fraction`` of circ_mv_asof per date (NaN market value is excluded —
    fail-closed, an unrankable asset cannot be admitted to the cap universe).
    """
    if arm not in KNOWN_ARMS:
        raise ValueError(f'unknown universe arm {arm!r}; expected one of {KNOWN_ARMS}')
    if arm == ARM_BOARD_ONLY:
        return pd.Series(True, index=aux_frame.index)
    if arm == ARM_BOARD_ST:
        return ~aux_frame['st_flag_asof'].astype(bool)
    fraction = float(cap_fraction)
    if not 0.0 <= fraction < 1.0:
        raise ValueError(f'cap_fraction must be in [0, 1), got {cap_fraction}')
    keep = pd.Series(False, index=aux_frame.index)
    circ = aux_frame['circ_mv_asof']
    for date, group in aux_frame.groupby(level='date'):
        values = circ.loc[group.index]
        ranked = values.dropna().sort_values(ascending=False)
        if ranked.empty:
            continue
        n_drop = int(len(ranked) * fraction)
        kept_ids = ranked.index[:len(ranked) - n_drop] if n_drop else ranked.index
        keep.loc[kept_ids] = True
    return keep