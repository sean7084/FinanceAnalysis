# Technical Guide

**Explanation.** How the system works and why it is built this way.

This document deliberately contains **no row counts, no coverage dates, and no
artifact version strings**. Those are facts, they change constantly, and they are
generated from the live database and repository into `docs/reference/`:

| Fact | Generated sheet |
| --- | --- |
| Table row counts, asset spread, coverage ranges, per-indicator and per-field breakdowns | [`docs/reference/metrics.md`](docs/reference/metrics.md) |
| Active artifacts, registry rows, ensemble weights, on-disk artifact state | [`docs/reference/models.md`](docs/reference/models.md) |
| Management commands and their full option surface | [`docs/reference/commands.md`](docs/reference/commands.md) |
| Celery tasks, queues, routes, schedules, time limits | [`docs/reference/celery.md`](docs/reference/celery.md) |
| Environment variables, defaults, and `.env.example` gaps | [`docs/reference/env.md`](docs/reference/env.md) |

Regenerate with `python manage.py export_documentation_facts`. Verify with
`--check`.

Procedures live in `docs/how-to/`. This file explains the contracts those
procedures depend on.

---

## 1. The effective universe contract

One rule governs every cross-sectional calculation, training-sample filter,
backtest candidate pool, benchmark construction, and daily prediction:

```
date >= 2010-01-01   ->  CSI 500 (000905.SH)
```

The canonical implementation is `apps/markets/benchmarking.py`.

### Why it fails closed

When required point-in-time membership coverage is missing, workflows **raise**
rather than widening to all assets. The alternative was tried and is worse: a
silent fallback produces a cross-section computed over a different population
than the one inference will see, and nothing in the output reveals it. Ranking
percentiles, relative strength, and factor scores all become quietly
incomparable across dates, and the corruption propagates into training labels.

An incorrect universe is not a data-quality issue; it invalidates every
cross-sectional feature derived from it.

### Why membership is point-in-time

Index membership changes. Using *current* membership to compute *historical*
cross-sections is survivorship bias — it evaluates the past using only the names
that survived into the present. `IndexMembership` stores historical snapshots so
each date resolves the constituents that were actually in the index then;
`Asset.membership_tags` carries the current state for operational filtering.
Overlapping constituents are deduplicated at the asset row level.

### Why warm-up reaches before the floor

`HISTORICAL_DATA_FLOOR` bounds routine backfills. Two deliberate exceptions:

- **Reference data** — exchange calendars and suspension history are backfilled
  well before the floor because they are context, not features, and continuity
  reasoning needs them.
- **Feature warm-up** — indicators and `RS_SCORE` intentionally reach roughly 18
  months before the floor. Without it, every rolling metric would be null for its
  first window length after the floor, so the floor's first trading day would have
  no usable features.

`purge_pre_floor_historical_data` exists to stop future backfills from
re-expanding stale pre-floor history. It is dry-run by default.

---

## 2. Stored analytics vs runtime-computed features

Stored analytics rows serve **two** purposes, and this dual role is the reason
backfill completeness matters so much:

1. **Inspection surfaces** — the technical-indicator API, dashboards, admin.
2. **The shared model and runtime feature path** — heuristic, LightGBM, LSTM, and
   runtime backtests all read the same stored rows wherever a storage-backed input
   exists.

`RSI`, `MOM_*D`, `RS_SCORE`, and the stored OHLCV-derived model metrics
(`RETURN_*`, `RELATIVE_VOLUME_*`, `REALIZED_VOLATILITY_5D`) are read from storage
by all consumers. Stored `BBANDS` and `SMA` rows are consumed by the TP/SL helper
logic rather than being inspection-only. `FactorScore` is the stored dependency
for shared factor features.

**Implication:** backfill completeness for `analytics_technicalindicator`
directly affects runtime parity across prediction, training, and backtest paths.
Stale or missing rows degrade behaviour, but the null/default contracts remain
explicit and identical across every shared path — which is what makes the
degradation survivable rather than silent.

### Why backtests regenerate candidates

Stored prediction tables are the **live daily snapshot layer** only — they serve
the current stock-level prediction APIs and dashboard latest-row joins.

Backtests do **not** depend on them. Heuristic, LightGBM, and LSTM candidate
selection is generated at runtime in `apps/backtest/tasks.py`. Two reasons:

- Historical prediction coverage is patchy by nature; making backtests depend on
  it would silently restrict which windows can be tested.
- A backtest must use the model that is active *now* to be comparable with other
  backtests. Reading stored predictions would freeze whichever artifact happened
  to be deployed when the row was written.

Long windows resume through chunked execution state persisted in
`BacktestRun.report.runtime_state`. Legacy paginated prediction-history browsing
is not a supported backtest or monitoring dependency.

---

## 3. Technical freshness policy

### Why staleness is refused rather than tolerated

