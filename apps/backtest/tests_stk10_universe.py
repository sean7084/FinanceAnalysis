"""STK-10 enablement: per-run ``st_layer`` reaches the engine, and a cached signal
surface can never cross universes.

Both guards are load-bearing for the STK-10 matrix.

Without the first, the ``default``-policy / ST-off arms are unreachable: the engine
used to read only ``universe_policy`` and ``pit_index_codes`` from ``run.parameters``,
so ``st_layer`` was an inert key that the serializer accepted unvalidated while
``resolve_st_layer`` fell through to the global ``ST_LAYER_ENABLED`` switch.

Without the second, one process serving cells under different universes hands every
cell the FIRST cell's candidate pool -- identical equity curves and no error, because
the cached mapping is built from the policy-filtered eligible asset list.

``execution_model`` is deliberately NOT part of the surface key: it changes when a
fill happens, not what was predicted. That is what lets the two execution models of
one arm share a single surface, which is half the wall-clock saving the matrix plan
depends on.
"""
import os
from collections import OrderedDict
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from apps.backtest.models import BacktestRun
from apps.backtest.serializers import BacktestRunSerializer
from apps.backtest.tasks import (
    _eligible_backtest_asset_ids,
    _matrix_signal_cache_key,
    _prediction_eligible_asset_ids,
    _prediction_universe,
    _prediction_universe_cache_key,
    _st_layer_override,
    _universe_cache_key,
)
from apps.prediction.models_lightgbm import LightGBMModelArtifact

D = date(2020, 1, 2)
MODEL = ('LightGBMModelArtifact', 47, 'lgb-7d-2026-11-09')


class StLayerOverrideTests(SimpleTestCase):
    def _run(self, **params):
        return BacktestRun(parameters=params)

    def test_absent_means_inherit(self):
        self.assertIsNone(_st_layer_override(self._run()))

    def test_explicit_false_is_distinguishable_from_absent(self):
        value = _st_layer_override(self._run(st_layer=False))
        self.assertIsNotNone(value)
        self.assertFalse(value)

    def test_explicit_true(self):
        self.assertTrue(_st_layer_override(self._run(st_layer=True)))


class StLayerThreadingTests(SimpleTestCase):
    """The override is passed to the effective-universe contract, not dropped."""

    def _call(self, **cache_overrides):
        cache = {'universe_policy': 'default', 'pit_index_codes': None, 'st_layer': None}
        cache.update(cache_overrides)
        with patch(
            'apps.backtest.tasks.effective_universe_tradeable_asset_ids',
            return_value=[1],
        ) as mocked:
            _eligible_backtest_asset_ids(D, cache)
        return mocked.call_args.kwargs

    def test_explicit_false_reaches_the_contract(self):
        kwargs = self._call(st_layer=False)
        self.assertIn('st_layer', kwargs)
        self.assertFalse(kwargs['st_layer'])
        self.assertEqual(kwargs['policy'], 'default')

    def test_explicit_true_reaches_the_contract(self):
        self.assertTrue(self._call(st_layer=True)['st_layer'])

    def test_absent_passes_none_so_legacy_runs_are_unchanged(self):
        cache = {'universe_policy': 'default', 'pit_index_codes': None}
        with patch(
            'apps.backtest.tasks.effective_universe_tradeable_asset_ids',
            return_value=[1],
        ) as mocked:
            _eligible_backtest_asset_ids(D, cache)
        self.assertIsNone(mocked.call_args.kwargs['st_layer'])

    def test_run_cache_separates_st_layer(self):
        """Mutating only st_layer must not be served the previous answer."""
        cache = {'universe_policy': 'default', 'pit_index_codes': None, 'st_layer': None}
        with patch(
            'apps.backtest.tasks.effective_universe_tradeable_asset_ids',
            side_effect=['inherited', 'overridden'],
        ) as mocked:
            first = _eligible_backtest_asset_ids(D, cache)
            cache['st_layer'] = False
            second = _eligible_backtest_asset_ids(D, cache)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(first, 'inherited')
        self.assertEqual(second, 'overridden')


