import json
from decimal import Decimal
from datetime import datetime
from django.db import connection
from django.test.utils import CaptureQueriesContext
from apps.analytics.models import TechnicalIndicator
from apps.markets.models import OHLCV
from apps.prediction.tasks import _feature_snapshot
from apps.prediction.tasks_lightgbm import _extract_features_for_asset
from apps.prediction.odds import estimate_trade_decision

# 1. Find a suitable asset and date
indicator = TechnicalIndicator.objects.order_by('-timestamp').first()
if not indicator:
    print("No TechnicalIndicator data found at all.")
    exit()

asset_id = indicator.asset_id
as_of = indicator.timestamp.date()
print(f"Testing with Asset ID: {asset_id}, Date: {as_of}")

def analyze_queries(queries):
    counts = {}
    for q in queries:
        sql = q['sql'].lower()
        # Basic parsing to identity tables
        words = sql.replace('"', '').replace('`', '').split()
        for i, word in enumerate(words):
            if word in ['from', 'join'] and i+1 < len(words):
                table = words[i+1]
                counts[table] = counts.get(table, 0) + 1
    return counts

# 2. Measure queries for _feature_snapshot
with CaptureQueriesContext(connection) as ctx:
    try:
        _feature_snapshot(asset_id, as_of, cache={})
    except Exception as e:
        print(f"_feature_snapshot failed: {e}")
print(f"_feature_snapshot query count: {len(ctx)}")
if len(ctx) > 0:
    print(f"Breakdown: {analyze_queries(ctx.captured_queries)}")

# 3. Measure queries for _extract_features_for_asset
with CaptureQueriesContext(connection) as ctx:
    try:
        _extract_features_for_asset(asset_id, as_of, cache={})
    except Exception as e:
        print(f"_extract_features_for_asset failed: {e}")
print(f"_extract_features_for_asset query count: {len(ctx)}")
if len(ctx) > 0:
    print(f"Breakdown: {analyze_queries(ctx.captured_queries)}")

# 4. Measure queries for estimate_trade_decision
with CaptureQueriesContext(connection) as ctx:
    try:
        estimate_trade_decision(asset_id, as_of, 7, Decimal('0.6'), 'UP', cache={})
    except Exception as e:
        print(f"estimate_trade_decision failed: {e}")
print(f"estimate_trade_decision query count: {len(ctx)}")
if len(ctx) > 0:
    print(f"Breakdown: {analyze_queries(ctx.captured_queries)}")
