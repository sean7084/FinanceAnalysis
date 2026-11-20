"""H2 / D9: the stock measurement ledger reuses the ETF digest, separate path."""
import json
import os
import tempfile

from django.test import SimpleTestCase, override_settings

from apps.etf_rotation.evidence import preregistration_digest as etf_digest
from apps.prediction import evidence


class StockLedgerTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'stock-ledger.jsonl')

    def tearDown(self):
        self.tmp.cleanup()

    def test_digest_matches_etf_single_implementation(self):
        text = '# prereg\narms: 4\n'
        self.assertEqual(evidence.preregistration_digest(text), etf_digest(text))
        self.assertEqual(len(evidence.preregistration_digest(text)), 12)

    def test_record_and_read_roundtrip(self):
        with override_settings(STOCK_EVIDENCE_PATH=self.path):
            rec = evidence.record_stock_measurement(
                context={'arm': 'csi1000_board_st', 'panel_start': '2018-01-02', 'horizon': 30},
                results={'mean_ic': 0.05, 'folds_beating': 6},
                verdict='pass',
                preregistration='abc123def456',
            )
            rows = evidence.read_stock_measurements()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], rec['id'])
        self.assertEqual(rows[0]['preregistration'], 'abc123def456')
        self.assertEqual(rows[0]['context']['arm'], 'csi1000_board_st')
        self.assertEqual(rows[0]['results']['mean_ic'], 0.05)

    def test_unregistered_run_recorded_with_empty_hash(self):
        with override_settings(STOCK_EVIDENCE_PATH=self.path):
            evidence.record_stock_measurement(context={'arm': 'x'}, results={})
            rows = evidence.read_stock_measurements()
        self.assertEqual(rows[0]['preregistration'], '')

    def test_append_only_and_repeats(self):
        ctx = {'arm': 'csi500_board', 'panel_start': '2018-01-02', 'horizon': 30}
        with override_settings(STOCK_EVIDENCE_PATH=self.path):
            evidence.record_stock_measurement(context=ctx, results={'mean_ic': 0.04})
            evidence.record_stock_measurement(context=ctx, results={'mean_ic': 0.041})
            rows = evidence.read_stock_measurements()
            repeats = evidence.stock_context_repeats(rows, ctx)
        self.assertEqual(len(rows), 2)
        self.assertEqual(repeats, 2)
        self.assertNotEqual(rows[0]['id'], rows[1]['id'])

    def test_missing_file_is_empty(self):
        with override_settings(STOCK_EVIDENCE_PATH=os.path.join(self.tmp.name, 'absent.jsonl')):
            self.assertEqual(evidence.read_stock_measurements(), [])