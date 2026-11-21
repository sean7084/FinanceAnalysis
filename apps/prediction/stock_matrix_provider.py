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

from apps.core.date_contract import STOCK_TRAINING_START
from apps.markets.models import OHLCV
from apps.prediction import tasks_lightgbm as tl
from apps.prediction.universe_arms import build_aux_frame
from apps.prediction.walk_forward import train_dates_for_fold


class DBMatrixProvider:
    def __init__(self, *, panel_start, panel_end, index_codes, horizons,
                 universe_policy=None, missing_value_strategy=None,
                 executable_benchmark_code=None):
        self.panel_start = panel_start
        self.panel_end = panel_end
        self.index_codes = tuple(index_codes)
        self.horizons = tuple(horizons)
        self.executable_benchmark_code = executable_benchmark_code
        # The matrix + labels must span the FULL training+validation panel
        # [STOCK_TRAINING_START, panel_end]: fold 0 trains on [STOCK_TRAINING_START,
        # panel_start), so building only [panel_start, panel_end] leaves the training
        # dates empty. panel_start only determines the fold plan (validation start).
        self._matrix = tl._create_feature_matrix(
            STOCK_TRAINING_START, panel_end,
            missing_value_strategy=missing_value_strategy or tl.MISSING_VALUE_STRATEGY_NATIVE_NAN,
            universe_policy=universe_policy, pit_index_codes=list(self.index_codes),
        ).reset_index()
        self._aux = build_aux_frame(
            pd.MultiIndex.from_frame(self._matrix[['date', 'asset_id']])
        )
        self._labels = {}
        for horizon in self.horizons:
            records = tl._label_records_for_training(
                STOCK_TRAINING_START, panel_end, horizon,
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
                asset_id__in=asset_ids, date__gte=STOCK_TRAINING_START, date__lte=self.panel_end,
            ).values('asset_id', 'date', 'open', 'close', 'raw_close')
            self._prices = pd.DataFrame(list(rows), columns=['date', 'asset_id', 'open', 'close', 'raw_close'])
        return self._prices

    def benchmark_returns(self):
        """Date-indexed daily returns for the verdict alpha/beta (H2b / O5).

        official = the index close-to-close return (first index code); executable =
        the universe-derived PIT benchmark daily_return (ex-board, ex-ST per the arm).
        Absent series are omitted (the runner reports None, never a fabricated zero).
        The size factor is not supplied here; beta_size stays None until a size series
        is wired (O5 circ_mv spread).
        """
        from apps.markets.models import BenchmarkIndexDaily, PointInTimeBenchmarkDaily

        result = {}
        official_code = self.index_codes[0]
        official_rows = (
            BenchmarkIndexDaily.objects.filter(
                index_code=official_code, trade_date__gte=self.panel_start, trade_date__lte=self.panel_end,
            ).order_by('trade_date').values('trade_date', 'close')
        )
        closes = pd.Series({r['trade_date']: float(r['close']) for r in official_rows})
        if len(closes) > 1:
            result['official'] = closes.pct_change().dropna()
        if self.executable_benchmark_code:
            pit_rows = (
                PointInTimeBenchmarkDaily.objects.filter(
                    benchmark_code=self.executable_benchmark_code,
                    trade_date__gte=self.panel_start, trade_date__lte=self.panel_end,
                ).order_by('trade_date').values('trade_date', 'daily_return')
            )
            executable = pd.Series({r['trade_date']: float(r['daily_return']) for r in pit_rows})
            if len(executable):
                result['executable'] = executable
        return result