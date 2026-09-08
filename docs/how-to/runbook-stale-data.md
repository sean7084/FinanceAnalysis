# Runbook: stale or missing derived data

For when stored indicators, factor scores, or predictions are older than they
should be, or when a model output looks neutral in a way that suggests it is
reading nothing.

The authoritative policy lives in `apps/analytics/technical_staleness.py`. The
tables below are transcribed from that module — if they disagree with the code,
the code wins and this file is wrong.

---

## 1. Why staleness is enforced at all

Stored analytics serve two purposes: inspection surfaces **and** the shared
feature path for heuristic, LightGBM, LSTM, and runtime backtests. A stale row is
worse than a missing row, because a missing row falls back to a documented
neutral value while a stale row is silently consumed as if it were current.

So the guards **refuse to write** a row whose trailing window is incomplete, and
runtime readers **refuse to use** a row that is too old. Both sides fall back to
the same documented neutral, which keeps training and inference consistent.

Freshness is measured in **official trading days** from
`ExchangeTradingCalendar` rows with `is_open=True` — never in calendar days, and
never by counting sparse OHLCV rows. TuShare's `pretrade_date` is not persisted
and is not used for continuity decisions.

---

## 2. The two guard families

### Gap-tolerant metrics

Trend and smoothing metrics may span a bounded interior gap. The guard checks
**both** the age of the latest usable bar against the current official trading
date **and** the maximum trading-day gap inside the required trailing window.

From `BASE_MAX_GAP_TRADING_DAYS`:

| Indicator | Max gap (trading days) |
| --- | --- |
| `RSI` | 3 |
| `MACD` | 3 |
| `BBANDS` | 5 |
| `ADX` | 5 |
| `STOCH` | 5 |
| `OBV` | 7 |
| `FIB_RET` | 10 |
| `RS_SCORE` | 5 |

Moving averages are bucketed by period (`MOVING_AVERAGE_MAX_GAP_BUCKETS`):

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

These have **zero** gap tolerance and additionally require an exact aligned
anchor date. They never reuse a stale trailing window:

| Indicator | Max gap | Requirement |
| --- | --- | --- |
| `RETURN_3D` | 0 | Exact 3-trading-day anchor |
| `RETURN_5D` | 0 | Exact 5-trading-day anchor |
| `RETURN_10D` | 0 | Exact 10-trading-day anchor |
| `RELATIVE_VOLUME_5D` | 0 | Exact window |
| `RELATIVE_VOLUME_20D` | 0 | Exact window |
| `REALIZED_VOLATILITY_5D` | 0 | 6 aligned bars |
| `MOM_5D` / `MOM_10D` / `MOM_20D` | no entry → no tolerance | 6 / 11 / 21 bars respectively |

A `max_gap` of `0` means the metric is not written at all unless every session in
its window is present. A missing entry (the `MOM_*` family) resolves to `None`
from `technical_indicator_max_gap_trading_days`, which callers treat as "no gap
tolerance".

> **Known documentation defect.** `TECHNICAL_GUIDE.md` states that
> `relative_volume_5d` and `relative_volume_20d` inherit the 5-day and 20-day
> moving-average gap rules (2 and 5). The code sets both to `0`. The code is
> authoritative. Tracked in `BACKLOG.md`.

### Runtime fallbacks

| Metric | Neutral fallback when unusable |
| --- | --- |
| `RSI` | `50` |
| `MOM_*D` | `0` |
| `return_*`, `realized_volatility_5d` | `0` |
| `relative_volume_*` | `1.0` |
| `RS_SCORE` | `0.5` when the latest stored row is older than 5 trading days |
| `BBANDS` | `None` — disables price-vs-band checks |
| `SMA` | neutral/default |

---

## 3. Diagnose

### 3.1 What is the actual freshness?

```bash
python manage.py export_documentation_facts --only metrics
```

The `analytics_technicalindicator` breakdown lists every indicator type with its
row count, earliest date, and **latest date**. Compare the latest dates against
each other and against the OHLCV latest date.

Uneven latest dates across indicator types are the normal signature of a partial
backfill — different families were last run over different windows. It is a
maintenance signal, not a correctness bug, but it does mean some features are
older than others.

### 3.2 Is the guard refusing to write, or is the input missing?

Two very different causes produce the same absent row:

| Cause | How to tell | Fix |
| --- | --- | --- |
| OHLCV window incomplete or gapped | Guard computed and rejected | Repair OHLCV, then re-run the derived backfill |
| Derived backfill never covered that range | OHLCV is complete but no row exists | Run the owning backfill for that range |
| Asset suspended for the whole window | Suspension row covers the date | Correct behaviour — no fix needed |
| Asset not yet listed / already delisted | Listing dates bound the range | Correct behaviour |
| Outside the effective universe on that date | Membership history excludes it | Correct behaviour |