class UniverseCacheKeyTests(SimpleTestCase):
    def test_key_carries_policy_st_and_index(self):
        key = _universe_cache_key({
            'universe_policy': 'default',
            'st_layer': True,
            'pit_index_codes': ['000905.SH'],
        })
        self.assertEqual(key, ('default', True, ('000905.SH',)))

    def test_policy_splits_the_surface(self):
        full = _universe_cache_key({'universe_policy': 'research_full', 'st_layer': False})
        default = _universe_cache_key({'universe_policy': 'default', 'st_layer': False})
        self.assertNotEqual(full, default)

    def test_st_layer_splits_the_surface(self):
        off = _universe_cache_key({'universe_policy': 'default', 'st_layer': False})
        on = _universe_cache_key({'universe_policy': 'default', 'st_layer': True})
        self.assertNotEqual(off, on)

    def test_inherited_st_splits_from_explicit_st(self):
        """None (inherit the global) and False (override off) are different universes."""
        self.assertNotEqual(
            _universe_cache_key({'universe_policy': 'default', 'st_layer': None}),
            _universe_cache_key({'universe_policy': 'default', 'st_layer': False}),
        )


class MatrixSignalCacheKeyTests(SimpleTestCase):
    UNIVERSE = _universe_cache_key({
        'universe_policy': 'default', 'st_layer': False, 'pit_index_codes': ['000905.SH'],
    })

    def _run(self, **params):
        base = {'matrix_signal_cache_key': 'stk10-scope'}
        base.update(params)
        return BacktestRun(parameters=base)

    def _key(self, run, universe_key=None):
        return _matrix_signal_cache_key(
            run, 'lightgbm', D, 7, MODEL, (),
            self.UNIVERSE if universe_key is None else universe_key,
        )

    def test_execution_models_of_one_arm_share_a_surface(self):
        self.assertEqual(
            self._key(self._run(execution_model='next_day_open')),
            self._key(self._run(execution_model='same_day_close')),
        )

    def test_different_universes_do_not_share_a_surface(self):
        other = _universe_cache_key({
            'universe_policy': 'research_full', 'st_layer': False, 'pit_index_codes': ['000905.SH'],
        })
        self.assertNotEqual(self._key(self._run(), self.UNIVERSE), self._key(self._run(), other))

    def test_arm_and_superset_do_not_share_a_full_matrix_key(self):
        arm_cache = {
            'prediction_universe': 'arm',
            'universe_policy': 'default',
            'st_layer': True,
            'pit_index_codes': ['000905.SH'],
        }
        superset_cache = dict(arm_cache, prediction_universe='superset')
        arm_key = _matrix_signal_cache_key(
            self._run(prediction_universe='arm'), 'lightgbm', D, 7, MODEL, (),
            _prediction_universe_cache_key(arm_cache),
        )
        superset_key = _matrix_signal_cache_key(
            self._run(prediction_universe='superset'), 'lightgbm', D, 7, MODEL, (),
            _prediction_universe_cache_key(superset_cache),
        )
        self.assertNotEqual(arm_key, superset_key)

    def test_no_scope_means_no_key(self):
        self.assertIsNone(
            _matrix_signal_cache_key(BacktestRun(parameters={}), 'lightgbm', D, 7, MODEL, (), self.UNIVERSE)
        )


