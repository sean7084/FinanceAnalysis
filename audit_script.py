import os
import sys
import django
import random
import datetime

# Ensure the root directory is in sys.path
sys.path.append('/home/chang-liu/Documents/FinanceAnalysis')

# Ensure environment variables are loaded
import environ
env = environ.Env()
if os.path.exists(".env"):
    environ.Env.read_env(".env")

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

# Use correct import paths
from apps.backtest.models import BacktestRun
from apps.backtest.tasks import _pick_candidates, _eligible_backtest_asset_ids, point_in_time_union_asset_ids
from apps.markets.models import OHLCV
from apps.prediction.models import Asset

def audit():
    # 1. Select 5 deterministic random trading dates
    all_dates = sorted(list(OHLCV.objects.filter(
        date__gte=datetime.date(2024, 1, 1),
        date__lte=datetime.date(2026, 4, 30)
    ).values_list('date', flat=True).distinct()))
    
    if not all_dates:
        print("No OHLCV dates found in range.")
        return

    random.seed(42)
    sample_dates = sorted(random.sample(all_dates, min(5, len(all_dates))))
    print(f"Sampled dates: {sample_dates}")

    # Build unsaved run 1
    run1 = BacktestRun(
        prediction_source='heuristic',
        candidate_mode='top_n',
        top_n_metric='up_prob_7d',
        top_n=5,
        horizon_days=7,
        up_threshold=0.55
    )

    cache = {}
    first_date_with_min_2 = None

    for dt in sample_dates:
        print(f"\n--- Date: {dt} ---")
        candidates = _pick_candidates(run1, dt, cache=cache)
        eligible_ids = _eligible_backtest_asset_ids(dt, cache=cache)
        pit_ids = point_in_time_union_asset_ids(dt)
        
        asset_map = {a.id: a.symbol for a in Asset.objects.filter(id__in=[c.asset_id for c in candidates])}
        
        all_in_eligible = all(c.asset_id in eligible_ids for c in candidates)
        all_in_pit = all(c.asset_id in pit_ids for c in candidates)
        
        print(f"Candidate count: {len(candidates)} | Eligible universe: {len(eligible_ids)}")
        print(f"All candidates in eligible: {all_in_eligible} | All in PIT: {all_in_pit}")
        
        for c in candidates:
            p = c.signal_payload
            print(f"  Symbol: {asset_map.get(c.asset_id)} | Rank: {c.rank_value:.4f} | UpProb: {p.get('up_probability')} | TopNMetric: {p.get('top_n_metric')} | Horizon: {p.get('horizon_days')}")

        if len(candidates) >= 2 and first_date_with_min_2 is None:
            first_date_with_min_2 = dt

    # 4. Step 4
    if first_date_with_min_2:
        print(f"\n--- Step 4 (Date: {first_date_with_min_2}) ---")
        run2 = BacktestRun(
            prediction_source='heuristic',
            candidate_mode='top_n',
            top_n_metric='trade_score', 
            top_n=2,
            max_positions=1,
            horizon_days=7,
            up_threshold=0.20
        )
        candidates2 = _pick_candidates(run2, first_date_with_min_2, cache=cache)
        print(f"Run top_n=2, max_positions=1. _pick_candidates returned {len(candidates2)} rows.")

    # 5. Step 5
    if first_date_with_min_2:
        print(f"\n--- Step 5 (Date: {first_date_with_min_2}) ---")
        run3 = BacktestRun(
            candidate_mode='trade_score',
            trade_score_scope='independent',
            prediction_source='heuristic',
            horizon_days=7,
            top_n=5,
            max_positions=3,
            up_threshold=0.55,
            trade_score_threshold=1.0
        )
        candidates3 = _pick_candidates(run3, first_date_with_min_2, cache=cache)
        all_independent = all(c.signal_payload.get('trade_score_scope') == 'independent' for c in candidates3)
        print(f"Run independent trade_score. Count: {len(candidates3)} | All scope='independent': {all_independent}")

    # 6. Step 6
    if first_date_with_min_2:
        print(f"\n--- Step 6 (Date: {first_date_with_min_2}) ---")
        run4 = BacktestRun(
            prediction_source='heuristic',
            candidate_mode='top_n',
            top_n_metric='up_prob_30d',
            horizon_days=7,
            top_n=3,
            up_threshold=0.55
        )
        candidates4 = _pick_candidates(run4, first_date_with_min_2, cache=cache)
        if candidates4:
            p = candidates4[0].signal_payload
            print(f"Candidate top_n_metric: {p.get('top_n_metric')} | horizon_days: {p.get('horizon_days')}")
            print(f"Selection matches 30d metric: {p.get('top_n_metric') == 'up_prob_30d'}")

audit()
