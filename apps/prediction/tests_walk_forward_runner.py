"""H2 / D7-D11: walk-forward cell orchestration with injected collaborators.

The runner is exercised without the DB matrix build: a fake provider supplies fold
frames, fit/predict/simulate/holdout are injected, and the ledger is the real
append-only JSONL redirected to a tmp path. Pins: one ledger row per horizon carrying
the prereg digest, fold artifacts persisted per fold x horizon, undeclared cells
refused, checkpoint resume skips done folds, empty plan raises.
"""
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from django.test import TestCase, override_settings

from apps.core.date_contract import STOCK_TRAINING_START
from apps.prediction import evidence
from apps.prediction.prereg import PreregistrationError
from apps.prediction.walk_forward_runner import WalkForwardCell, run_cell

FEATURES = ('mom_5d', 'vol_20d')


def _sim_panel():
    dates = [date(2020, 1, d) for d in (1, 2, 3, 6)]
    prices = pd.DataFrame([
        {'date': d, 'asset_id': 1, 'open': Decimal(10) + Decimal(i) * Decimal('0.5'),
         'close': Decimal(10) + Decimal(i) * Decimal('0.5'), 'raw_close': Decimal(10) + Decimal(i) * Decimal('0.5')}
        for i, d in enumerate(dates)
    ] + [
        {'date': d, 'asset_id': 2, 'open': Decimal(20), 'close': Decimal(20), 'raw_close': Decimal(20)}
        for d in dates
    ])
    predictions = pd.DataFrame([
        {'date': dates[0], 'asset_id': 1, 'up_prob': 0.9},
        {'date': dates[0], 'asset_id': 2, 'up_prob': 0.6},
    ])
    return predictions, prices, dates


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