class CacheMechanicsTests(SimpleTestCase):
    def test_matrix_cache_bound_reads_the_environment(self):
        from apps.backtest import tasks

        with patch.dict(os.environ, {'BACKTEST_MATRIX_SIGNAL_CACHE_MAX_ENTRIES': '8000'}):
            self.assertEqual(tasks._int_env('BACKTEST_MATRIX_SIGNAL_CACHE_MAX_ENTRIES', 2048), 8000)

    def test_invalid_matrix_cache_bound_falls_back_to_the_default(self):
        from apps.backtest import tasks

        with patch.dict(os.environ, {'BACKTEST_MATRIX_SIGNAL_CACHE_MAX_ENTRIES': 'not-an-int'}):
            self.assertEqual(tasks._int_env('BACKTEST_MATRIX_SIGNAL_CACHE_MAX_ENTRIES', 2048), 2048)

    def test_lru_hit_refreshes_recency_before_eviction(self):
        from apps.backtest import tasks

        cache = OrderedDict()
        tasks._bounded_cache_set(cache, 'a', 1, 2)
        tasks._bounded_cache_set(cache, 'b', 2, 2)
        self.assertEqual(tasks._bounded_cache_get(cache, 'a'), 1)
        tasks._bounded_cache_set(cache, 'c', 3, 2)
        self.assertNotIn('b', cache)
        self.assertEqual(list(cache), ['a', 'c'])


class SerializerStLayerTests(TestCase):
    def _payload(self, **params):
        base = {'top_n': 3, 'horizon_days': 7, 'up_threshold': 0.5, 'prediction_source': 'heuristic'}
        base.update(params)
        return {
            'name': 'stk10-serializer-test',
            'strategy_type': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            'start_date': date(2020, 1, 2),
            'end_date': date(2020, 6, 30),
            'parameters': base,
        }

    def test_boolean_accepted_and_recorded(self):
        serializer = BacktestRunSerializer(data=self._payload(st_layer=False))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertIs(serializer.validated_data['parameters']['st_layer'], False)

    def test_non_boolean_rejected(self):
        serializer = BacktestRunSerializer(data=self._payload(st_layer='yes'))
        self.assertFalse(serializer.is_valid())
        self.assertIn('parameters', serializer.errors)

    def test_absent_stays_absent(self):
        serializer = BacktestRunSerializer(data=self._payload())
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn('st_layer', serializer.validated_data['parameters'])

    def test_st_requested_while_global_disabled_fails_closed(self):
        with patch('apps.markets.universe_policy.ST_LAYER_ENABLED', False):
            serializer = BacktestRunSerializer(
                data=self._payload(universe_policy='default', st_layer=True)
            )
            self.assertFalse(serializer.is_valid())
            self.assertIn('parameters', serializer.errors)


