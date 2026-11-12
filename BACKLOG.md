# Backlog — index

This file is a **thin index**. It is deliberately not a work queue and not a notebook.

- **Open, actionable work** → GitHub issues (table below). Anything that needs a decision from
  the owner is marked as such.
- **Resolved work, measurement evidence, and decision records** → [`BACKLOG_ARCHIVE.md`](BACKLOG_ARCHIVE.md), verbatim.
- **What shipped** → [`CHANGELOG.md`](CHANGELOG.md).

The split is the point: an item used to stay open in prose long after it was fixed, and the file
grew to ~1,800 lines with finished work mixed into live work. Nothing was deleted in the 2026-11-12
restructure without either shipping into `CHANGELOG.md`, moving into an issue body with its evidence
intact, or being archived verbatim.

---

## Current state

Phases A–C (reliability and closure) are merged as PRs #129–#135: the NAS-Redis cache resilience,
the `ETFFeatureSnapshot` `NameError`, the freshness rule for immortal `RUNNING` rows, the dead
event-impact surface, the `--check` verdict ordering, and the BACKLOG triage itself. The frozen
`RUNNING` rows are now *detectable* (#131); the `--fail`/`--restart` verb that clears them is an
owner-gated production write, not code.

What remains is organised as three milestones. The ordering is a principle, not a preference:
**fix the measuring instrument before adding signal.** Much of what reads as "no edge" in the ETF
results is currently indistinguishable from "the instrument cannot resolve the edge".

- **M1 — measurement power** (Phase D): #136, #138–#143
- **M2 — model quality** (Phase E): #137, #144–#148
- **M3 — hygiene and micro-tasks**: #152–#156, #160 (#151 and #153 shipped/closed)
- **Owner decisions** — deployment hygiene, not diffs: #149, #150
- **Blocked on a data source** (not schedulable): #157–#159

Seven PRs came out of executing the milestones: [#163](https://github.com/sean7084/FinanceAnalysis/pull/163) (#143 schema faults now raise, mutation-checked), [#164](https://github.com/sean7084/FinanceAnalysis/pull/164) (#151 lazy-import rationale), [#165](https://github.com/sean7084/FinanceAnalysis/pull/165) (#138 per-asset bulk read — equivalence proven, timing claim narrowed), [#169](https://github.com/sean7084/FinanceAnalysis/pull/169) (chunk size becomes a setting, stacked on #165), [#166](https://github.com/sean7084/FinanceAnalysis/pull/166) (#155 entitlement-flag eviction), [#167](https://github.com/sean7084/FinanceAnalysis/pull/167) (#152 engine decision rules), [#168](https://github.com/sean7084/FinanceAnalysis/pull/168) (#153 orphaned i18n keys, closed). Three issues were found to describe work already shipped and were corrected rather than implemented — #142 (watchdog built, wired, observed), #156 (unit already in `help_text`), #161 (temp fixture already in place, then closed). A roadmap that schedules finished work is the defect this restructure exists to remove; it took executing the milestones to find three more instances of it.

---

## M1 — measurement power

| Issue | One line | Gate |
|---|---|---|
| [#136](https://github.com/sean7084/FinanceAnalysis/issues/136) | ETF instrument: block layout swings ~1 Sharpe on panel start alone; declare a hashed, pre-registered stopping rule | **blocks #137** |
| [#138](https://github.com/sean7084/FinanceAnalysis/issues/138) | DQ gate: bulk-read the per-asset phase — [PR #165](https://github.com/sean7084/FinanceAnalysis/pull/165), [PR #169](https://github.com/sean7084/FinanceAnalysis/pull/169) (sizing) + the timestamp-column removal. **4.4× win at 6 months** (149.0 → 34.1 s). Two-year penalty now **+10.6 s** (105.05 → 115.67 s), down from ~+23 s, after attributing it to one prefetched column no consumer read. Byte-identical everywhere. The residual is **not** in the reads: bulk reads 6.85 s *cheaper* than what they replaced, technical alone 84% of read time, and three further candidates measured and refuted (0.23 s / 0.88 s / 11.2 s total) | narrowing per-asset sessions to effective-universe membership — i.e. stop reading 9.26 M indicator rows; and note the floors: `default_bucket_scans` (shared code) swings 206.7–387.3 s, and a replay of the same shape moved 67.18 → 72.82 s, so only repeated in-process runs can resolve what is left |
| [#139](https://github.com/sean7084/FinanceAnalysis/issues/139) | Backtest engine: batch candidate-generation indicator IO (run 1468 hit its time limit) | not a wider `soft_time_limit` |
| [#140](https://github.com/sean7084/FinanceAnalysis/issues/140) | `list_date`: refresh cadence, class-B range backfill, and a policy for the 21 criticals the attribution left standing | gate re-run over one year + one stated policy |
| [#141](https://github.com/sean7084/FinanceAnalysis/issues/141) | Newest-session gap measured **closed** on both feeds (2026-11-12: OHLCV 499/session, ETFDaily 2,183) — what is left is a stock-feed scope question | decide the intended universe for `sync_daily_a_shares` |
| [#142](https://github.com/sean7084/FinanceAnalysis/issues/142) | DB-wedge watchdog — already shipped, wired, tested **and observed**: `scripts/db_wedge_watch.py` records 328 clean passes with zero terminations across a 90-minute full-range DQ run | one controlled true-positive reproduction remains |
| [#143](https://github.com/sean7084/FinanceAnalysis/issues/143) | `build_metrics`: retry link faults, **raise** schema faults — fix in [PR #163](https://github.com/sean7084/FinanceAnalysis/pull/163) | a simulated `ProgrammingError` must fail the run |

#138 and #139 are the same trap in two places — per-asset fetches, one round trip at a time.
The named-cursor problem was already removed by the `db_scans` rewrite, so the expectation was
query *count* — and on a short window that is what it was: the per-asset phase measured
**149.0 s of 165 s (90%)**, and the bulk read took it to **34.1 s** with every detail CSV
byte-identical. At two years the same change was *slower*, which is how the real term got
found: not query count but **per-row materialisation cost**, paid once per extra column per
row. An earlier full-range profile had attributed **47.8%** to per-asset scans and **44.6%**
to the universe/audit writes; both readings are true of their own window, and neither licenses
quoting a full-range speedup before measuring one (the warning is in `db_scans.py:71`). #138 is
a release gate, where the failure mode is a silent miss rather than a slow run — which is why
its acceptance was output equality at every window, not a timing.

## M2 — model quality

| Issue | One line | Gate |
|---|---|---|
| [#137](https://github.com/sean7084/FinanceAnalysis/issues/137) | ETF ML rematch + RULE-default re-arbitration under the new instrument | `blocked`, by #136 |
| [#144](https://github.com/sean7084/FinanceAnalysis/issues/144) | Stock 7d: measure the purged holdout t-stat, expose holdout provenance, audit the 36 features | the holdout t-stat is the verdict, not the 0.4874 |
| [#145](https://github.com/sean7084/FinanceAnalysis/issues/145) | Provenance fields on artifacts and backtest runs | two windows distinguishable from their rows alone |
| [#146](https://github.com/sean7084/FinanceAnalysis/issues/146) | Persist the feature schema; strict validation at inference | mismatch raises instead of neutral-filling |
| [#147](https://github.com/sean7084/FinanceAnalysis/issues/147) | Per-date candidate snapshots across the full cross-section | closes the zero-trade reference blind spot |
| [#148](https://github.com/sean7084/FinanceAnalysis/issues/148) | Prediction audit: predictions vs realised returns by horizon | stored predictions only, no re-training |

The purged holdout (#144) already exists and already reaches the model metrics; what is missing is
running it and surfacing the window it was measured on. #137 must not run before #136 lands — a
rematch on the current layout re-measures the harness, not the strategy.

## M3 — hygiene and micro-tasks

| Issue | One line |
|---|---|
| [#151](https://github.com/sean7084/FinanceAnalysis/issues/151) | Correct the lazy-import rationale in `apps/prediction/tasks.py` — fix in [PR #164](https://github.com/sean7084/FinanceAnalysis/pull/164); the note's premise was also wrong: the deferral saves the **web** process, while workers load the trainer anyway via `apps/backtest/tasks.py:60` + `autodiscover_tasks()` |
| [#152](https://github.com/sean7084/FinanceAnalysis/issues/152) | Engine decision rules — the four named behaviours already had docstrings; [PR #167](https://github.com/sean7084/FinanceAnalysis/pull/167) adds the five mechanical functions around them (piecewise commission floor, resume-progress coupling) |
| ~~#153~~ closed | Eight orphaned screener i18n keys removed in [PR #168](https://github.com/sean7084/FinanceAnalysis/pull/168), gated on 408 `t()` literals resolving in both locales — `tsc` and eslint cannot catch an over-deletion here |
| [#154](https://github.com/sean7084/FinanceAnalysis/issues/154) | Macro semantics: `dxy` name, publication-date alignment, where northbound flow belongs |
| [#155](https://github.com/sean7084/FinanceAnalysis/issues/155) | Entitlement flags cached with `timeout=None` — documented-convention option shipped in [PR #166](https://github.com/sean7084/FinanceAnalysis/pull/166); TTL declined because the condition only changes when the operator changes the token | one operator walk-through of `runbook-provider-blackout.md` §9 |
| [#156](https://github.com/sean7084/FinanceAnalysis/issues/156) | `amount` unit — **already documented** (`models.py:220` states 千元 vs 元 and the do-not-join rule); remainder is the `raw_close IS NULL` asset count and whether the repair walks them |
| [#160](https://github.com/sean7084/FinanceAnalysis/issues/160) | Operator model-version selection — and which surface absorbs it |
| ~~#161~~ closed | `apps/macro/tests.py` gitignored-CSV dependency — **verified already fixed**: this host has no `source_data/` at all and the pre-2016 yield case still reports `csv_yield_updates=21` from a temp fixture |

## Owner decisions — deployment hygiene

Not schedulable as diffs: each needs a deployment choice first, and neither closes with a PR.

| Issue | The mismatch | What has to be chosen |
|---|---|---|
| [#149](https://github.com/sean7084/FinanceAnalysis/issues/149) | mlflow pulls `gunicorn==25.3.0` / `uvicorn==0.54.0` into `local.txt`, while `production.txt` pins the declared `23.0.0` / `0.32.0`; the lockfiles are resolved on Python 3.12 but CI runs 3.14 | align production to what CI exercises, pin the server stack in the `.in` files, or declare the divergence intentional and stop claiming CI covers the production server |
| [#150](https://github.com/sean7084/FinanceAnalysis/issues/150) | `torch==2.12.0` resolves from the default index with **no** `--index-url` / `--extra-index-url` / `--find-links` anywhere, and the hashed closure proves that wheel is the CUDA flavour: `cuda-toolkit[…]`, fifteen `nvidia-*` packages, `triton==3.7.0` — while nothing documents which runtime has a driver, and `windows_gpu` is a valid inference backend | name the GPU-bearing runtimes (this host, the NAS, CI, any Windows worker), then either support GPU inference and pin the CUDA deps by intent, or drop `windows_gpu` and stop shipping ~GB of CUDA wheels that CI downloads to run GPU-less tests |

`scripts/check_requirements_lockfile.py` passes for both of these — each file is internally
consistent — which is exactly why nothing catches them. The `blocked` label is the honest state.

## Blocked on a data source

These are **not schedulable code work**. Each re-opens only on a purchasing decision or on a
declared re-baseline that invalidates existing readings.

| Issue | Evidence | Re-opens on |
|---|---|---|
| [#157](https://github.com/sean7084/FinanceAnalysis/issues/157) index valuation | 12.1% pool coverage (`index_dailybasic`: 6 broad indices); #66 terminated the family at 2.01% of mapped indices | a source covering thematic indices (CSIndex direct, Wind), re-measured against the 70% floor |
| [#158](https://github.com/sean7084/FinanceAnalysis/issues/158) P30 constituent breadth | 53.2% of eligible rows vs the 70% floor; `idx_share_above_ma60` is 0.83-correlated with `ma60_ratio`; `idx_mom_consistency` ICIR 0.13 | monthly weights back to 2016, or a declared post-2020 window — which invalidates every reading since P18 |
| [#159](https://github.com/sean7084/FinanceAnalysis/issues/159) news / sentiment | deleted with #52: the feature was non-zero on 151 of 181,130 rows; `sentiment_score` held one distinct value across 2,043,000 rows; retraining without it *improved* 30d `rank_ic` | a provider/entitlement decision, treated as a new subsystem with its own pre-registered criterion |

---

## Deferred ideas

Unprioritised, unvalidated, and with no commitment attached. Kept here rather than filed as issues
because none is actionable as written; the reasoning that made it a deferral is in
[`BACKLOG_ARCHIVE.md`](BACKLOG_ARCHIVE.md) §D and §F.

- **Market rules**: limit-up / limit-down regimes (10% / 20% / ST; main board vs ChiNext vs STAR vs BSE) are not modelled, and limit-up days are not marked.
- **Features**: promote the stored macro series (`dxy`, `cny_usd`, `cpi_yoy`, `ppi_yoy`) into features; decide whether `technical_score` should read the stored analytics (MACD, ADX, OBV, SMA/EMA, RS score) instead of the reversal scorer, and say so where a reader will look.
- **Architecture**: replace the heuristic in the ensemble with a tuned multi-factor linear model; replace LSTM with a Transformer / TFT; add a 14-day horizon.
- **Pipeline**: backfill as a validation-only step, off the model-rebuild path.
- **Execution**: entry price at the close is not achievable (intraday minute data would model real cost); the Tuesday/Thursday entry weekday is unjustified as far as anyone has measured; position sizing from binary `suggested` to half-Kelly or a `trade_score` tier map.
- **Product**: sector/theme rotation context; CSRC/NDRC policy text analysis; personal holdings (cost basis + size) for portfolio-specific suggestions.
- **Quality bar** the model work aims at, against the 3.2 baseline:

  | Metric | Baseline | Target |
  | --- | --- | --- |
  | Out-of-sample return | +10.66% | > 20% (approach or beat benchmark) |
  | Out-of-sample Sharpe | 0.81 | > 1.0 |
  | Out-of-sample win rate | 50.56% | > 55% |
  | In/out-of-sample alpha gap | ~44% | < 20% |

- **Re-runs awaiting an instrument**: the `no_flow` feature-set panel batch, the dedup-off rerun of rows 354 and 356–361, and the Kelly floor sweep — all three gated on #136, with their evidence in §C and §D of the archive.

---

## Archive

[`BACKLOG_ARCHIVE.md`](BACKLOG_ARCHIVE.md) — resolved narratives, measurement evidence, and
no-action decisions, copied verbatim from the pre-restructure file (commit `c225c0d`). Sections:
**A** resolved-general, **B** resolved-correctness, **C** research programme P18–P37, **D** ETF
measurement results and decisions, **E** data-correctness forensics, **F** no-action decisions,
**G** the retired/stale log (what was deleted rather than archived, and why).

Convention: when an issue closes, its record goes to `CHANGELOG.md`; a research finding that is not
a fix goes to `BACKLOG_ARCHIVE.md`; this file loses the row. Prose does not come back here.
