"""A5 / #195: the v3 training schema (33 features) and the macro release-lag wiring."""
import inspect
from datetime import date

import pandas as pd
from django.test import TestCase

from apps.macro.models import MacroSnapshot, MarketContext
from apps.prediction import tasks_lightgbm as t


class FeatureSchemaV3Tests(TestCase):
    def test_base_schema_exact_set(self):
        expected = (
            'rsi', 'mom_5d', 'rs_score',
            'rsi_lag_3d', 'rsi_delta_3d', 'mom_5d_delta_3d', 'rs_score_delta_3d',
            'rsi_lag_5d', 'rsi_delta_5d', 'mom_5d_delta_5d', 'rs_score_delta_5d',
            'rsi_lag_10d', 'rsi_delta_10d', 'mom_5d_delta_10d', 'rs_score_delta_10d',
            'return_3d', 'return_5d', 'return_10d',
            'relative_volume_5d', 'relative_volume_20d', 'realized_volatility_5d',
            'pe_ttm_percentile', 'pb_percentile', 'roe_trend',
            'main_force_flow', 'margin_flow', 'factor_composite',
            'macro_phase', 'pmi_manufacturing', 'pmi_non_manufacturing',
        )
        self.assertEqual(t.TRAINING_BASE_FEATURES_V3, expected)
        self.assertEqual(len(set(expected)), 30)

    def test_removed_features_absent(self):
        outputs = [name for name, _, _ in t.INTERACTION_FEATURE_SPECS]
        schema = set(t.TRAINING_BASE_FEATURES_V3) | set(outputs)
        self.assertEqual(len(schema), 33)
        for removed in (
            'northbound_flow', 'yield_curve', 'northbound_flow_x_mom_5d',
            'sentiment_7d', 'sentiment_7d_avg_20d', 'factor_composite_x_sentiment',
        ):
            self.assertNotIn(removed, schema)

    def test_interaction_build_on_v3_base(self):
        df = pd.DataFrame({name: [0.5] for name in t.TRAINING_BASE_FEATURES_V3})
        df, names = t._build_interaction_features(df, list(t.TRAINING_BASE_FEATURES_V3))
        self.assertEqual(len(names), 33)
        self.assertIn('rsi_x_macro_phase', names)
        self.assertNotIn('northbound_flow_x_mom_5d', names)

    def test_legacy_extractor_keys_retained_for_v2_artifacts(self):
        src = inspect.getsource(t._extract_features_for_asset)
        self.assertIn("features['northbound_flow']", src)
        self.assertIn("features['yield_curve']", src)

    def test_legacy_interaction_still_produced_for_v2_artifacts(self):
        """The pre-A5 fleet declares northbound_flow_x_mom_5d; serving must produce it.

        Regression: the source-text assertion above stayed green while the interaction
        spec had been deleted, so all three ACTIVE artifacts (native_nan, 36 declared
        names) raised "feature schema mismatch" on every prediction -- daily beat and
        backtest alike. A text assertion about the base keys cannot cover a feature
        built from a spec list, so this one exercises the builder.
        """
        augmented = t._augment_single_asset_features(
            {'northbound_flow': 0.5, 'mom_5d': 0.04, 'rsi': 55.0}
        )
        self.assertIn('northbound_flow_x_mom_5d', augmented)
        self.assertAlmostEqual(augmented['northbound_flow_x_mom_5d'], 0.5 * 0.04)

    def test_legacy_interaction_is_not_produced_when_inputs_are_absent(self):
        augmented = t._augment_single_asset_features({'rsi': 55.0, 'mom_5d': 0.04})
        self.assertNotIn('northbound_flow_x_mom_5d', augmented)

    def test_legacy_specs_never_leak_into_the_training_schema(self):
        schema = set(t.TRAINING_BASE_FEATURES_V3) | {
            name for name, _, _ in t.INTERACTION_FEATURE_SPECS
        }
        for legacy_name, _, _ in t.LEGACY_INTERACTION_FEATURE_SPECS:
            self.assertNotIn(legacy_name, schema)
        df = pd.DataFrame({name: [0.5] for name in t.TRAINING_BASE_FEATURES_V3})
        df, names = t._build_interaction_features(df, list(t.TRAINING_BASE_FEATURES_V3))
        self.assertNotIn('northbound_flow_x_mom_5d', names)
        self.assertNotIn('northbound_flow_x_mom_5d', df.columns)

    def test_engineered_feature_version_bumped(self):
        src = inspect.getsource(t.train_lightgbm_models)
        self.assertIn("'engineered_feature_version': 'v3'", src)


class MacroReleaseLagTests(TestCase):
    def test_prepare_shifts_frames_by_publication_lag(self):
        MacroSnapshot.objects.create(
            date=date(2020, 3, 1),
            pmi_manufacturing=51.0,
            pmi_non_manufacturing=52.0,
            cn10y_yield=2.8,
            cn3y_yield=2.2,
        )
        MarketContext.objects.create(
            context_key='current',
            macro_phase=MarketContext.MacroPhase.OVERHEAT,
            is_active=True,
            starts_at=date(2020, 3, 1),
        )
        macro_df, context_df = t._prepare_macro_dataframe(date(2020, 3, 31))
        # PMI publishes with a 1-day lag; the phase inference reads yields (2-day lag).
        self.assertEqual(str(macro_df['date'].iloc[0].date()), '2020-03-02')
        self.assertEqual(str(context_df['date'].iloc[0].date()), '2020-03-03')

    def test_prepare_empty_frames_still_typed(self):
        macro_df, context_df = t._prepare_macro_dataframe(date(2020, 3, 31))
        self.assertTrue(macro_df.empty)
        self.assertTrue(context_df.empty)