class Stk10MatrixBuildTests(SimpleTestCase):
    def setUp(self):
        from apps.backtest.management.commands import run_stk10_matrix as command

        self.command = command
        self.artifacts = {
            horizon: LightGBMModelArtifact(
                id=horizon,
                version=command.PINNED_ARTIFACT_VERSIONS[horizon],
                horizon_days=horizon,
            )
            for horizon in (3, 7, 30)
        }
        self.cells = command.build_matrix(
            date(2018, 1, 2), date(2026, 11, 20), [3, 7, 30], 'stk10-scope', self.artifacts,
            'd8b09eb37d91', 'superset',
        )

    def test_eighteen_cells(self):
        self.assertEqual(len(self.cells), 18)

    def test_arms_exclude_the_inexpressible_research_full_st_cell(self):
        arms = {(c['parameters']['universe_policy'], c['parameters']['st_layer']) for c in self.cells}
        self.assertEqual(arms, {('research_full', False), ('default', False), ('default', True)})

    def test_every_cell_pins_the_pre_registered_fleet(self):
        for cell in self.cells:
            params = cell['parameters']
            horizon = params['horizon_days']
            self.assertEqual(
                params['lightgbm_model_artifact_version'],
                self.command.PINNED_ARTIFACT_VERSIONS[horizon],
            )
            self.assertEqual(params['top_n_metric'], f'up_prob_{horizon}d')
            self.assertEqual(
                params['holding_period_days'],
                self.command.HORIZON_PROFILES[horizon]['holding_period_days'],
            )

    def test_pre_contract_override_is_absent_from_every_cell(self):
        for cell in self.cells:
            self.assertNotIn('allow_pre_contract_history', cell['parameters'])

    def test_chunking_stays_under_the_celery_soft_limit(self):
        for cell in self.cells:
            self.assertEqual(cell['parameters']['chunk_trading_days'], self.command.CHUNK_TRADING_DAYS)

    def test_execution_models_of_one_arm_are_adjacent(self):
        """Adjacency is what lets a pair share one signal surface in one process."""
        for start in range(0, len(self.cells), 2):
            pair = self.cells[start:start + 2]
            self.assertEqual(pair[0]['arm'], pair[1]['arm'])
            self.assertEqual(pair[0]['horizon'], pair[1]['horizon'])
            self.assertEqual(
                {pair[0]['execution_model'], pair[1]['execution_model']},
                set(self.command.EXECUTION_MODELS),
            )

    def test_cache_scope_is_shared_across_the_launch(self):
        scopes = {c['parameters']['matrix_signal_cache_key'] for c in self.cells}
        self.assertEqual(scopes, {'stk10-scope'})

    def test_digest_and_prediction_mode_are_visible_in_every_cell(self):
        """--dry-run is the human review artifact before 18 rows exist.

        Both were stamped in the create loop, AFTER the dry-run returned, so a reviewer
        could not confirm the registration digest or the prediction mode from it.
        """
        for cell in self.cells:
            self.assertEqual(cell['parameters']['preregistration_digest'], 'd8b09eb37d91')
            self.assertEqual(cell['parameters']['prediction_universe'], 'superset')


class ExecuteInlineStatusTests(TestCase):
    """A cell that does not COMPLETE must fail the command, not be reported as success."""

    def _command(self):
        import io

        from django.core.management.base import OutputWrapper

        from apps.backtest.management.commands.run_stk10_matrix import Command

        command = Command()
        buffer = io.StringIO()
        command.stdout = OutputWrapper(buffer)
        return command, buffer

    def _run(self, horizon=7):
        return BacktestRun.objects.create(
            name=f'stk10-status-h{horizon}',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            status=BacktestRun.Status.PENDING,
            start_date=date(2025, 1, 2),
            end_date=date(2025, 1, 31),
            initial_capital='200000.00',
            cash='200000.00',
            final_value=0,
            total_return=0,
            annualized_return=0,
            max_drawdown=0,
            sharpe_ratio=0,
            win_rate=0,
            total_trades=0,
            winning_trades=0,
            parameters={'horizon_days': horizon},
            report={},
            error_message='',
        )

    def _drive(self, terminal_status):
        from apps.backtest.management.commands import run_stk10_matrix as command_module

        def fake_run_backtest(run_id):
            BacktestRun.objects.filter(id=run_id).update(status=terminal_status)

        return patch.object(command_module, 'run_backtest', side_effect=fake_run_backtest)

    def test_failed_cell_raises_and_names_the_run(self):
        row = self._run()
        command, buffer = self._command()
        with self._drive(BacktestRun.Status.FAILED):
            with self.assertRaises(CommandError) as ctx:
                command._execute_inline([row.id], [7])
        message = str(ctx.exception)
        self.assertIn('did not reach', message)
        self.assertIn(str(row.id), message)
        self.assertIn('run_id=%d status=FAILED' % row.id, buffer.getvalue())

    def test_all_completed_reports_success(self):
        row = self._run()
        command, buffer = self._command()
        with self._drive(BacktestRun.Status.COMPLETED):
            command._execute_inline([row.id], [7])
        self.assertIn('COMPLETED', buffer.getvalue())

    def test_mixed_outcomes_fail_the_whole_matrix(self):
        """One bad cell out of 18 must not be averaged away into a success line."""
        good = self._run(horizon=7)
        bad = self._run(horizon=30)
        from apps.backtest.management.commands import run_stk10_matrix as command_module

        def fake_run_backtest(run_id):
            status = (BacktestRun.Status.COMPLETED if run_id == good.id
                      else BacktestRun.Status.FAILED)
            BacktestRun.objects.filter(id=run_id).update(status=status)

        command, buffer = self._command()
        with patch.object(command_module, 'run_backtest', side_effect=fake_run_backtest):
            with self.assertRaises(CommandError) as ctx:
                command._execute_inline([good.id, bad.id], [7, 30])
        self.assertIn(str(bad.id), str(ctx.exception))
        self.assertNotIn(str(good.id), str(ctx.exception).split('COMPLETED:')[1])


