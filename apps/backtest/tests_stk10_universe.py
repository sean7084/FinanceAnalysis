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
from datetime import date
from unittest.mock import patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from apps.backtest.models import BacktestRun
from apps.backtest.serializers import BacktestRunSerializer
from apps.backtest.tasks import (
    _eligible_backtest_asset_ids,
    _matrix_signal_cache_key,
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

    def test_no_scope_means_no_key(self):
        self.assertIsNone(
            _matrix_signal_cache_key(BacktestRun(parameters={}), 'lightgbm', D, 7, MODEL, (), self.UNIVERSE)
        )


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
