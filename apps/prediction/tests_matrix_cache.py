"""H2 / D7: matrix cache key binds every matrix-changing input."""
from django.test import SimpleTestCase

from apps.prediction.matrix_cache import matrix_cache_key, matrix_cache_dir, ohlcv_signature

BASE = dict(
    index_codes=['000905.SH'],
    panel_start='2018-01-02',
    panel_end='2026-11-19',
    schema_version='v3',
    missing_value_strategy='native_nan',
    storage_era='param_key',
    ohlcv_signature='180150010:2026-11-19T02:00:00+00:00',
)


class MatrixCacheKeyTests(SimpleTestCase):
    def test_key_is_stable_and_16_hex(self):
        k1 = matrix_cache_key(**BASE)
        k2 = matrix_cache_key(**BASE)
        self.assertEqual(k1, k2)
        self.assertEqual(len(k1), 16)

    def test_index_order_insensitive(self):
        a = matrix_cache_key(**{**BASE, 'index_codes': ['000905.SH', '000852.SH']})
        b = matrix_cache_key(**{**BASE, 'index_codes': ['000852.SH', '000905.SH']})
        self.assertEqual(a, b)

    def test_storage_era_changes_key(self):
        a = matrix_cache_key(**BASE)
        b = matrix_cache_key(**{**BASE, 'storage_era': 'legacy_jsonb'})
        self.assertNotEqual(a, b)

    def test_schema_version_changes_key(self):
        a = matrix_cache_key(**BASE)
        b = matrix_cache_key(**{**BASE, 'schema_version': 'v2'})
        self.assertNotEqual(a, b)

    def test_ohlcv_signature_changes_key(self):
        a = matrix_cache_key(**BASE)
        b = matrix_cache_key(**{**BASE, 'ohlcv_signature': ohlcv_signature(180150011, '2026-11-19T02:00:00+00:00')})
        self.assertNotEqual(a, b)

    def test_panel_start_changes_key(self):
        a = matrix_cache_key(**BASE)
        b = matrix_cache_key(**{**BASE, 'panel_start': '2019-01-02'})
        self.assertNotEqual(a, b)

    def test_cache_dir(self):
        self.assertEqual(matrix_cache_dir('tmp/cache', 'abc123'), 'tmp/cache/abc123')