class Stk10FleetPinTests(SimpleTestCase):
    def setUp(self):
        from apps.backtest.management.commands import run_stk10_matrix as command

        self.command = command

    def _resolve(self, artifact):
        with patch.object(self.command.LightGBMModelArtifact.objects, 'filter') as mocked:
            mocked.return_value.order_by.return_value.first.return_value = artifact
            return self.command.resolve_pinned_artifacts([7])

    def test_matching_fleet_is_accepted(self):
        pinned = LightGBMModelArtifact(
            id=47, version=self.command.PINNED_ARTIFACT_VERSIONS[7], horizon_days=7,
        )
        self.assertEqual(self._resolve(pinned)[7].version, pinned.version)

    def test_moved_fleet_is_refused(self):
        """A retrain between registration and launch invalidates the measurement."""
        moved = LightGBMModelArtifact(id=99, version='lgb-7d-2026-12-01', horizon_days=7)
        with self.assertRaises(CommandError) as ctx:
            self._resolve(moved)
        self.assertIn('pre-registration pins', str(ctx.exception))

    def test_missing_active_artifact_is_refused(self):
        with self.assertRaises(CommandError) as ctx:
            self._resolve(None)
        self.assertIn('No active READY', str(ctx.exception))


ARMS = (
    {'universe_policy': 'research_full', 'st_layer': False, 'pit_index_codes': ['000905.SH']},
    {'universe_policy': 'default', 'st_layer': False, 'pit_index_codes': ['000905.SH']},
    {'universe_policy': 'default', 'st_layer': True, 'pit_index_codes': ['000905.SH']},
)


class PredictionUniverseParamTests(SimpleTestCase):
    def _run(self, **params):
        return BacktestRun(parameters=params)

    def test_defaults_to_arm_so_existing_runs_are_unchanged(self):
        self.assertEqual(_prediction_universe(self._run()), 'arm')

    def test_superset_is_read(self):
        self.assertEqual(_prediction_universe(self._run(prediction_universe='superset')), 'superset')

    def test_garbage_falls_back_to_arm(self):
        self.assertEqual(_prediction_universe(self._run(prediction_universe='everything')), 'arm')


class SupersetEligibilityTests(SimpleTestCase):
    def test_superset_predicts_research_full_st_off_regardless_of_the_arm(self):
        cache = {
            'prediction_universe': 'superset',
            'universe_policy': 'default',
            'st_layer': True,
            'pit_index_codes': ['000905.SH'],
        }
        with patch(
            'apps.backtest.tasks.effective_universe_tradeable_asset_ids', return_value=[1],
        ) as mocked:
            _prediction_eligible_asset_ids(D, cache)
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs['policy'], 'research_full')
        self.assertFalse(kwargs['st_layer'])

    def test_arm_mode_keeps_the_arm_universe(self):
        cache = {
            'prediction_universe': 'arm',
            'universe_policy': 'default',
            'st_layer': True,
            'pit_index_codes': ['000905.SH'],
        }
        with patch(
            'apps.backtest.tasks.effective_universe_tradeable_asset_ids', return_value=[1],
        ) as mocked:
            _prediction_eligible_asset_ids(D, cache)
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs['policy'], 'default')
        self.assertTrue(kwargs['st_layer'])

    def test_superset_eligibility_is_cached_separately_from_the_arm(self):
        """The nested cache must not collide with the arm's own entry."""
        cache = {
            'prediction_universe': 'superset',
            'universe_policy': 'default',
            'st_layer': True,
            'pit_index_codes': ['000905.SH'],
        }
        with patch(
            'apps.backtest.tasks.effective_universe_tradeable_asset_ids',
            side_effect=['superset', 'arm'],
        ) as mocked:
            first = _prediction_eligible_asset_ids(D, cache)
            second = _prediction_eligible_asset_ids(D, cache)
            arm = _eligible_backtest_asset_ids(D, cache)
        self.assertEqual(first, 'superset')
        self.assertEqual(second, 'superset')
        self.assertEqual(arm, 'arm')
        self.assertEqual(mocked.call_count, 2)