A stale row is worse than a missing row. A missing row falls back to a documented
neutral value that training and inference agree on. A stale row is consumed as if
it were current, so the model silently reasons about the wrong date. The guards
therefore refuse on both sides: backfill will not *write* a row whose trailing
window is incomplete, and runtime readers will not *use* a row that is too old.

### Trading days, not calendar days

The shared rule set is based on official open `ExchangeTradingCalendar` rows.
Never on raw calendar-day differences, and never on sparse OHLCV row counts —
which would make a period of suspended trading look like a period of missing data.

`ExchangeTradingCalendar.trade_date` stores TuShare `trade_cal.cal_date` and
`is_open` stores `trade_cal.is_open`. Open-day consumers filter on `is_open=True`.
TuShare `pretrade_date` is **not** persisted and is not used for continuity
decisions.

Authoritative constants: `apps/analytics/technical_staleness.py`.

### Gap-tolerant trend and smoothing metrics

These may span a bounded interior gap. The guard checks **both** the age of the
latest usable bar against the current official trading date **and** the maximum
trading-day gap inside the required trailing window.

| Metric family | Max gap (trading days) | Behaviour when violated |
| --- | --- | --- |
| `RSI(14)` | 3 | skip stored row; runtime falls back to `50` |
| `MACD(12,26,9)` | 3 | skip stored row |
| `BBANDS(20)` | 5 | skip stored row; runtime returns `None` |
| `STOCH(14,3,3)` | 5 | skip stored row |
| `ADX(14)` | 5 | skip stored row |
| `OBV` | 7 | skip stored row |
| `FIB_RET(60)` | 10 | skip stored row |
| `RS_SCORE` | 5 | see §3.1 |

Moving averages are bucketed by period:

| `timeperiod` | Max gap |
| --- | --- |
| ≤ 5 | 2 |
| ≤ 10 | 3 |
| ≤ 20 | 5 |
| ≤ 50 | 7 |
| ≤ 100 | 10 |
| > 100 | 15 |

An unlisted period uses the nearest bucket above it, so runtime `SMA(60)` in the
TP/SL logic inherits the ≤100 bucket and therefore a 10-trading-day max gap.

### Exact-window metrics

These have **zero** gap tolerance and additionally require an exact current
trading date plus an exact aligned anchor date. They never reuse a stale trailing
window.

| Metric | Max gap | Requirement | Neutral fallback |
| --- | --- | --- | --- |
| `RETURN_3D` | 0 | exact 3-trading-day anchor | `0` |
| `RETURN_5D` | 0 | exact 5-trading-day anchor | `0` |
| `RETURN_10D` | 0 | exact 10-trading-day anchor | `0` |
| `RELATIVE_VOLUME_5D` | 0 | exact window | `1.0` |
| `RELATIVE_VOLUME_20D` | 0 | exact window | `1.0` |
| `REALIZED_VOLATILITY_5D` | 0 | 6 aligned bars | `0` |
| `MOM_5D` | no entry → none | 6 bars | `0`; no stored row if incomplete |
| `MOM_10D` | no entry → none | 11 bars | `0`; no stored row if incomplete |
| `MOM_20D` | no entry → none | 21 bars | `0`; no stored row if incomplete |

`RELATIVE_VOLUME_*` are exact-window metrics in the code. An earlier revision of
this guide described them as inheriting the 5-day and 20-day moving-average gap
rules; that was wrong and is corrected here. Where this table and
`technical_staleness.py` disagree, the module wins.

### 3.1 Cross-sectional `RS_SCORE`

`RS_SCORE` no longer accepts sparse row-order approximations.

- Daily sync and historical backfill require an exact **20-trading-day** anchor
  from the official exchange calendar (21 window points including the current
  date).
- An asset missing the current trading date, the exact anchor date, **or any
  interior session** inside that window is excluded from that day's ranking
  entirely. It gets no row, rather than an approximated one.
- Runtime consumption falls back to neutral `0.5` when the latest stored row is
  older than 5 trading days.
- Historical reruns **delete and rebuild** the requested `RS_SCORE` /
  `HIGH_RS_SCORE` slice, so newly invalid windows remove previously stored stale
  rows. A rerun can therefore legitimately reduce the row count.

The usual causes of a cross-sectional miss are full-day suspensions inside the
required window and new listings still inside their 20-day warm-up — not
outside-universe leakage.

### 3.2 Signal-event suppression

Signal tasks do not emit stale technical events just because an old OHLCV row
exists. MA, Bollinger, volume, momentum, and reversal signals all require fresh
enough underlying windows; if the window is stale or incomplete the signal row is
**skipped** rather than reusing old state. This keeps `SignalEvent` behaviour
aligned with the stored-indicator and runtime-feature guards.

Historical non-RS signal rows are rebuilt with `backfill_signal_events`.
`HIGH_RS_SCORE` remains owned by `backfill_model_data`.

---

## 4. Macro snapshot semantics

