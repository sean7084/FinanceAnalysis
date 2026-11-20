"""#144 residual: ModelVersion.metrics carries holdout_start / purge_sessions (card A6)."""
from datetime import date

from django.test import TestCase

from apps.prediction.models import ModelVersion
from apps.prediction.tasks_lightgbm import _register_lightgbm_model_version


class HoldoutProvenanceRegistrationTests(TestCase):
    def test_register_preserves_holdout_provenance(self):
        _register_lightgbm_model_version(
            horizon_days=7,
            model_version='lgb-7d-prov-test',
            training_start=date(2016, 6, 1),
            training_end=date(2026, 11, 1),
            metrics={
                'accuracy': 0.49,
                'test_accuracy': 0.49,
                'evaluation_mode': 'purged_holdout',
                'holdout_start': '2025-10-21',
                'purge_sessions': 7,
                'rank_ic': 0.05,
                'rank_ic_tstat': 3.9,
            },
            feature_names=['rsi'],
            metadata={},
        )
        row = ModelVersion.objects.get(
            model_type=ModelVersion.ModelType.LIGHTGBM, version='lgb-7d-prov-test',
        )
        self.assertEqual(row.metrics['evaluation_mode'], 'purged_holdout')
        self.assertEqual(row.metrics['holdout_start'], '2025-10-21')
        self.assertEqual(row.metrics['purge_sessions'], 7)
        self.assertEqual(row.metrics['test_accuracy'], 0.49)