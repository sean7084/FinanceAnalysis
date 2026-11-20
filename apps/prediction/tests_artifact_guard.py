"""STK-06: the artifact registry — the per-horizon deployment authority the daily
beat actually loads — carries the same activation guard as ModelVersion (issue #194).
"""
from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.backtest.models import BacktestRun
from apps.backtest.tasks import _get_selected_lightgbm_artifact
from apps.prediction.models_lightgbm import LightGBMModelArtifact


def _artifact(version='guard-test', horizon=7, metrics=None,
              status=LightGBMModelArtifact.Status.READY, is_active=False):
    return LightGBMModelArtifact.objects.create(
        horizon_days=horizon,
        version=version,
        status=status,
        artifact_path=f'models/lightgbm/{horizon}d_{version}',
        metrics_json=metrics if metrics is not None else {'accuracy': 0.49, 'rank_ic': 0.03},
        is_active=is_active,
    )


class ArtifactActivationGuardTests(TestCase):
    def test_archived_artifact_cannot_be_active(self):
        artifact = _artifact()
        artifact.status = LightGBMModelArtifact.Status.ARCHIVED
        artifact.is_active = True
        with self.assertRaises(ValidationError) as ctx:
            artifact.save()
        self.assertIn('ARCHIVED', str(ctx.exception))

    def test_implausible_accuracy_blocks_activation(self):
        with self.assertRaises(ValidationError) as ctx:
            _artifact(metrics={'accuracy': 0.95}, is_active=True)
        self.assertIn('accuracy', str(ctx.exception))

    def test_implausible_rank_ic_blocks_activation(self):
        with self.assertRaises(ValidationError) as ctx:
            _artifact(metrics={'rank_ic': 0.41}, is_active=True)
        self.assertIn('rank_ic', str(ctx.exception))

    def test_plausible_metrics_activate(self):
        artifact = _artifact(metrics={'accuracy': 0.49, 'rank_ic': 0.03}, is_active=True)
        self.assertTrue(artifact.is_active)

    def test_inactive_artifact_skips_validation(self):
        artifact = _artifact(metrics={'accuracy': 0.95}, is_active=False)
        self.assertFalse(artifact.is_active)

    def test_update_or_create_activation_is_guarded(self):
        # The training path activates via update_or_create defaults; save() must cover it.
        with self.assertRaises(ValidationError):
            LightGBMModelArtifact.objects.update_or_create(
                horizon_days=7, version='guard-test',
                defaults={
                    'status': LightGBMModelArtifact.Status.READY,
                    'artifact_path': 'x',
                    'metrics_json': {'accuracy': 0.95},
                    'is_active': True,
                },
            )


class ArtifactLoaderFailClosedTests(TestCase):
    def _run(self):
        return BacktestRun.objects.create(
            name='guard-loader-test',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=date(2020, 1, 2),
            end_date=date(2020, 6, 30),
            parameters={
                'prediction_source': 'lightgbm', 'horizon_days': 7,
                'top_n': 3, 'up_threshold': 0.5,
            },
        )

    def test_loader_refuses_when_no_artifact(self):
        run = self._run()
        with self.assertRaises(ValueError) as ctx:
            _get_selected_lightgbm_artifact(run, 7)
        self.assertIn('No active LightGBM artifact', str(ctx.exception))

    def test_loader_refuses_archived_only(self):
        _artifact(status=LightGBMModelArtifact.Status.ARCHIVED, is_active=False)
        run = self._run()
        with self.assertRaises(ValueError):
            _get_selected_lightgbm_artifact(run, 7)

    def test_loader_selects_active_ready(self):
        artifact = _artifact(is_active=True)
        run = self._run()
        selected = _get_selected_lightgbm_artifact(run, 7)
        self.assertEqual(selected.id, artifact.id)