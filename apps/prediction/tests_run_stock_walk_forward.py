"""H2: run_stock_walk_forward command gating and the fold-artifact invariant.

The command must refuse a missing prereg, a panel-end that drifts from the frozen
manifest, and an undeclared arm; fold artifacts it persists must never be active.
Heavy collaborators (matrix provider, OHLCV/Asset/suspension queries, fit) are
mocked so the CLI contract is tested without the DB matrix build.
"""
import json
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.prediction.models_lightgbm import LightGBMModelArtifact
from apps.prediction.prereg import PreregistrationError

CMD = 'apps.prediction.management.commands.run_stock_walk_forward'


def _prereg(tmp, *, panel_end='2021-12-31', arms=('board_only',), starts=('2021-01-04',)):
    manifest = {
        'arms': list(arms), 'panel_starts': list(starts), 'panel_end': panel_end,
        'horizons': [3, 7], 'fold_plan': 'expanding-7', 'schema_version': 'v3',
        'feature_names': ['mom_5d', 'vol_20d'],
    }
    path = Path(tmp) / 'prereg.md'
    path.write_text('# prereg\n\n```json\n' + json.dumps(manifest) + '\n```\n', encoding='utf-8')
    return str(path)


class RunStockWalkForwardCommandTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prereg = _prereg(self.tmp.name)

    def _db_patches(self, trade_dates=()):
        ohlcv = MagicMock()
        chain = ohlcv.objects.filter.return_value.values_list.return_value
        chain.distinct.return_value.order_by.return_value = list(trade_dates)
        asset = MagicMock()
        asset.objects.values_list.return_value = []
        suspension = MagicMock()
        suspension.objects.filter.return_value.values_list.return_value = []
        return [
            patch(f'{CMD}.OHLCV', ohlcv), patch(f'{CMD}.Asset', asset),
            patch(f'{CMD}.AssetSuspension', suspension),
            patch(f'{CMD}.st_asset_ids_by_dates', return_value={}),
            patch(f'{CMD}.DBMatrixProvider', return_value=MagicMock()),
        ]

    def test_preregistration_required(self):
        with self.assertRaises(CommandError):
            call_command('run_stock_walk_forward', '--arm', 'board_only',
                         '--panel-start', '2021-01-04', '--panel-end', '2021-12-31')

    def test_panel_end_mismatch_refused(self):
        with self.assertRaises(CommandError) as ctx:
            call_command('run_stock_walk_forward', '--preregistration', self.prereg,
                         '--arm', 'board_only', '--panel-start', '2021-01-04',
                         '--panel-end', '2022-06-30')
        self.assertIn('frozen panel', str(ctx.exception))

    def test_undeclared_arm_refused(self):
        patches = self._db_patches()
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        with self.assertRaises(PreregistrationError):
            call_command('run_stock_walk_forward', '--preregistration', self.prereg,
                         '--arm', 'cap', '--panel-start', '2021-01-04',
                         '--panel-end', '2021-12-31', '--horizons', '3')

    def test_fold_artifact_persisted_never_active(self):
        captured = {}

        def fake_run_cell(cell, **kwargs):
            captured['persist'] = kwargs['persist_fn']
            return {'horizons': {}}

        patches = self._db_patches(trade_dates=[date(2021, 1, 4)])
        patches.append(patch(f'{CMD}.run_cell', side_effect=fake_run_cell))
        patches.append(patch(f'{CMD}.tl._save_model_artifacts', return_value=None))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        call_command('run_stock_walk_forward', '--preregistration', self.prereg,
                     '--arm', 'board_only', '--panel-start', '2021-01-04',
                     '--panel-end', '2021-12-31', '--horizons', '3')
        fit = {'holdout_metrics': {'rank_ic': 0.05}, 'model': object()}
        captured['persist'](fit, 3, 'wf-board_only-2021-01-04-f0-h3')
        artifact = LightGBMModelArtifact.objects.get(version='wf-board_only-2021-01-04-f0-h3')
        self.assertFalse(artifact.is_active)
        self.assertEqual(artifact.horizon_days, 3)
        self.assertTrue(artifact.metadata['fold_artifact'])