Check the inputs before blaming the guard:

```bash
python manage.py validate_data_quality \
  --start-date <date> --end-date <date> \
  --effective-universe-only --include-delisted \
  --output-dir reports/ops_logs/staleness_<date>
```

`validate_data_quality` classifies OHLCV gaps as excused (pre-listing,
on/after-delist, suspension-covered) or suspicious. Only suspicious gaps are
worth chasing.

### 3.3 `RS_SCORE` specifically

`RS_SCORE` is the strictest series in the system and the most common source of
"why is this neutral" questions.

- Requires an exact **20-trading-day** anchor from the official exchange
  calendar (21 window points including the current date).
- An asset missing the current trading date, the exact anchor date, **or any
  interior session** in that window is excluded from that day's ranking entirely
  — it gets no row, rather than an approximated one.
- Full-day suspensions inside the window are the usual cause of a cross-sectional
  miss. So is a new listing still inside its 20-day warm-up.
- Historical reruns **delete and rebuild** the requested `RS_SCORE` /
  `HIGH_RS_SCORE` slice, so newly invalid windows remove previously stored stale
  rows. A rerun can therefore legitimately reduce the row count.
- `RS_SCORE` is owned by `backfill_model_data`, **not**
  `backfill_technical_indicators`. Running the wrong command is the most common
  wasted hour here.

---

## 4. Remediate

### 4.1 Repair upstream first

Derived data cannot be fresher than its inputs. The dependency order is:

```
OHLCV + trading calendar + suspensions + listing dates
   └─> technical indicators, signals, market context
         └─> RS_SCORE, sentiment, factor scores   (backfill_model_data)
               └─> predictions
                     └─> backtests (regenerate candidates at runtime)
```

Repairing a downstream stage without repairing upstream reproduces the same gap.

### 4.2 Re-run the owning backfill

```bash
# OHLCV repair for a window
python manage.py backfill_ohlcv_history --start-date <date> --end-date <date>

# Non-RS indicators and stored model metrics
python manage.py backfill_technical_indicators \
  --start-date <date> --end-date <date> --chunk-size-days 120 \
  --checkpoint-file reports/ops_logs/ti_<date>.json

# RS_SCORE, sentiment, factor scores
python manage.py backfill_model_data \
  --start-date <date> --end-date <date> \
  --checkpoint-file reports/ops_logs/model_data_<date>.json

# Non-RS signal events
python manage.py backfill_signal_events \
  --start-date <date> --end-date <date> --chunk-size-days 120
```

Ownership table: [`backfill.md`](backfill.md) §4.

### 4.3 Re-trigger the daily tasks

Backfills write history; the daily tasks write the current date. After repairing,
re-run the daily path so today's row reflects the repaired inputs:

```bash
python manage.py shell -c "
from apps.analytics.tasks import calculate_indicators_for_all_assets
calculate_indicators_for_all_assets.delay()
"
```

Then re-run predictions for the affected dates if they were already generated
against stale inputs. See [`runbook-sync-failure.md`](runbook-sync-failure.md) §3.

### 4.4 Signal events

Signal tasks apply the same freshness rules and **skip** a signal rather than
emitting it from a stale window. So a missing signal on a date with a gapped
OHLCV window is correct behaviour, not a bug. Rebuild historical non-RS signals
with `backfill_signal_events` after repairing OHLCV; `HIGH_RS_SCORE` remains
owned by `backfill_model_data`.

---

## 5. When staleness reached the models

If stale stored features were consumed by inference before you repaired them:

1. Stored prediction rows for the affected dates are wrong and will not
   self-correct. Backtests **regenerate candidates at runtime**, so a backtest
   run after the repair is fine — but a backtest run *before* it is not.
2. Re-run predictions for the affected dates (§4.3).
3. Re-run any backtest whose window overlaps the stale period. Compare the
   metrics before and after; a material change tells you how much the staleness
   cost.
4. Training data is a separate question. If the stale period falls inside a
   training window, the artifact was fitted on degraded features and should be
   retrained — see [`retrain.md`](retrain.md).

---

## 6. Preventing recurrence

| Measure | How |
| --- | --- |
| Make coverage visible | `export_documentation_facts --only metrics` after every backfill; commit the diff |
| Gate releases on validation | `validate_data_quality --fail-on-critical` in any scripted release path |
| Alert on findings | `validate_data_quality --alert --alert-recipients <...>` |
| Keep the calendar authoritative | `backfill_trading_calendar` must cover the full range before any freshness reasoning is meaningful |
| Checkpoint long runs | A resumed run is a complete run; an abandoned one leaves exactly this class of gap |

The generated coverage sheet is the cheapest early-warning system available: a
`Latest` column that stops advancing is visible in a diff long before a model
output looks wrong.
