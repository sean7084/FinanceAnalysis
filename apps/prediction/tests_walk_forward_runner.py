"""H2 / D7-D11: walk-forward cell orchestration with injected collaborators.

The runner is exercised without the DB matrix build: a fake provider supplies fold
frames, fit/predict/simulate/holdout are injected, and the ledger is the real
append-only JSONL redirected to a tmp path. Pins: one ledger row per horizon carrying
the prereg digest, fold artifacts persisted per fold x horizon, undeclared cells
refused, checkpoint resume skips done folds, empty plan raises.
"""
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from django.test import TestCase, override_settings

from apps.core.date_contract import STOCK_TRAINING_START
from apps.prediction import evidence
from apps.prediction.prereg import PreregistrationError
from apps.prediction.walk_forward_runner import WalkForwardCell, run_cell

FEATURES = ('mom_5d', 'vol_20d')


def _sessions():
    return [ts.date() for ts in pd.bdate_range(STOCK_TRAINING_START, periods=400)]


class _FakeProvider:
    def __init__(self, sessions):
        self.sessions = sessions
        self.fit_calls = 0

    def aux_frame(self):
        idx = pd.MultiIndex.from_tuples(
            [(self.sessions[300], 1), (self.sessions[300], 2)], names=['date', 'asset_id'])
        return pd.DataFrame({'st_flag_asof': [False, False], 'circ_mv_asof': [100.0, 200.0]}, index=idx)

    def fold_frames(self, fold, horizon, mask):
        self.fit_calls += 1
        train = self._frame([fold.train_start, fold.train_end])
        valid = self._frame([fold.valid_start, fold.valid_end])
        return train, valid

    def _frame(self, dates):
        rows = []
        for d in dates:
            for aid in (1, 2):
                rows.append({'date': d, 'asset_id': aid, 'mom_5d': 0.1, 'vol_20d': 0.2,
                             'label': 'UP', 'fwd_return': 0.03})
        return pd.DataFrame(rows).set_index(['date', 'asset_id'])

    def prices(self):
        return pd.DataFrame(columns=['date', 'asset_id', 'open', 'close', 'raw_close'])


def _fit_fn(train, horizon, feature_names):
    return {'model': object(), 'horizon': horizon}


def _predict_fn(fit, valid):
    n = len(valid)
    return np.tile([0.2, 0.2, 0.6], (n, 1))


def _holdout_fn(labels, proba, dates, fwd):
    return {'rank_ic': 0.05, 'icir': 0.3, 'rank_ic_tstat': 2.0, 'accuracy': 0.6}


def _simulate_fn(predictions, prices, **kwargs):
    return pd.DataFrame({'date': list(predictions['date']),
                         'portfolio_return': [0.001] * len(predictions),
                         'n_selected': [2] * len(predictions)})


def _cell(sessions, horizons=(3, 7)):
    return WalkForwardCell(
        arm='board_only', panel_start=sessions[300], panel_end=sessions[-1],
        horizons=horizons, index_codes=('000905.SH',), feature_names=FEATURES,
        execution_model='next_day_open',
    )


def _manifest(sessions, horizons=(3, 7)):
    return {'arms': ['board_only'], 'panel_starts': [str(sessions[300])],
            'panel_end': str(sessions[-1]), 'horizons': list(horizons),
            'fold_plan': 'expanding-7', 'schema_version': 'v3'}


class WalkForwardRunnerTests(TestCase):
    def setUp(self):
        self.sessions = _sessions()
        self.provider = _FakeProvider(self.sessions)
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = str(Path(self.tmp.name) / 'stock.jsonl')
        self.addCleanup(self.tmp.cleanup)

    def _run(self, cell=None, manifest=None, checkpoint=None, persist=None, horizons=(3, 7)):
        with override_settings(STOCK_EVIDENCE_PATH=self.ledger):
            return run_cell(
                cell or _cell(self.sessions, horizons),
                trade_dates=self.sessions, provider=self.provider,
                fit_fn=_fit_fn, predict_fn=_predict_fn, simulate_fn=_simulate_fn,
                record_fn=evidence.record_stock_measurement, holdout_metrics_fn=_holdout_fn,
                persist_fn=persist, preregistration='abc123def456',
                manifest=manifest, checkpoint=checkpoint,
            )

    def test_one_ledger_row_per_horizon_with_digest(self):
        self._run(manifest=_manifest(self.sessions))
        with override_settings(STOCK_EVIDENCE_PATH=self.ledger):
            rows = evidence.read_stock_measurements()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r['context']['horizon'] for r in rows}, {3, 7})
        for row in rows:
            self.assertEqual(row['preregistration'], 'abc123def456')
            self.assertEqual(row['context']['arm'], 'board_only')
            self.assertEqual(row['results']['n_folds'], 7)

    def test_fold_artifacts_persisted_per_fold_horizon(self):
        tags = []
        self._run(manifest=_manifest(self.sessions),
                  persist=lambda fit, h, tag: tags.append((h, tag)))
        self.assertEqual(len(tags), 14)  # 7 folds x 2 horizons
        self.assertTrue(all(tag.startswith('wf-board_only-') for _, tag in tags))

    def test_undeclared_arm_refused(self):
        manifest = _manifest(self.sessions)
        manifest['arms'] = ['board_st']
        with self.assertRaises(PreregistrationError):
            self._run(manifest=manifest)

    def test_undeclared_panel_start_refused(self):
        manifest = _manifest(self.sessions)
        manifest['panel_starts'] = ['2019-01-02']
        with self.assertRaises(PreregistrationError):
            self._run(manifest=manifest)

    def test_undeclared_horizon_refused(self):
        with self.assertRaises(PreregistrationError):
            self._run(manifest=_manifest(self.sessions, horizons=(3,)), horizons=(3, 7))

    def test_checkpoint_resume_skips_done_folds(self):
        checkpoint = {(3, i) for i in range(7)}  # horizon 3 fully done
        self._run(manifest=_manifest(self.sessions), checkpoint=checkpoint)
        # only horizon 7 fits run (7), horizon 3 skipped.
        self.assertEqual(self.provider.fit_calls, 7)

    def test_summary_aggregates_mean_ic(self):
        summary = self._run(manifest=_manifest(self.sessions))
        self.assertAlmostEqual(summary['horizons'][3]['results']['mean_rank_ic'], 0.05)
        self.assertAlmostEqual(summary['horizons'][3]['results']['mean_icir'], 0.3)

    def test_empty_fold_plan_raises(self):
        cell = WalkForwardCell(
            arm='board_only', panel_start=self.sessions[10], panel_end=self.sessions[-1],
            horizons=(3,), index_codes=('000905.SH',), feature_names=FEATURES,
            execution_model='next_day_open',
        )
        with self.assertRaises(ValueError):
            self._run(cell=cell, horizons=(3,))