class H2bCompletionTests(TestCase):
    def setUp(self):
        self.sessions = _sessions()
        self.provider = _FakeProvider(self.sessions)
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = str(Path(self.tmp.name) / 'stock.jsonl')
        self.addCleanup(self.tmp.cleanup)

    def test_simulator_params_per_horizon_resolution(self):
        from apps.prediction.walk_forward_runner import _simulator_params_for
        params = {'common': {'slippage_bps': 5}, '3': {'top_n': 5}, '30': {'top_n': 3}}
        self.assertEqual(_simulator_params_for(params, 3), {'slippage_bps': 5, 'top_n': 5})
        self.assertEqual(_simulator_params_for(params, 30), {'slippage_bps': 5, 'top_n': 3})
        self.assertEqual(_simulator_params_for(params, 7), {'slippage_bps': 5})

    def test_simulator_params_flat_backward_compatible(self):
        from apps.prediction.walk_forward_runner import _simulator_params_for
        self.assertEqual(_simulator_params_for({'top_n': 9}, 3), {'top_n': 9})
        self.assertEqual(_simulator_params_for({'top_n': 9}, 30), {'top_n': 9})
        self.assertEqual(_simulator_params_for({}, 3), {})

    def test_runner_passes_per_horizon_top_n(self):
        seen = []

        def capturing_sim(predictions, prices, **kwargs):
            seen.append(kwargs.get('top_n'))
            return pd.DataFrame({'date': list(predictions['date']),
                                 'portfolio_return': [0.001] * len(predictions),
                                 'n_selected': [2] * len(predictions)})

        cell = WalkForwardCell(
            arm='board_only', panel_start=self.sessions[300], panel_end=self.sessions[-1],
            horizons=(3, 30), index_codes=('000905.SH',), feature_names=FEATURES,
            execution_model='next_day_open',
            simulator_params={'3': {'top_n': 5}, '30': {'top_n': 3}},
        )
        with override_settings(STOCK_EVIDENCE_PATH=self.ledger):
            run_cell(cell, trade_dates=self.sessions, provider=self.provider,
                     fit_fn=_fit_fn, predict_fn=_predict_fn, simulate_fn=capturing_sim,
                     record_fn=evidence.record_stock_measurement, holdout_metrics_fn=_holdout_fn,
                     manifest=_manifest(self.sessions, horizons=(3, 30)))
        self.assertEqual(set(seen), {5, 3})

    def test_is_sharpe_recorded_per_fold(self):
        with override_settings(STOCK_EVIDENCE_PATH=self.ledger):
            summary = run_cell(
                _cell(self.sessions), trade_dates=self.sessions, provider=self.provider,
                fit_fn=_fit_fn, predict_fn=_predict_fn, simulate_fn=_simulate_fn,
                record_fn=evidence.record_stock_measurement, holdout_metrics_fn=_holdout_fn,
                manifest=_manifest(self.sessions))
        results = summary['horizons'][3]['results']
        self.assertIn('mean_is_sharpe', results)
        self.assertTrue(all('is_sharpe' in f for f in results['per_fold']))

    def test_undeclared_index_codes_refused(self):
        manifest = _manifest(self.sessions)
        manifest['index_codes'] = ['000905.SH']
        cell = WalkForwardCell(
            arm='board_only', panel_start=self.sessions[300], panel_end=self.sessions[-1],
            horizons=(3,), index_codes=('000852.SH',), feature_names=FEATURES,
            execution_model='next_day_open',
        )
        with override_settings(STOCK_EVIDENCE_PATH=self.ledger):
            with self.assertRaises(PreregistrationError):
                run_cell(cell, trade_dates=self.sessions, provider=self.provider,
                         fit_fn=_fit_fn, predict_fn=_predict_fn, simulate_fn=_simulate_fn,
                         record_fn=evidence.record_stock_measurement, holdout_metrics_fn=_holdout_fn,
                         manifest=manifest)

    def test_transfer_fee_in_cost_model(self):
        from apps.prediction.simulator import simulate_topn_returns
        predictions, prices, dates = _sim_panel()
        kwargs = dict(top_n=1, up_threshold=0.55, ts_code_map={1: '600001.SH', 2: '600002.SH'})
        with_fee = simulate_topn_returns(predictions, prices, **kwargs)
        without_fee = simulate_topn_returns(predictions, prices, transfer_fee_rate=Decimal('0'), **kwargs)
        r1 = with_fee[with_fee['date'] == dates[0]].iloc[0]['portfolio_return']
        r0 = without_fee[without_fee['date'] == dates[0]].iloc[0]['portfolio_return']
        self.assertAlmostEqual(r0 - r1, 0.00001, places=7)

    def test_float_cost_params_accepted(self):
        # Regression: the manifest passes JSON floats; the simulator must coerce to Decimal.
        from apps.prediction.simulator import simulate_topn_returns
        predictions, prices, dates = _sim_panel()
        common = dict(top_n=1, up_threshold=0.55, ts_code_map={1: '600001.SH', 2: '600002.SH'})
        float_out = simulate_topn_returns(predictions, prices, commission_rate=0.00025,
                                          stamp_duty_rate=0.001, transfer_fee_rate=1e-05, **common)
        dec_out = simulate_topn_returns(predictions, prices, **common)
        self.assertAlmostEqual(float_out.iloc[0]['portfolio_return'],
                               dec_out.iloc[0]['portfolio_return'], places=12)

    def test_fit_receives_date_and_label_as_columns(self):
        # Regression: the fit core's purged split reads frame['date'], and it indexes
        # train_frame[selected_feature_names] expecting a LIST (a tuple is one pandas key).
        # The provider returns an indexed frame, so run_cell must reset_index + pass a list.
        seen = {}

        def checking_fit(train, horizon, features):
            seen['columns'] = set(train.columns)
            seen['features'] = features
            seen['indexable'] = train[features].shape[1]  # would raise if features is a tuple
            return {'model': object()}

        with override_settings(STOCK_EVIDENCE_PATH=self.ledger):
            run_cell(_cell(self.sessions, horizons=(3,)), trade_dates=self.sessions,
                     provider=self.provider, fit_fn=checking_fit, predict_fn=_predict_fn,
                     simulate_fn=_simulate_fn, record_fn=evidence.record_stock_measurement,
                     holdout_metrics_fn=_holdout_fn, manifest=_manifest(self.sessions, horizons=(3,)))
        self.assertIn('date', seen['columns'])
        self.assertIn('label', seen['columns'])
        self.assertIn('fwd_return', seen['columns'])
        self.assertIsInstance(seen['features'], list)
        self.assertEqual(seen['indexable'], len(FEATURES))

    def test_alpha_beta_ols_recovers_slope(self):
        from apps.prediction.walk_forward_runner import _alpha_beta
        dates = pd.date_range('2021-01-01', periods=20)
        bench = pd.Series(np.linspace(-0.02, 0.02, 20), index=dates)
        strat = 2.0 * bench + 0.001
        alpha, beta = _alpha_beta(strat, bench)
        self.assertAlmostEqual(beta, 2.0, places=6)
        self.assertAlmostEqual(alpha, 0.001 * 252, places=6)

    def test_alpha_beta_zero_variance_benchmark_none(self):
        from apps.prediction.walk_forward_runner import _alpha_beta
        dates = pd.date_range('2021-01-01', periods=5)
        bench = pd.Series([0.01] * 5, index=dates)
        strat = pd.Series([0.02] * 5, index=dates)
        self.assertEqual(_alpha_beta(strat, bench), (None, None))

    def test_verdict_metrics_folds_beating_and_alpha(self):
        from apps.prediction.walk_forward_runner import _verdict_metrics
        dates = pd.date_range('2021-01-01', periods=10)
        fold1 = pd.Series([0.02] * 5, index=dates[:5])
        fold2 = pd.Series([-0.01] * 5, index=dates[5:])
        exe = pd.Series([0.001] * 10, index=dates)
        official = pd.Series([0.0005] * 10, index=dates)
        out = _verdict_metrics([fold1, fold2], {i: 0.01 + 0.001 * i for i in range(60)}, {'official': official, 'executable': exe})
        self.assertEqual(out['folds_beating'], 1)
        self.assertIsNotNone(out['alpha_executable'])
        self.assertIsNotNone(out['alpha_official'])
        self.assertIsNotNone(out['nw_t'])

    def test_verdict_metrics_absent_benchmark_is_none(self):
        from apps.prediction.walk_forward_runner import _verdict_metrics
        out = _verdict_metrics([pd.Series([0.01], index=[date(2021, 1, 4)])], {}, {})
        self.assertIsNone(out['alpha_executable'])
        self.assertIsNone(out['nw_t'])
        self.assertEqual(out['folds_beating'], 0)

    def test_returns_sink_called_per_fold(self):
        captured = []
        with override_settings(STOCK_EVIDENCE_PATH=self.ledger):
            run_cell(_cell(self.sessions), trade_dates=self.sessions, provider=self.provider,
                     fit_fn=_fit_fn, predict_fn=_predict_fn, simulate_fn=_simulate_fn,
                     record_fn=evidence.record_stock_measurement, holdout_metrics_fn=_holdout_fn,
                     returns_sink=lambda h, fi, sim: captured.append((h, fi)),
                     manifest=_manifest(self.sessions))
        self.assertEqual(len(captured), 14)  # 7 folds x 2 horizons

    def test_nw_t_from_per_date_ic(self):
        def holdout_with_ic(labels, proba, dates, fwd):
            return {'rank_ic': 0.05, 'icir': 0.3, 'rank_ic_tstat': 2.0,
                    'per_date_ic': {i: 0.01 + 0.001 * i for i in range(50)}}
        with override_settings(STOCK_EVIDENCE_PATH=self.ledger):
            summary = run_cell(_cell(self.sessions), trade_dates=self.sessions, provider=self.provider,
                               fit_fn=_fit_fn, predict_fn=_predict_fn, simulate_fn=_simulate_fn,
                               record_fn=evidence.record_stock_measurement, holdout_metrics_fn=holdout_with_ic,
                               manifest=_manifest(self.sessions))
        self.assertIsNotNone(summary['horizons'][3]['results']['nw_t'])