`MacroSnapshot` stores **monthly** rows normalised to the first day of each month,
populated from the first available trade date in that month. Coverage is not
uniform across fields because upstream exposes different historical floors per
series — measured coverage is in
[`docs/reference/metrics.md`](docs/reference/metrics.md).

| Field | Upstream source | Stored semantics | Consumed by |
| --- | --- | --- | --- |
| `dxy` | TuShare `fx_daily`, `USDOLLAR.FXCM` then `USDOLLAR` | Month-start normalisation of the index close | Storage and API only |
| `cny_usd` | TuShare `fx_daily`, `USDCNH.FXCM` | Inverted `CNY/USD` from the offshore `USD/CNH` quote | Storage and API only |
| `cn6m_yield` … `cn30y_yield` | ChinaBond CSV for the earliest years, then TuShare `yc_cb` with `curve_term in [0.5, 1, 3, 5, 7, 10, 30]` and `curve_type=0` | Month-start rows from the first available trade date | `cn10y_yield − cn3y_yield` drives the yield-curve feature and regime inference; the full tenor surface is stored for inspection |
| `pmi_manufacturing` | TuShare `cn_pmi`, field `PMI010000` | Manufacturing PMI | Regime inference and macro features |
| `pmi_non_manufacturing` | TuShare `cn_pmi`, field `PMI020100` | Non-manufacturing business activity PMI | Regime inference and macro features |
| `cpi_yoy` | TuShare `cn_cpi` | CPI year-over-year growth | Regime inference |
| `ppi_yoy` | TuShare `cn_ppi` | PPI year-over-year growth | Storage and API only |

Notes on the semantics that are easy to get wrong:

- `pmi_non_manufacturing` maps to `PMI020100`. It previously and incorrectly
  followed the composite PMI field `PMI030000`.
- TuShare `cn_pmi` returns `MONTH` uppercase by default. Ingestion reads
  `MONTH`/`month` first and falls back to `CREATE_TIME` only if needed.
- `curve_type=0` means the maturity curve. Other curve types are not ingested.
- The runtime yield spread is `cn10y_yield − cn3y_yield`. An older `10Y − 2Y`
  formulation was replaced when the fuller tenor surface became available.
- `MarketContext` is derived from `MacroSnapshot` history and is queryable by
  date. It is backfilled rather than computed only forward, so historical
  prediction and backtest runs resolve the context that was inferable *then*.

Macro snapshots currently carry no per-field release date. As-of resolution
therefore points at the month-start row, which is an approximation of true
publication availability. Tracked in `BACKLOG.md`.

---

## 5. Models and formulas

### 5.1 Heuristic probability model

Primary code: `apps/prediction/tasks.py` (`_feature_snapshot`,
`_probabilities_from_features`).

**Inputs.** `factor_composite`, `factor_bottom_prob`, `sentiment_score`
(`ASSET_7D`), `rsi`, `mom_5d`, `rs_score`.

**Core formula.** Base prior `0.33`, with signals:

```
sentiment_signal = sentiment_score * 0.25
momentum_signal  = mom_5d * 1.2
rs_signal        = (rs_score - 0.5) * 0.4
factor_signal    = (factor_bottom_prob - 0.5)
```

Horizon scale: 3d `1.10`, 7d `1.00`, 30d `0.85`. Unclamped:

```
up   = base + (momentum_signal + sentiment_signal + rs_signal - factor_signal) * horizon_scale
down = base + (factor_signal - sentiment_signal - momentum_signal - rs_signal) * horizon_scale
flat = 1 - up - down
```

**Macro adjustments.** `RECESSION`: `down += 0.04`, `up -= 0.02`. `RECOVERY`:
`up += 0.03`, `down -= 0.02`.

**As-of resolution.** `_resolve_context()` takes `target_date` and resolves
`MarketContext` by `starts_at`/`ends_at`. Historical replay must not read the
latest active context — that would leak the future into a past prediction.

**Feature lookups.** Every input resolves as the most recent row with
`date <= as_of`, never a same-day-only lookup, then passes the freshness guard.

### 5.2 Factor scoring (bottom-candidate engine)

Primary code: `apps/factors/tasks.py` (`calculate_factor_scores_for_date`,
`_technical_reversal_score`).

The daily scorer writes one `FactorScore(mode=COMPOSITE)` row per asset per date.

**Component scores.**

- *Fundamental*: average of `pe_score`, `pb_score`, `roe_trend`.
- *Capital flow*: average of `main_force_flow_score` and `margin_flow_score`.
  Asset-level northbound fields were physically removed because northbound flow is
  not a valid per-stock field — it is a market-level series.
- *Sentiment*: mapped from `[-1, 1]` to `[0, 1]`.

**Technical reversal score** (`technical_score`) is intentionally a
**bottom-fishing reversal** signal, not a general trend-strength score. In the
daily composite scorer, `technical_score` is exactly the same value as
`technical_reversal_score`.