class SupersetSurfaceSharingTests(SimpleTestCase):
    def _keys(self, mode):
        keys = set()
        for arm in ARMS:
            cache = dict(arm)
            cache['prediction_universe'] = mode
            keys.add(_prediction_universe_cache_key(cache))
        return keys

    def test_all_three_arms_share_one_surface_in_superset_mode(self):
        self.assertEqual(len(self._keys('superset')), 1)

    def test_arms_do_not_share_in_arm_mode(self):
        self.assertEqual(len(self._keys('arm')), 3)

    def test_superset_key_is_the_research_full_identity(self):
        self.assertEqual(
            self._keys('superset').pop(),
            ('research_full', False, ('000905.SH',)),
        )


class SupersetSurfaceIntegrationTests(SimpleTestCase):
    def setUp(self):
        from apps.backtest.tasks import clear_backtest_process_caches

        clear_backtest_process_caches()

    def tearDown(self):
        from apps.backtest.tasks import clear_backtest_process_caches

        clear_backtest_process_caches()

    def _run(self, policy, st_layer):
        return BacktestRun(parameters={
            'matrix_signal_cache_key': 'stk10-integration-scope',
            'prediction_universe': 'superset',
            'universe_policy': policy,
            'st_layer': st_layer,
            'pit_index_codes': ['000905.SH'],
        })

    def _cache(self, policy, st_layer):
        return {
            'prediction_universe': 'superset',
            'universe_policy': policy,
            'st_layer': st_layer,
            'pit_index_codes': ['000905.SH'],
        }

    def test_second_arm_reuses_the_superset_surface(self):
        from apps.backtest import tasks

        artifact = LightGBMModelArtifact(
            id=47, version='lgb-7d-2026-11-09', horizon_days=7,
        )
        with patch.object(tasks, '_get_lightgbm_runtime', return_value={'model_artifact': artifact}), \
                patch.object(tasks, '_resolved_lightgbm_inference_backend', return_value='cpu_serial'), \
                patch.object(tasks, '_prediction_eligible_asset_ids', return_value=[1]), \
                patch.object(tasks, '_predict_lightgbm_for_asset', return_value={'up_probability': 0.6}) as predict:
            tasks._build_lightgbm_prediction_map(
                D, 7, self._cache('research_full', False), run=self._run('research_full', False),
            )
            second_cache = self._cache('default', True)
            tasks._build_lightgbm_prediction_map(
                D, 7, second_cache, run=self._run('default', True),
            )

        self.assertEqual(predict.call_count, 1)
        self.assertEqual(tasks._lightgbm_runtime_metrics(second_cache)['matrix_cache_hits'], 1)

    def test_arm_mode_does_not_share_across_different_arms(self):
        from apps.backtest import tasks

        artifact = LightGBMModelArtifact(
            id=47, version='lgb-7d-2026-11-09', horizon_days=7,
        )
        first_cache = dict(self._cache('research_full', False), prediction_universe='arm')
        second_cache = dict(self._cache('default', False), prediction_universe='arm')
        first_run = self._run('research_full', False)
        second_run = self._run('default', False)
        first_run.parameters['prediction_universe'] = 'arm'
        second_run.parameters['prediction_universe'] = 'arm'
        with patch.object(tasks, '_get_lightgbm_runtime', return_value={'model_artifact': artifact}), \
                patch.object(tasks, '_resolved_lightgbm_inference_backend', return_value='cpu_serial'), \
                patch.object(tasks, '_prediction_eligible_asset_ids', return_value=[1]), \
                patch.object(tasks, '_predict_lightgbm_for_asset', return_value={'up_probability': 0.6}) as predict:
            tasks._build_lightgbm_prediction_map(D, 7, first_cache, run=first_run)
            tasks._build_lightgbm_prediction_map(D, 7, second_cache, run=second_run)

        self.assertEqual(predict.call_count, 2)
        self.assertEqual(tasks._lightgbm_runtime_metrics(second_cache)['matrix_cache_hits'], 0)


