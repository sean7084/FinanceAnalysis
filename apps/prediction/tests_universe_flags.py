"""B1b part 2b: rebuild command universe flags + provenance reader subset."""
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from apps.backtest.tasks import _reference_metadata_subset


def _passthrough_contract(**kwargs):
    return kwargs['requested_start']


class RebuildUniverseFlagsTests(TestCase):
    def test_flags_reach_trainer(self):
        with patch('apps.prediction.management.commands.rebuild_lightgbm_pipeline.train_lightgbm_models') as train, \
             patch('apps.prediction.management.commands.rebuild_lightgbm_pipeline.enforce_date_contract', side_effect=_passthrough_contract):
            train.return_value = {}
            call_command(
                'rebuild_lightgbm_pipeline',
                '--start-date', '2016-06-01', '--end-date', '2026-11-01',
                '--skip-backfill',
                '--universe-policy', 'research_full',
                '--pit-index-codes', '000852.SH,000905.SH',
            )
        kwargs = train.call_args.kwargs
        self.assertEqual(kwargs['universe_policy'], 'research_full')
        self.assertEqual(kwargs['pit_index_codes'], ['000852.SH', '000905.SH'])

    def test_defaults_are_none(self):
        with patch('apps.prediction.management.commands.rebuild_lightgbm_pipeline.train_lightgbm_models') as train, \
             patch('apps.prediction.management.commands.rebuild_lightgbm_pipeline.enforce_date_contract', side_effect=_passthrough_contract):
            train.return_value = {}
            call_command('rebuild_lightgbm_pipeline', '--start-date', '2016-06-01', '--end-date', '2026-11-01', '--skip-backfill')
        kwargs = train.call_args.kwargs
        self.assertIsNone(kwargs['universe_policy'])
        self.assertIsNone(kwargs['pit_index_codes'])


class ProvenanceReaderTests(SimpleTestCase):
    def test_subset_includes_universe_keys(self):
        metadata = {
            'effective_universe_policy': 'research_full',
            'pit_index_codes': ['000852.SH'],
            'universe_version': 'v1',
            'pit_benchmark_code': 'CSI1000_PIT',
            'missing_value_strategy': 'native_nan',
            'unrelated_key': 'dropped',
        }
        row = _reference_metadata_subset(metadata)
        self.assertEqual(row['effective_universe_policy'], 'research_full')
        self.assertEqual(row['pit_index_codes'], ['000852.SH'])
        self.assertEqual(row['pit_benchmark_code'], 'CSI1000_PIT')
        self.assertNotIn('unrelated_key', row)

    def test_subset_non_dict_is_empty(self):
        self.assertEqual(_reference_metadata_subset(None), {})