Input lookups use the latest available rows **on or before** `as_of`, not strictly
same-day rows: `latest_rsi(asset_id, as_of, default=50)`,
`latest_bbands(asset_id, as_of)` lower band, `latest_ohlcv(asset_id, as_of)` close
and recent volume, and any `SignalEvent(signal_type=OVERSOLD_COMBINATION)` on or
before `as_of`.

Score blocks:

| Block | Condition | Added |
| --- | --- | --- |
| RSI oversold | `RSI <= 35` | `0.35` |
| Lower-band proximity | `close <= lower_band * 1.03` | `0.25` |
| Confirmed oversold reversal | an `OVERSOLD_COMBINATION` signal exists on or before `as_of`, **or** the fallback block below passes | `0.40` |

The fallback confirmation block requires all of: `RSI < 30`,
`close <= lower_band * 1.02`, and latest volume below `80%` of the average volume
over the prior 20 sessions.

```
technical_score = min(rsi_block + lower_band_block + reversal_block, 1.0)
```

Missing-data behaviour: missing RSI falls back to `50`, which disables the
RSI-driven blocks; missing BBANDS or latest OHLCV disables the price-vs-band
checks; fewer than 21 OHLCV rows disables the fallback volume-confirmed reversal
block.

Interpretation: near `0.00` no oversold evidence; around `0.35` an RSI-only
setup; around `0.60` oversold plus lower-band proximity; `1.00` the strongest
confirmed reversal after capping.

**Not included today:** stored MACD, ADX, OBV, SMA/EMA, and RS score analytics do
not feed `technical_score`. That is a deliberate scoping decision, not an
oversight — the component measures reversal, not trend. Revisit via `BACKLOG.md`.

**Composite weights** (before normalisation): financial `0.4`, capital flow
`0.3`, technical `0.3`, sentiment `0.0`.

```
composite = financial*w_f + capital_flow*w_c + technical*w_t + sentiment*w_s
bottom_probability_score = clamp(composite, 0, 1)
```

Sentiment carries zero weight, so `FactorScore.sentiment_score` is stored for
inspection and completeness but does not move the composite.

### 5.3 LightGBM multi-class model

Primary code: `apps/prediction/tasks_lightgbm.py` (`_extract_features_for_asset`,
`_create_feature_matrix`, `_create_labels_for_training`, `train_lightgbm_models`,
`_predict_with_lightgbm`).

**Labels.** For each horizon (3, 7, 30 days): `UP` if forward return ≥ `+2%`,
`DOWN` if ≤ `−2%`, `FLAT` otherwise. The label builder uses the **first available
trading day on or after** `target_date + horizon_days`, not a fixed calendar-row
offset — so suspensions and holidays shift the label date rather than corrupting it.

**Training flow.**

1. Build the feature matrix for the training window.
2. Build labels per horizon.
3. Align labels to feature rows.
4. Prune to a compact core feature set from the latest active
   `FeatureImportanceSnapshot` per horizon (when pruning is enabled).
5. Standardise with `StandardScaler`.
6. Train the multiclass booster.
7. Calibrate probabilities (`sigmoid` for sklearn-style estimators when
   available; `identity` fallback for the native booster).
8. Save artifact files, registry rows, and feature-importance snapshots.
9. Refresh ensemble weights from the latest metrics.

**Hyperparameter defaults.** `objective=multiclass`, `num_class=3`,
`num_leaves=15`, `learning_rate=0.05`, `feature_fraction=0.6`,
`bagging_fraction=0.8`, `bagging_freq=5`, `lambda_l1=1.0`, `lambda_l2=1.0`,
`min_data_in_leaf=50`, `random_state=42`.

These are deliberately conservative — shallow leaves, substantial L1/L2, a high
minimum leaf population, and heavy column/row subsampling. The feature surface is
noisy and the signal is weak; regularisation matters more than capacity.

`northbound_flow` and `northbound_flow_x_mom_5d` persist in the engineered feature
set as neutral `0.5` compatibility placeholders. They are no longer stored on
`FactorScore`, exposed in the dashboard DTO, or stored in per-stock capital-flow
snapshots. Whether a given artifact retains or prunes them depends on its pruning
rule — read the generated models sheet rather than assuming.

### 5.4 LSTM model

Primary code: `apps/prediction/tasks_lstm.py` (`train_lstm_models`),
`apps/prediction/management/commands/rebuild_lstm_pipeline.py`.

**Structure.** 2-layer LSTM (`hidden_size=64`, `dropout=0.2`) plus an MLP
classifier head.

**Inputs.** The same shared feature extraction pipeline used by LightGBM,
converted into rolling sequences (default length 20). Each base feature is
expanded with an `__is_missing` mask column, which is why the LSTM feature count
is roughly double the LightGBM count.

