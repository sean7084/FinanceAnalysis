# Runbook: provider rate limits and data blackouts

For missing data caused by the upstream source rather than by this system.
Distinguishing "the provider never had it" from "we failed to fetch it" is the
whole job here — the remediation is completely different.

---

## 1. Providers

| Provider | Role | Token |
| --- | --- | --- |
| TuShare | Primary for market data, fundamentals, capital flow, macro, news | `TUSHARE_TOKEN` |
| AkShare | Fallback, and primary for some news providers | none |

Provider selection for macro is configurable:

| Variable | Default | Effect |
| --- | --- | --- |
| `MACRO_SYNC_PRIMARY_PROVIDER` | `tushare` | Tried first |
| `MACRO_SYNC_FALLBACK_PROVIDER` | `akshare` | Tried when the primary fails or returns empty |
| `MACRO_SYNC_PROVIDER_SLEEP_SECONDS` | `0.2` | Pause between provider calls |

Full inventory: [`../reference/env.md`](../reference/env.md).

---

## 2. Recognise the failure mode

| Symptom | Likely cause | Section |
| --- | --- | --- |
| `抱歉，您每分钟最多访问该接口N次` / points-exhausted error | TuShare per-minute quota | §3 |
| Empty dataframe, no exception, for a date range | Historical blackout | §4 |
| `NaN`/null in one field but not its siblings on the same row | Field-level blackout | §4 |
| Timeout or connection reset | Transient network | §3 |
| Whole provider raises on every call | Token invalid or expired | §3 |
| Data present but stale by a few days | Provider publication lag | §5 |

---

## 3. Rate limiting

TuShare quotas are **per minute and per account tier**, not per day. The macro
yield backfill is the heaviest consumer and is already tuned around this:

| Variable | Default | Meaning |
| --- | --- | --- |
| `MACRO_YIELD_BACKFILL_CALL_SLEEP_SECONDS` | `31.0` | Pause between successful calls |
| `MACRO_YIELD_BACKFILL_RETRY_SLEEP_SECONDS` | `65.0` | Pause before retrying a throttled call |
| `MACRO_YIELD_BACKFILL_MAX_RETRIES` | `3` | Retries per window |
| `MACRO_YIELD_BACKFILL_WINDOW_MONTHS` | `36` | Months per request window |

A 31-second inter-call pause and a 65-second retry pause are not arbitrary — they
are sized to clear a one-minute quota window. **Do not reduce them to make a
backfill faster.** The result is not a faster backfill; it is a backfill that
fails halfway and leaves partial coverage that looks like a blackout.

If you are being throttled:

1. **Stop and wait.** Quotas reset per minute; hammering extends the problem.
2. **Narrow the range.** Backfill in smaller windows rather than retrying the
   full span.
3. **Use checkpointing.** `backfill_model_data`,
   `backfill_technical_indicators`, and `backfill_signal_events` accept
   `--checkpoint-file` / `--resume-from-checkpoint`. Provider-bound backfills
   should always use them. See [`backfill.md`](backfill.md) §5.
4. **Reduce concurrency to one.** Running two provider-bound backfills in
   parallel shares one quota and doubles the throttle rate.
5. **Check the token.** `TUSHARE_TOKEN` unset or expired produces auth errors
   that look like throttling. `scripts/verify_local_stack.sh` does not validate
   the token, so test it explicitly:

   ```bash
   python manage.py check_earliest_data
   ```

`backfill_news` has its own throttling handles — `--sleep-seconds`,
`--max-retries`, `--chunk-days`, `--limit-per-provider`. Use `--dry-run` first to
confirm the plan before spending quota on it.

---

## 4. Historical blackouts

A blackout is a period where the provider **has no rows at all**, or has rows
with the field null. It is not an error and cannot be retried into existence.

### Known blackouts in this dataset

| Series | Coverage | Cause |
| --- | --- | --- |
| `margin_balance_change_5d` | Roughly three quarters of capital-flow rows; starts later than its siblings | Raw `margin_detail` history begins later, plus mid-history and trailing TuShare blackout windows |
| `main_force_net_5d` | Starts materially later than OHLCV | Raw moneyflow history floor |
| `pe_ttm` | A small number of assets have null rows despite a same-day `daily_basic` record | TuShare `daily_basic.pe_ttm` null/blackout behaviour |
| `dxy` | Starts later than other macro fields | TuShare `fx_daily` history floor |
| `cny_usd` | Starts later still | Same |
| `cn3y_yield` … `cn30y_yield` | A few rows fewer than `cn6m_yield` | Some tenors published later |

