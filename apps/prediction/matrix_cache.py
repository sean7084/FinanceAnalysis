"""Feature-matrix cache for the stock walk-forward harness (H2 / plan D7).

The expensive part of a fold campaign is building the feature matrix, not the
LightGBM fit. The matrix is built ONCE per (index, panel_start) over the frozen
panel at the board level (serving default policy = ex-board), carrying st_flag_asof
and circ_mv_asof auxiliary columns; arms are row masks over it. The cache key binds
everything that would change the matrix: index codes, panel window, feature schema
version, missing-value strategy, the storage schema era (param_key vs legacy jsonb),
and an OHLCV signature (row count + max updated_at) so a data refresh invalidates it.
"""
import hashlib
import json
import os


def matrix_cache_key(*, index_codes, panel_start, panel_end, schema_version,
                     missing_value_strategy, storage_era, ohlcv_signature) -> str:
    payload = {
        'index_codes': sorted(index_codes),
        'panel_start': str(panel_start),
        'panel_end': str(panel_end),
        'schema_version': schema_version,
        'missing_value_strategy': missing_value_strategy,
        'storage_era': storage_era,
        'ohlcv_signature': ohlcv_signature,
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()[:16]


def matrix_cache_dir(root, key) -> str:
    return os.path.join(str(root), key)


def ohlcv_signature(row_count, max_updated_at) -> str:
    return f'{int(row_count)}:{max_updated_at}'