**Labels.** Identical direction labels to LightGBM (`UP`/`FLAT`/`DOWN` at ±2% by
horizon).

**Optimisation.** `Adam(lr=1e-3)` with cross-entropy, a temporal 80/20
train/validation split, and best-validation checkpoint selection. The split is
temporal, never random — a random split leaks future information into training
through adjacent samples of the same asset.

**Memory controls.** Chunked feature extraction by asset, and a cap on sampled
sequences per horizon, keep peak memory bounded on long windows.

Artifacts persist as `3d_model.pt`, `7d_model.pt`, `30d_model.pt`, and
`summary.json` under `models/lstm/<version>/`, and the active `ModelVersion` row
is updated via `update_or_create`. Each successful retrain also refreshes ensemble
weights against the currently active LightGBM artifacts.

### 5.5 Ensemble weights

Primary code: `apps/prediction/tasks_lightgbm.py` (`_refresh_ensemble_weights`).

- Basis window: the last 60 days.
- Weights proportional to model accuracies (LightGBM, LSTM, heuristic), quantised
  to 4 decimals.
- Fallback when no usable accuracy exists: approximately equal weights
  (`0.3333 / 0.3333 / 0.3334`).

Two registry nuances that cause confusion:

- The **active ensemble `ModelVersion`** is dated to the retrain window end and
  carries the latest retrain metrics.
- The **latest `EnsembleWeightSnapshot`** is a chronological monitoring row dated
  to when it was computed, reflecting the 60-day basis available *then*.

They are different objects and will usually disagree. Ensemble weights are
monitoring and reporting only — backtest ranking does not consume them.

A snapshot inherits whatever accuracy its basis window contained. If it was
computed while a leaked artifact was active, its weights encode that artifact's
inflated accuracy. Always read the basis columns in the generated sheet before
trusting a snapshot.

### 5.6 Trade decision model

Primary code: `apps/prediction/odds.py` (`estimate_trade_decision`).

**Outputs persisted on predictions:** `target_price`, `stop_loss_price`,
`risk_reward_ratio`, `trade_score`, `suggested`.

**Resolution.** The function first resolves the latest OHLCV bar on or before
`as_of`. Missing OHLCV or a non-positive close returns null `target_price`,
`stop_loss_price`, `risk_reward_ratio`, and `trade_score`, with
`suggested=false`.

**Price context.** Up to the latest 60 OHLCV rows are scanned. Missing or invalid
high/low, Bollinger, or SMA values are converted to `close`, which makes them
neutral — they are then filtered out unless strictly above or below the close.

`resistance_candidates` (values strictly **above** close):

| Candidate | Fallback when unavailable |
| --- | --- |
| `max(highs_20)` — highest high in the last 20 rows | `close` |
| `max(highs_60)` — highest high in the last 60 rows | `close` |
| `upper_band` — latest Bollinger upper band on or before `as_of` | `close` |
| `_round_price_ceiling(close * 1.01)` | disabled by policy flag |

`support_candidates` (values strictly **below** close):

| Candidate | Fallback when unavailable |
| --- | --- |
| `min(lows_20)` | `close` |
| `min(lows_60)` | `close` |
| `lower_band` — latest Bollinger lower band on or before `as_of` | `close` |
| `moving_average_support` — latest `SMA60`, else `SMA50` | `close` |

```
target_price     = min(resistance_candidates)   # nearest valid resistance above close
stop_loss_price  = max(support_candidates)      # nearest valid support below close
```

Target and stop fall back **independently**: if `resistance_candidates` is empty,
target uses `close * (1 + upside_fallback)`; if `support_candidates` is empty,
stop uses `close * (1 - downside_fallback)`.

The internal price ladder rounds the near-target up by `0.5` below `10`, `1` below
`50`, `2` below `100`, `5` below `500`, and `10` at `500` or above — matching how
A-share prices actually cluster.

Fallback assumptions by horizon: 3d `+3% / −2%`, 7d `+6% / −4%`, 30d `+12% / −8%`,
other horizons `+5% / −3%`.

**Quantisation.** Prices to `0.0001`; ratios and scores to `0.000001`. Minimum
reward/risk floor is `0.5%` of close, so a degenerate target or stop at the close
cannot produce an infinite or zero ratio.

**Decision formulas.**

```
reward            = max(target_price - close, close * 0.005)
risk              = max(close - stop_loss_price, close * 0.005)
risk_reward_ratio = reward / risk                          when risk > 0
down_risk         = max(0.05, 1 - up_probability)
trade_score       = (up_probability * reward) / (down_risk * risk)   when risk > 0
```

`down_risk` is floored at `0.05` so a near-certain prediction cannot inflate
`trade_score` without bound.

**Suggested** requires all three: predicted label `UP`, `risk_reward_ratio >= 1.5`,
`trade_score >= 1.0`.