Current measured coverage is generated in
[`../reference/metrics.md`](../reference/metrics.md) — the "Field-level non-null
coverage" section reports non-null counts, min/max, and the non-null date range
for exactly these tables. Read it before assuming a gap is new.

### Classifying a gap correctly

`validate_data_quality` separates causes that look identical in the data:

| Finding | Meaning | Action |
| --- | --- | --- |
| `missing_margin_detail_source_row` | The raw upstream row does not exist | None — structural. Document and move on |
| `margin_diff_5_warmup_insufficient` | Fewer than 5 prior sessions available to compute the difference | None — expected at series start and after new listings |
| Excused OHLCV gap | Pre-listing, on/after delist, or suspension-covered | None |
| Suspicious gap | A trading day inside the listing window with no row | Investigate — this is a real fetch failure |

Conflating the first two is the most common misdiagnosis: both present as a null
`margin_balance_change_5d`, and only one is fixable.

Run the classification rather than eyeballing nulls:

```bash
python manage.py validate_data_quality \
  --start-date <date> --end-date <date> \
  --effective-universe-only --include-delisted \
  --output-dir reports/ops_logs/blackout_<date>

python manage.py audit_model_data_quality \
  --start-date <date> --end-date <date> --sample-size 50
```

---

## 5. Publication lag

Providers publish on their own schedule, and this system aligns to it:

| Series | Cadence | Normalisation |
| --- | --- | --- |
| Macro (PMI, CPI, PPI, yields, FX) | Monthly | Stored on the **first day of the month**, populated from the first available trade date in that month |
| Fundamentals (`fina_indicator`) | Quarterly | Aligned by **announcement date**, not report period — never uses a future filing |
| `daily_basic` (PE/PB/shares/market cap) | Daily | Same trade date |
| News | Continuous | Ingested hourly for backfill, daily at 16:35 UTC for the latest window |

Macro sync runs on days 2–8 of each month at 00:10 UTC precisely because
month-start publication is not instantaneous. A macro snapshot for the current
month being absent on the 1st is **expected**.

`validate_data_quality --macro-max-age-days` sets the tolerance before a macro
snapshot counts as stale. Raise it rather than "fixing" a snapshot that is simply
waiting on publication.

---

## 6. What the system does about missing values

Nothing silently breaks, because every consumer has an explicit fallback. This is
why a blackout degrades signal quality rather than causing errors:

| Missing input | Fallback | Effect |
| --- | --- | --- |
| `RS_SCORE` | neutral `0.5` | Weakens relative-strength signal |
| `RSI` | `50` | Disables RSI-driven factor blocks |
| `MOM_*D` | `0` | Neutral momentum |
| `relative_volume_*` | `1.0` | Neutral volume |
| `return_*`, `realized_volatility_5d` | `0` | Neutral |
| Factor percentile scores | `0.5` | Neutral valuation/flow context |
| Sentiment `ASSET_7D` | `0.0` | Removes the news-tone signal |
| `MarketContext` | recovery/neutral | Weakens macro-aware ranking |
| Macro yields | neutral spread | Weakens the yield-curve feature |

The exact per-field behaviour and its downstream impact is documented in
`TECHNICAL_GUIDE.md`. The point operationally: **a blackout does not stop
predictions.** It produces plausible-looking predictions with less information in
them. That is why coverage is measured and published rather than assumed.

Artifacts trained under the `native_nan` missing-value contract preserve real
gaps instead of neutral-filling them. Artifacts without that key fall back to
legacy neutral fills. See [`retrain.md`](retrain.md) §6.

---

## 7. Remediation decision table

| Situation | Do this |
| --- | --- |
| Throttled mid-backfill | Wait, narrow the range, resume from checkpoint (§3) |
| Gap classified as structural | Record it, adjust expectations, do not retry |
| Gap classified as suspicious | Re-run the owning backfill command for that window (§4) |
| Provider returned data for the wrong period | Check as-of alignment before blaming the provider — fundamentals align by announcement date |
| Whole series empty after a backfill | Token, network, or the wrong provider is primary (§1, §3) |
| Fallback provider disagrees with primary | Trust the primary for fields it owns; the fallback exists for availability, not accuracy |
| Coverage regressed after a repair | Re-run `export_documentation_facts` and diff — a shrinking count means the repair deleted more than it wrote |

---

## 8. After remediation

```bash
python manage.py export_documentation_facts --only metrics
git diff docs/reference/metrics.md
```

The diff is the evidence that the remediation worked. Commit it with the change
so the coverage history is traceable, and note the blackout window in
`CHANGELOG.md` if it is a new structural finding — the next person to see that
null should not have to rediscover why it is there.
