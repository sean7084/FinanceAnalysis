"""DB-backed matrix/label/price provider for the stock walk-forward harness (H2 / D7).

Builds the board-level feature matrix ONCE per (index, panel_start) over the frozen
panel at the serving default policy (ex-board), carries the parallel aux frame
(st_flag_asof / circ_mv_asof), and joins per-horizon labels. Arms are row masks over
this single build (never separate matrix builds). Fold frames are sliced by the
purged training dates and the validation block, then masked to the arm.

The matrix/label builders are the production training functions, so the harness and
the serving trainer cannot drift. The actual campaign run is gated on the data runs
(DR1-7) and the signed preregistration (P1); this provider is exercised then.
"""
import pandas as pd

from apps.markets.models import OHLCV
from apps.prediction import tasks_lightgbm as tl
from apps.prediction.universe_arms import build_aux_frame
from apps.prediction.walk_forward import train_dates_for_fold


class DBMatrixProvider:
    def __init__(self, *, panel_start, panel_end, index_codes, horizons,
                 universe_policy=None, missing_value_strategy=None):
        self.panel_start = panel_start
        self.panel_end = panel_end
        self.index_codes = tuple(index_codes)
        self.horizons = tuple(horizons)
        self._matrix = tl._create_feature_matrix(
            panel_start, panel_end,
            missing_value_strategy=missing_value_strategy or tl.MISSING_VALUE_STRATEGY_NATIVE_NAN,
            universe_policy=universe_policy, pit_index_codes=list(self.index_codes),
        ).reset_index()
        self._aux = build_aux_frame(
            pd.MultiIndex.from_frame(self._matrix[['date', 'asset_id']])
        )
        self._labels = {}
        for horizon in self.horizons:
            records = tl._label_records_for_training(
                panel_start, panel_end, horizon,
                universe_policy=universe_policy, pit_index_codes=list(self.index_codes),
            )
            self._labels[horizon] = pd.DataFrame([
                {'date': key[0], 'asset_id': key[1], 'label': rec[0], 'fwd_return': rec[1]}
                for key, rec in records.items()
            ])
        self._prices = None

    def aux_frame(self):
        return self._aux

    def _aligned(self, horizon):
        labels = self._labels[horizon]
        if labels.empty:
            frame = self._matrix.copy()
            frame['label'] = None
            frame['fwd_return'] = None
        else:
            frame = self._matrix.merge(labels, on=['date', 'asset_id'], how='left')
        return frame.dropna(subset=['label', 'fwd_return'])

    def fold_frames(self, fold, horizon, mask):
        frame = self._aligned(horizon)
        kept, _ = train_dates_for_fold(fold, sorted(self._matrix['date'].unique()), horizon)
        kept_set = set(kept)
        mask_keys = set(mask[mask].index)
        train = frame[frame['date'].isin(kept_set)
                      & frame.set_index(['date', 'asset_id']).index.isin(mask_keys)]
        valid = frame[(frame['date'] >= fold.valid_start) & (frame['date'] <= fold.valid_end)
                      & frame.set_index(['date', 'asset_id']).index.isin(mask_keys)]
        return (train.set_index(['date', 'asset_id']), valid.set_index(['date', 'asset_id']))

    def prices(self):
        if self._prices is None:
            asset_ids = sorted(self._matrix['asset_id'].unique())
            rows = OHLCV.objects.filter(
                asset_id__in=asset_ids, date__gte=self.panel_start, date__lte=self.panel_end,
            ).values('asset_id', 'date', 'open', 'close', 'raw_close')
            self._prices = pd.DataFrame(list(rows), columns=['date', 'asset_id', 'open', 'close', 'raw_close'])
        return self._prices