Runtime prediction payloads from heuristic, LightGBM, and LSTM candidates carry the
same trade-decision fields when available. During backtest entry,
`_backfill_prediction_trade_decision` fills missing `trade_score`, `target_price`,
`stop_loss_price`, and `suggested` when the payload still has a prediction source,
horizon, up probability, and predicted label.

**Backtest-only policy overrides**, supplied in
`BacktestRun.parameters.trade_decision_policy` without changing default prediction
behaviour:

| Key | Type | Effect |
| --- | --- | --- |
| `include_near_round_target` | bool, default `true` | When `false`, omits the rounded `close * 1.01` resistance candidate |
| `min_target_return_pct` | decimal ratio | Enforces `target_price >= close * (1 + value)` after structural/fallback selection |
| `min_stop_distance_pct` | decimal ratio | Enforces `stop_loss_price <= close * (1 - value)` when structural support is closer |

These exist so an experiment can test whether the structural levels or a
return-based floor produces better exits, without changing what the live
prediction APIs report.

---

## 6. Model lifecycle and artifact governance

### 6.1 Which registry table is authoritative

| Table | Authority over | Granularity |
| --- | --- | --- |
| `LightGBMModelArtifact` | **LightGBM deployment** — what inference and backtests actually load | One row per `(horizon_days, version)`; `is_active` is **per horizon** |
| `ModelVersion` | Registry metadata and labelling for LSTM, ensemble, and historical LightGBM | One row per `(model_type, version)` |

To know which LightGBM model is deployed, read `LightGBMModelArtifact` filtered on
`is_active=True`. **Not** `ModelVersion(model_type=LIGHTGBM)`.

Why the split exists: `ModelVersion` is refreshed during training but does not
maintain one simultaneously active row per horizon. It is a labelling and history
surface. Per-horizon deployment state genuinely requires one active row per
horizon, which is a different constraint, so it lives in a table whose uniqueness
is `(horizon_days, version)`.

The practical failure mode this prevents: reading `ModelVersion` and concluding a
different generation is deployed than the one inference loads.

### 6.2 Version tags

```
lgb-{horizon}d-{training_end_date}[-{version_tag}]
lstm-{training_end_date}[-{version_tag}]
```

Because `(horizon_days, version)` is unique, **retraining against the same cutoff
date requires a distinct tag** or the run collides with the existing family. This
is deliberate: it makes every retrain produce a new, addressable, retained family
rather than overwriting the previous one. Overwriting would make rollback
impossible and would destroy the audit trail for why a metric changed.

Tags are free-form labels — nothing in code enforces a vocabulary. The convention
in practice is that a tag names **what changed**, since the date is already in the
version string. The generated models sheet records what each family actually
contains (feature count, pruning rule, missing-value strategy, accuracy, window),
which is the authoritative description of any tag.

### 6.3 The missing-value contract

Artifacts record how they expect gaps to be handled at inference:

| `missing_value_strategy` | Meaning | Where recorded |
| --- | --- | --- |
| `native_nan` | LightGBM consumes real `NaN`; no neutral fill | LightGBM artifact metadata |
| `mask_and_zero_impute` | Build `__is_missing` masks, then zero-fill after scaling | LSTM summary |
| key absent | Inference falls back to the legacy neutral-fill path | Older artifacts |

**An artifact without the key does not fail — it silently degrades** to legacy
neutral fills (`0`, `1`, `50`, or `0.5` depending on the feature). Training and
inference then disagree about what a gap means, and nothing logs it. This is a
correctness problem that presents as a working model, which is why the generated
models sheet prints `ABSENT` explicitly rather than leaving the column blank.

The code paths support the newer contract in both directions: LightGBM training
builds the matrix under `native_nan` and inference reads the strategy from artifact
metadata; LSTM training uses a raw-NaN matrix, expands `__is_missing` masks, and
zero-fills after scaling, while inference builds masks for missing columns rather
than aborting. Code support and deployed state are separate questions — an old
active artifact will still use the legacy path.

### 6.4 Feature pruning

| Mode | Recorded `pruning.rule` | Retained |
| --- | --- | --- |
| Default | `none` | The full engineered feature set |
| Snapshot pruning | `latest_snapshot_cumulative_80_core20_25` | The prefix reaching 80% cumulative importance, floored at 20 and capped at 25 |

Pruning reads the **latest active `FeatureImportanceSnapshot`** per horizon. With
no snapshot, pruning is skipped and the full set is retained — so the first
training run of a new feature family cannot be pruned meaningfully. It produces the
snapshots the next run consumes. That two-step dependency is intentional: pruning
against importance measured on a *different* feature set would discard new features
before they are ever evaluated.

Artifact metadata records the pruning source, the target cumulative importance, the
retain floor and cap, the source feature count, the threshold keep-counts, the
retained and ranked feature lists, the pruned list, and any features missing from
the source snapshot. That is the audit trail for why a feature is or is not in a
deployed model.

### 6.5 Artifact path portability