class SupersetCandidateFilterTests(SimpleTestCase):
    """The load-bearing guard: a wider prediction surface must not widen what is traded."""

    MAPPING = {
        1: {'up_probability': '0.9'},
        2: {'up_probability': '0.8'},
    }

    def _candidates(self, mode, eligible, top_n=5):
        from apps.backtest import tasks

        cache = {
            'prediction_universe': mode,
            'universe_policy': 'default',
            'st_layer': True,
            'pit_index_codes': ['000905.SH'],
        }
        with patch.object(tasks, '_build_lightgbm_prediction_map', return_value=self.MAPPING), \
                patch.object(tasks, '_eligible_backtest_asset_ids', return_value=eligible) as mocked:
            rows = tasks._build_lightgbm_candidates(D, 7, Decimal('0.45'), top_n, cache)
        return [row['asset_id'] for row in rows], mocked

    def test_superset_only_asset_is_excluded_from_candidates(self):
        ids, _ = self._candidates('superset', [1])
        self.assertEqual(ids, [1])

    def test_superset_only_top_asset_is_filtered_before_top_n(self):
        ids, _ = self._candidates('superset', [2], top_n=1)
        self.assertEqual(ids, [2])

    def test_arm_mode_does_not_re_filter(self):
        """In arm mode the map is already arm-scoped, so re-filtering would be redundant."""
        ids, mocked = self._candidates('arm', [1])
        self.assertEqual(ids, [1, 2])
        mocked.assert_not_called()


class SerializerPredictionUniverseTests(TestCase):
    def _payload(self, **params):
        base = {'top_n': 3, 'horizon_days': 7, 'up_threshold': 0.5, 'prediction_source': 'heuristic'}
        base.update(params)
        return {
            'name': 'stk10-universe-test',
            'strategy_type': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            'start_date': date(2020, 1, 2),
            'end_date': date(2020, 6, 30),
            'parameters': base,
        }

    def test_superset_accepted_with_lightgbm(self):
        serializer = BacktestRunSerializer(data=self._payload(
            prediction_source='lightgbm', prediction_universe='superset',
        ))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['parameters']['prediction_universe'], 'superset')

    def test_garbage_rejected(self):
        serializer = BacktestRunSerializer(data=self._payload(
            prediction_source='lightgbm', prediction_universe='everything',
        ))
        self.assertFalse(serializer.is_valid())
        self.assertIn('parameters', serializer.errors)

    def test_superset_without_lightgbm_rejected(self):
        """The heuristic path has no signal surface, so superset mode would be a silent no-op."""
        serializer = BacktestRunSerializer(data=self._payload(prediction_universe='superset'))
        self.assertFalse(serializer.is_valid())
        self.assertIn('parameters', serializer.errors)

    def test_absent_stays_absent(self):
        serializer = BacktestRunSerializer(data=self._payload())
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn('prediction_universe', serializer.validated_data['parameters'])
