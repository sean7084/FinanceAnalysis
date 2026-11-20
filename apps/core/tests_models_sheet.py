"""#144 residual: models.md surfaces the holdout provenance (campaign card A6)."""
from django.test import TestCase

from apps.core.management.commands.export_documentation_facts import build_models
from apps.prediction.models import ModelVersion
from apps.prediction.models_lightgbm import LightGBMModelArtifact


class ModelsSheetHoldoutColumnsTests(TestCase):
    def test_active_artifact_holdout_columns_rendered(self):
        LightGBMModelArtifact.objects.create(
            horizon_days=7,
            version='lgb-7d-sheet-test',
            status=LightGBMModelArtifact.Status.READY,
            is_active=True,
            artifact_path='models/lightgbm/7d_lgb-7d-sheet-test',
            metrics_json={
                'accuracy': 0.49,
                'evaluation_mode': 'purged_holdout',
                'rank_ic': 0.0559,
                'rank_ic_tstat': 3.96,
                'icir': 0.25,
                'holdout_start': '2025-10-21',
            },
            feature_names=['rsi'],
        )
        out = build_models()
        self.assertIn('Eval mode', out)
        self.assertIn('Rank IC', out)
        self.assertIn('IC t-stat', out)
        self.assertIn('ICIR', out)
        self.assertIn('Holdout start', out)
        self.assertIn('purged_holdout', out)
        self.assertIn('2025-10-21', out)

    def test_modelversion_registry_holdout_columns_rendered(self):
        ModelVersion.objects.create(
            model_type=ModelVersion.ModelType.LIGHTGBM,
            version='lgb-7d-sheet-version-test',
            status=ModelVersion.Status.READY,
            metrics={
                'accuracy': 0.49,
                'evaluation_mode': 'purged_holdout',
                'rank_ic': 0.0559,
                'rank_ic_tstat': 3.96,
                'holdout_start': '2025-10-21',
            },
        )
        out = build_models()
        self.assertIn('lgb-7d-sheet-version-test', out)
        self.assertIn('IC t', out)
        registry_section = out.split('## ModelVersion registry')[1]
        self.assertIn('purged_holdout', registry_section)
        self.assertIn('2025-10-21', registry_section)

    def test_absent_holdout_fields_render_dash(self):
        LightGBMModelArtifact.objects.create(
            horizon_days=3,
            version='lgb-3d-sheet-legacy',
            status=LightGBMModelArtifact.Status.READY,
            is_active=True,
            artifact_path='models/lightgbm/3d_lgb-3d-sheet-legacy',
            metrics_json={'accuracy': 0.55},
            feature_names=['rsi'],
        )
        out = build_models()
        self.assertIn('Eval mode', out)
        self.assertIn('&mdash;', out)