Stored `artifact_path` values are **absolute paths written on the host that
trained the artifact**. Moving the repository, switching hosts, or migrating out of
Docker invalidates them.

A model that loads by version lookup but cannot find its file fails at
**inference** time, not at promotion time — the worst place to discover it. The
generated models sheet reports whether each stored path resolves on the current
host, so path resolution should be checked as part of any promotion.

Long-term this should be repo-relative paths resolved against `BASE_DIR`. Tracked
in `BACKLOG.md`.

### 6.6 Plausible accuracy bounds

Directional accuracy for this problem sits in a narrow band, roughly **0.42–0.58**
across horizons. Anything materially above that is **leakage until proven
otherwise**, not skill.

The registry has contained families with 3-day accuracy near 0.95, and those
inflated numbers propagated into an ensemble-weight snapshot through the 60-day
basis window. Treat an implausible accuracy as a defect in the feature pipeline
and never promote on it. The procedure is in
[`docs/how-to/retrain.md`](docs/how-to/retrain.md).

---

## 7. Backtest engine semantics

Primary code: `apps/backtest/tasks.py::run_backtest`.
UI: `frontend/src/pages/BacktestWorkbenchPage.tsx`.
API: `POST /api/v1/backtest/`, `GET /api/v1/backtest/`,
`GET /api/v1/backtest/{id}/trades/`.

### 7.1 Runtime behaviour

1. Create a run from the UI form (`strategy_type=PREDICTION_THRESHOLD`).
2. Backend validates the parameter set and queues an asynchronous job on the
   `backtest` queue.
3. The job iterates trading dates, **closing positions first**, then opening
   positions on eligible entry days. Close-before-open matters: it frees capital
   and position slots within the same session rather than a day later.
4. Sell exits are either a scheduled-hold exit or a TP/SL early exit.
5. Long runs are processed in chunks of `BACKTEST_CHUNK_TRADING_DAYS` (20) and
   resume through `BacktestRun.report.runtime_state` until complete. Chunking is
   what lets a multi-year run survive a worker restart or a soft time limit.
6. The final report writes the equity curve, the benchmark curve, and strategy
   metadata.

Candidates are generated at runtime rather than read from stored predictions — see
§2.

### 7.2 Runner options

| Option | Parameter | Semantics |
| --- | --- | --- |
| Run mode | UI-only | Single creates one run; rolling batch creates multiple sliding windows |
| Prediction source | `prediction_source` | `heuristic`, `lightgbm`, or `lstm`. The UI `all-models` choice submits parallel runs for all three |
| Date bounds | `start_date`, `end_date` | Run window |
| Forecast horizon | `horizon_days` | `{3, 7, 30}` |
| Selections per entry | `top_n` | Candidate count in top-N mode |
| Minimum up probability | `up_threshold` | Filter applied before ranking |
| Candidate mode | `candidate_mode` | `top_n` or `trade_score` |
| Maximum open positions | `max_positions` | Cap after ranking/filtering |
| Trade score source | `trade_score_scope` | `independent` (selected model only) or `combined` (heuristic + LightGBM averaged) |
| Minimum trade score | `trade_score_threshold` | Threshold in `trade_score` mode |
| Planned holding days | `holding_period_days` | Scheduled hold before normal sell |
| Capital per entry | `capital_fraction_per_entry` | Fraction of initial capital deployable per entry cycle |
| Slippage | `slippage_bps` | Per-trade slippage in basis points |
| Starting capital | `initial_capital` | Portfolio initial cash |
| Macro-aware ranking | `use_macro_context` | Applies the macro-phase multiplier to ranking and writes a monthly macro report |
| TP/SL early exit | `enable_stop_target_exit` | Enables early sell before the scheduled exit date |
| Entry weekdays | `entry_weekdays` | Allowed opening weekdays (`MON`…`FRI`) |
| Legacy flat fee | `fee_rate` | Optional symmetric override; cannot be combined with the structured fee parameters |

Current UI defaults live in `BacktestWorkbenchPage.tsx` and are deliberately not
duplicated here — they drift. Backend fallbacks when parameters are omitted differ
from the UI defaults and are visible in `apps/backtest/tasks.py`.

### 7.3 Candidate modes

- **`top_n`** — uses the selected prediction source and ranks by `up_probability`.
- **`trade_score`** — `independent` uses the selected model's predictions filtered
  by `trade_score_threshold`; `combined` merges heuristic and LightGBM per asset and
  averages trade score and up probability.

`trade_score` mode enforces `max_positions` as the concurrent-position cap. In
`top_n` mode candidate count is controlled by `top_n`, and the backend
`max_positions` fallback is effectively unlimited unless trade-score mode is
active.

### 7.4 Exit logic

**Scheduled exit:** the first trading day after the `holding_period_days` target
date.