class DBMatrixProviderWindowTests(TestCase):
    def test_matrix_and_labels_span_training_start_not_panel_start(self):
        # Regression: fold 0 trains on [STOCK_TRAINING_START, panel_start); building the
        # matrix/labels only from panel_start leaves the training dates empty (0 samples).
        from apps.prediction.stock_matrix_provider import DBMatrixProvider
        captured = {}

        def fake_matrix(start, end, **kwargs):
            captured['matrix_start'] = start
            captured['matrix_st_layer'] = kwargs.get('st_layer')
            df = pd.DataFrame({'date': [STOCK_TRAINING_START], 'asset_id': [1],
                               'mom_5d': [0.1], 'vol_20d': [0.2]})
            df.attrs['feature_names'] = ['mom_5d', 'vol_20d']
            return df.set_index(['date', 'asset_id'])

        def fake_labels(start, end, horizon, **kwargs):
            captured['label_start'] = start
            captured['label_st_layer'] = kwargs.get('st_layer')
            return {}

        with patch('apps.prediction.stock_matrix_provider.tl._create_feature_matrix', side_effect=fake_matrix), \
                patch('apps.prediction.stock_matrix_provider.tl._label_records_for_training', side_effect=fake_labels), \
                patch('apps.prediction.stock_matrix_provider.build_aux_frame',
                      return_value=pd.DataFrame({'st_flag_asof': [False], 'circ_mv_asof': [1.0]},
                                                index=pd.MultiIndex.from_tuples([(STOCK_TRAINING_START, 1)],
                                                                               names=['date', 'asset_id']))):
            DBMatrixProvider(panel_start=date(2018, 1, 2), panel_end=date(2020, 1, 1),
                             index_codes=('000905.SH',), horizons=(30,))
        self.assertEqual(captured['matrix_start'], STOCK_TRAINING_START)
        self.assertEqual(captured['label_start'], STOCK_TRAINING_START)
        # Board-level build: ST included so board_only/board_st masks are meaningful
        # (after B3 the serving default excludes ST, which would break board_only).
        self.assertIs(captured['matrix_st_layer'], False)
        self.assertIs(captured['label_st_layer'], False)