**Early exit** (when enabled) uses the **raw daily close** from the price map, not
the slippage-adjusted fill price — the trigger is a market condition, the fill is a
transaction. Order of checks:

1. `close <= stop_loss_price` → exit with `exit_reason=STOP_LOSS`.
2. Otherwise `close >= target_price` → exit with `exit_reason=TARGET_PRICE`.
3. Otherwise, if the scheduled exit date has arrived → `exit_reason=SCHEDULED`.
4. Otherwise the position remains open.

Stop-loss is checked before target. On a day where both would trigger, the
conservative exit wins — which is the correct assumption when only a daily close is
available and the intraday path is unknown.

If the sell close is missing or non-positive, the position **remains open** and the
scheduled exit is retried on the next later date with a valid close. A position is
never force-closed at a fabricated price.

### 7.5 Cost and position mechanics

```
buy_fill  = close + (close * slippage_bps / 10000)
sell_fill = close - (close * slippage_bps / 10000)
```

Default CN A-share fee schedule:

```
buy_fee  = max(buy_amount  * 0.1‰, 5) + buy_amount  * (0.0341‰ + 0.02‰ + 0.01‰)
sell_fee = max(sell_amount * 0.1‰, 5) + sell_amount * (0.0341‰ + 0.02‰ + 0.01‰ + 0.5‰)
```

Commission at `0.1‰` with a ¥5 minimum on both sides; transaction handling
`0.0341‰`; regulatory `0.02‰`; transfer `0.01‰`; **stamp duty `0.5‰` on sells
only**. Asymmetry matters — stamp duty is a real cost of turnover and modelling it
symmetrically understates the penalty for short holding periods.

If `fee_rate` is supplied explicitly the engine falls back to the legacy symmetric
`amount * fee_rate` model on both sides. It cannot be combined with the structured
parameters.

```
realized_pnl = sell_amount - sell_fee - buy_amount - buy_fee
deployable_capital = min(cash, initial_capital * capital_fraction_per_entry)
per_candidate_budget = deployable_capital / selected_candidate_count
```

The buy amount is solved against that budget **after** the applicable buy-side fee
model, including the minimum-commission branch when it binds — so the budget is
respected net of fees rather than gross.

### 7.6 Report payload

The run report contains `equity_curve`, `benchmark.equity_curve`,
`prediction_source`, `candidate_mode`, `trade_score_scope`, `entry_weekdays`,
`holding_period_days`, `fee_model`, `enable_stop_target_exit`, and
`macro_context_monthly` when macro-aware ranking is enabled. Chunked runs also
carry `runtime_state`, which is resume bookkeeping rather than a result.

### 7.7 Metric definitions

```
total_return      = (final_value - initial_capital) / initial_capital
annualized_return = (1 + total_return) ** (365 / calendar_days) - 1     when total_return > -100%
max_drawdown      = maximum peak-to-trough decline over the strategy equity curve
sharpe_ratio      = annualised from daily equity-curve returns, 252 trading days,
                    population standard deviation
total_trades      = closed positions, not raw buy/sell rows
winning_trades    = closed positions with positive realised PnL
win_rate          = winning_trades / total_trades, or 0 when nothing closed
```

Annualisation uses **calendar** days for the return and **252 trading** days for
the Sharpe. Sharpe uses the population standard deviation, not the sample one.
`total_trades` counts round trips, so it is not comparable to a raw trade-row count.

### 7.8 Inline matrix execution

`run_core_backtest_matrix --execute-inline` runs a whole matrix in the command
process rather than through the queue. It buckets runs by `horizon_days` in
first-seen order and drains each bucket round-robin, so runs sharing a horizon
reuse the same cached signal surfaces instead of evicting each other. The
trading-date, price-map, and matrix-signal caches are cleared on entry, between
horizon groups, and in a `finally` block, so an interrupted matrix cannot leak
state into the next one.

Continuations are detected from **persisted run state** — a run still `RUNNING`
after a chunk is re-queued — rather than from an intercepted `delay()` call. That
makes the inline path resume the same way the queued path does.

### 7.9 Exports

`export_backtest_runs` defaults to a light export: `run_summary.csv`,
`run_config_results.csv`, `model_references.csv`.

`--detail-export` additionally writes `trades.csv`, `macro_context_monthly.csv`,
and comparison CSVs. `--include-active-lightgbm-artifacts` additionally writes
`lightgbm_model_artifacts.csv` with active artifact pruning metadata.

`trades.csv` includes signal payload fields — `trade_score`, `target_price`,
`stop_loss_price`, `suggested`, `model_version`, `model_version_id`,
`model_artifact_id` — when detail export is enabled.

Model references are collected from executed trades' `signal_payload`. A run with
zero trades therefore reports zero model references even though it did resolve
models; that is a reporting blind spot, not evidence that no model was used.
Tracked in `BACKLOG.md`.

Exports are generated local output under `reports/`, which is gitignored — they
are not committed source files.
