# Changelog

### version 0.1.39

#### Added:

- **The P35 gate is read from the ledger, on its pre-registered criteria.** `--gate p35` on
  `report_etf_rotation_evidence` recomputes per-fold IC mean and ICIR from the per-block
  readings rows already store, keyed by (arm, label, fold plan, feature schema) so two schemas
  are never averaged together, and names the criterion that failed. No run is repeated.
- **`seed_variance_table`** reports Sharpe and IC mean/sd per (cell, seed count), so the
  seed-averaging claim has a table instead of a sentence.
- `_distribution` now carries `mean` and `sd` alongside the quantiles.

#### Numbers:

- Every ML cell fails the declared IC-mean threshold: the best is the 28-column LightGBM veto
  at **0.0424 / ICIR 0.6977**; the 24-column veto reads 0.0337/0.5937, the selectors
  -0.0183..0.0337, dd_rank -0.0177. The P35 no-go is now confirmed on the criteria written
  down in advance, not only on replication counts (report section 29; §17.2 carries a dated
  appendix, the old argument is left in place).
- Seed variance: the veto arm reads Sharpe 0.5400 at seeds=1 and 0.3372 at seeds=5 -- about
  0.2 of that pass was one lucky seed.

#### Verified:

- Backend 813 -> 816 tests; frontend unchanged at 79. No measurement was re-run and no curve
  changed.

### version 0.1.38

#### Added:

- **Fourth label: `label_downside_rank`.** Same safety ordering as `label_dd_rank` (least
  downside risk ranks 1.0) but built from `fwd_downside_deviation`, so a regression objective
  and a ranker no longer share one series. Migration `0021`; `--label` now accepts
  `rank_pct / top_bucket / dd_rank / downside_rank` on both the trainer and the CPCV path.
- **`LABEL_SCHEMA_VERSION`** joins artifact metadata and the ledger context. A label change
  moves it; the feature schema (a hash of the column list) does not move, and a test pins that
  the two are independent.
- **Ablation and label pivots.** `report_etf_rotation_evidence --label-table / --ablation-table`
  read `results.blocks[].mean_ic` and `results.sharpe.median` from ledger rows that already
  exist and print mean IC, ICIR and median Sharpe grouped by arm, label, feature set and
  schema. No run is repeated.
- Feature-set definitions (`FEATURE_SETS = {all, no_flow}`, `resolve_feature_set`): a subset
  gets its own schema hash automatically, so it can never share a table with the full set.

#### Known gap:

- The trainers and the CPCV path do **not** accept `--feature-set` yet -- training frames are
  assembled from `FEATURE_COLUMNS` internally -- so every ledger row carries
  `feature_set: 'all'` and the ablation matrix's feature-set axis has one column for now.
  #71 stays open for that wiring rather than shipping a flag that records but does not apply.

#### Verified:

- Backend 799 -> 813 tests; frontend unchanged at 79. Migration applied to the dev database;
  `makemigrations --check` clean. No curve changed: the new label is look-only and the pivots
  read existing rows.

### version 0.1.37

#### Fixed:

- **Every measurement path appends a ledger row.** The walk-forward and pinned-artifact CPCV
  paths wrote none, so they could not produce gate-admissible evidence and refused
  `--preregistration`. Both now build their context through one `measurement_context()` helper,
  record their blocks/folds, and accept a registration; `--grid` and single runs still refuse
  one because they still write nothing.
- A RULE walk-forward row records `ic_by_fold: [null, ...]` and says the IC is unmeasurable on
  that path. RULE fits no scorer, so zero would have been a fabricated reading.
- `report_etf_rotation_evidence` prints reads per panel start, so "how often has this panel been
  read" is answerable without counting rows by hand.

#### Verified:

- Backend 795 -> 799 tests; frontend unchanged at 79. No curve changed: this version only adds
  bookkeeping and makes `--preregistration` admissible where a row exists.

### version 0.1.36

研究计划收尾修正：台账接线、arm 隔离、折计划覆盖面，以及 §17.1 的可追溯性。

#### Fixed:

- **Two shadow arms shared one turnover book.** `previous_book` looked up the newest signal of a
  `signal_type` with no arm filter, so whichever arm ran second measured its turnover constraint
  against the other arm's holdings — the two books stopped being independent observations of the
  same panel, which is the only reason to run two. It now takes the arm; the production book
  (`arm=''`) reads exactly what it did before.
- **The pre-registration hash never reached the ledger.** `record_measurement` accepted one and
  nothing passed it, so all 29 rows said `preregistration: ""` and no verdict could be tied back
  to the criteria declared before the run — which is what the gate tightened in plan §9 requires.
  `--preregistration PATH` is now an option on `run_etf_rotation_backtest`, the sensitivity grid
  hands its file to every cell, and one shared digest helper keeps the ledger value and the
  report filename from disagreeing. A registration on a path that writes no ledger row is
  refused rather than silently dropped.
- **The ledger context did not describe the execution model.** A run behind a macro gate and a
  run without one shared a context, so the repeat counter — the only thing that shows how often
  this finite panel has been read — could not tell them apart. The context now carries the
  resolved `BacktestConfig` fields that move the curve (fee, band, capacity, Kelly, macro gate,
  selection knobs), resolved through the config so an unset flag and a default-valued flag are
  the same measurement. Rows written before this read as "defaults", so the count does not
  restart at the schema change.
- **`--fold-plan` only worked on the CPCV path.** `--walk-forward-steps` built expanding folds
  whatever was asked. It now accepts `expanding`/`rolling` and refuses `cpcv`, which means
  nothing on a path that selects parameters per fold; the flag defaults per path (`cpcv` for
  blocks, `expanding` for walk-forward) instead of one default serving both.

#### Changed:

- `report_etf_rotation_evidence` prints the pre-registration hash per row, warns how many rows
  carry none, and groups repeats with the same normalized context the writer uses.
- Report §17.1 is two tables split by feature schema (`eab79996d610` / `b6165919fbf3`), every
  row carrying its evidence-ledger id and pre-registration hash where one exists. The two
  selector rows with no ledger row are marked as untraceable instead of looking like the rest,
  and the cross-schema comparison the plan forbids is now impossible by construction.
- The BACKLOG "ML as a filter" entry is rewritten: it quoted the `purge=1` numbers (0.2906 vs
  0.2227) that §12 superseded and asked for the sensitivity round that has since run
  (`lgb-veto` 2/3, `lstm-veto` 1/3, failing at the 2018 start).

#### Added:

- Issues #65-#72, listed in BACKLOG under "research programme: what P18-P37 did not finish":
  coverage denominator and coverage bias, valuation quality gate, share-flow confounds, macro
  release dates and features, variance table and the P35 criteria, rolling per-fold IC, the
  remaining label options, and ledger coverage of the other measurement paths.

#### Verified:

- Backend 783 → 795 tests (twelve new: arm-scoped turnover incumbents, the execution-model
  context and its back-compatibility with rows that predate it, the registration digest and its
  three refusals and its forwarding from the sensitivity grid, rolling walk-forward folds, and
  the unregistered-row warning); frontend unchanged at 79.
- `manage.py check`, `makemigrations --check`, `export_documentation_facts --check` and
  `scripts/verify_local_stack.sh` clean; no migration in this version, so the dev database is
  unchanged.

### version 0.1.35

臂判别（P22）、标签 horizon 扫描（P25）、消融协议（P26）与成分广度数据层（P30）。

#### Added:

- **Arm discriminator (P22).** `ETFRotationSignal.arm` (migration 0018) joins the unique key,
  so two candidate books can publish on the same date instead of overwriting each other.
  `generate_etf_rotation_signal --arm`, `position_ledger.arm_of`, `live_track` books keyed
  `SHADOW:<arm>`, and `ETF_ML_SHADOW_ARMS` (one fail-closed entry per arm) make the shadow
  book able to observe several candidates without touching the production path.
- **Label-horizon sweep (P25).** `run_etf_rotation_backtest --cpcv-retrain --horizons 20,40,60`
  re-reads the same per-fold scores against price-based forward returns at each horizon
  (`rank_ic.forward_return_panel`), so IC decay is measurable without a second label column.
- **Constituent breadth (P30).** `IndexWeightSnapshot` (migration 0019) plus
  `sync_index_weight`, which splits a page that comes back at TuShare's cap instead of
  silently truncating. `features.attach_index_breadth` derives `idx_share_above_ma60`,
  `idx_ret_dispersion_20d`, `idx_mom_consistency` from `markets.OHLCV` as of D-1;
  `ETFFeatureSnapshot` gained the four columns (migration 0020).
- The valuation and breadth columns now actually reach the snapshot row: both are produced by
  an attach function after assembly, so `build_snapshot_models` and `_full_update_fields`
  read a shared `ATTACHED_FEATURE_COLUMNS` tuple instead of only `FEATURE_COLUMNS`.
- **LSTM capacity is configuration (P21).** `ETF_LSTM_{SEQUENCE_LENGTH,HIDDEN_SIZE,
  NUM_LAYERS,DROPOUT,EPOCHS,BATCH_SIZE,LEARNING_RATE,PATIENCE}` are real settings, resolved by
  `resolve_lstm_hyperparameters` (argument beats setting beats the old literal). Defaults
  reproduce the previous model exactly; an unusable value is refused by name.
- `ETF_ML_SHADOW_ARMS` is declared in `config/settings/base.py` (JSON list). It was read via
  `getattr` before, which meant a value in `.env` never reached the task.
- `scripts/verify_local_stack.sh` passes `PYTHON_BIN`/`PROJECT_ROOT` to the migration check;
  the child process only saw them unset, so the check always reported "PYTHON_BIN must be set".
- Threads buy time, not readings: the same `lgb-select` arm runs **92.3 s at 4 threads vs
  66.8 s at 14**, with identical per-block Sharpe and IC. A fixed seed is bit-reproducible
  (`LightgbmSeedReproducibilityTests`). GPU stays off -- the wheel/OpenCL probe fails.

#### Numbers:

- Label horizon, same scores: mean IC 0.0337 / 0.0308 / 0.0311 and **ICIR 0.5937 / 0.4561 /
  0.3952** at 20 / 40 / 60 sessions. Longer labels lose consistency, so the 20-session label
  stays and no training-label change is justified (report section 21).
- Constituent breadth: 116 indices / 2.97M weight rows, but only **53.2% of eligible rows**
  (53.1% inside 2016-2023) have breadth -- TuShare's `index_weight` has no 2016-2017 history
  for most thematic indices. Below the pre-registered 70% floor, so the three columns stay
  out of `FEATURE_COLUMNS`. On the covered rows `idx_share_above_ma60` correlates 0.83 with
  `ma60_ratio` (redundant), and the one orthogonal feature, `idx_mom_consistency`, has
  ICIR 0.13 against the 0.5 gate (report section 23).

### version 0.1.34

宏观 regime 闸门（报告 §20）。

#### Added:

- **Macro regime gate.** `BacktestConfig.macro_gate_pmi` / `macro_gate_scale` plus
  `macro_risk_gate`: while the latest *published* manufacturing PMI (a 35-day publication lag)
  sits below the threshold, risk-on exposure is multiplied by the scale and combined with the
  Kelly governor. Off by default (0), so earlier runs are bit-identical. CLI:
  `--macro-gate-pmi`, `--macro-gate-scale`. `state.macro_history` records every gated session.
- A test enforces that macro series never enter `FEATURE_COLUMNS`: a date-level value cannot
  rank funds, so it belongs in the gate and nowhere else.

#### Numbers:

- RULE with PMI<50 -> 0.5x exposure, same six blocks: median Sharpe 0.1881 -> 0.1283, positive
  blocks 83.33% -> **100%**, median maxDD 17.97% -> 16.95%, **worst block -6.31% -> -3.21%**.
  The gate does what a de-risking switch should; it fails R3' only because that criterion asks
  a risk control to beat the base strategy on return.

### version 0.1.33

份额流特征已启用并测量（报告 §19）。

#### Fixed:

- **`attach_index_valuation` served one ETF per index.** It mapped valuation rows through a
  reversed ``{index_code: etf}`` dict, so of the 170 ETFs tracking 沪深300 only one received a
  value. It now maps ETF -> index on the block and joins on ``index_code``; a test pins two
  ETFs sharing one index.
- **Background-run state misread.** Section 18 blamed a duplicate-key upsert on a pre-existing
  block defect; the real cause was two backfills running concurrently. Re-running a single
  process wrote 2,602,300 rows cleanly. Marker files must get unique names per run.

#### Changed:

- **Share-flow features are in the model input.** `share_growth_5d/20d`, `flow_x_mom` and
  `has_share_flow` joined `FEATURE_COLUMNS` (24 -> 28) after the backfill showed 95.8%
  coverage of eligible rows.

#### Numbers:

- Adding the flow features moved the LightGBM selector from -0.1041 to -0.3607 and the veto
  arm from 0.5400 to 0.3801 (median Sharpe, same blocks). Both changes sit inside the ~1
  Sharpe start-to-start noise band, so the honest reading is "no measurable gain", not
  "harmful". Per-block rank IC barely moved, i.e. the new columns are largely redundant with
  the existing momentum and turnover features.

### version 0.1.32

研究计划 Phase 31/33/34/35/36（份额流、滚动对比、seed 平均、决策门）。
数据：`docs/reports/etf-rotation-cpcv-model-comparison-2026-10-25.md` §15-§18。

#### Added:

- **`--fold-plan expanding`**, so a rolling-vs-expanding comparison shares one validation
  calendar and differs only in training-window shape.
- **`--seeds N`**: N boosters per fold with different seeds, scores averaged by
  `_mean_scorer`. Variance reduction, not a bigger model.
- **`ETFShareDaily` + `sync_etf_share` + `attach_share_flow`**: daily creation/redemption
  flow for 811/811 pool funds (854,170 rows, 100% coverage) with `share_growth_5d/20d`,
  `flow_x_mom` and a coverage flag, all lagged one session.

#### Fixed:

- **Two many-to-many joins in the feature attachments.** Both `attach_index_valuation` and
  `attach_share_flow` mapped dozens of history rows onto one sparse block session; without a
  per-session dedupe the merge was a cartesian product (a 2-row block returned 2,000 rows).
  Both now dedupe, validate and assert the row count. A regression test pins it.

#### Numbers:

- Rolling vs expanding, same blocks: RULE reads identically (0.0791) under both, LightGBM
  goes from -0.7320 (expanding) to +0.4945 (rolling) -- but the pre-registered grid across
  three panel starts passes only **1/3**, so that is a boundary artefact.
- Seed averaging on the veto arm: median Sharpe 0.5400 -> 0.3372 with consistency unchanged,
  i.e. roughly 0.2 Sharpe of the earlier result was one lucky seed.
- **The instrument is the bottleneck**: RULE, which has no learned parameters, reads 0.0791 /
  0.4460 / -0.5284 across three panel starts. Six blocks of ~20 rebalances cannot resolve a
  0.1-0.5 effect. P35 is therefore no-go for a reason that has nothing to do with models.
- Share-flow columns are **switched off** pending the duplicate-key defect those columns
  exposed in the assembled block (report section 18).

### version 0.1.31

研究计划 Phase 27-29（指数映射、估值接入、估值特征）。结果：
`docs/reports/etf-rotation-cpcv-model-comparison-2026-10-25.md` §14。

#### Added:

- **ETF -> tracked index mapping.** `ETFIndexMapping` plus `build_etf_index_mapping`,
  which matches `fund_basic`'s `benchmark` text against the 9,575-name `index_basic`
  catalogue (longest name wins, composite benchmarks marked `partial`). 1,474 of the 1,715
  pool ETFs resolve (85.9%), above the pre-registered 70% floor. Coverage report:
  `docs/reports/etf-index-mapping-coverage.md`.
- **Index valuation ingestion.** `IndexValuationDaily` plus `sync_index_valuation`: one call
  per mapped index, retries with backoff, `--only-missing` for resumes.
- **Valuation features (built, switched off).** `features.attach_index_valuation` adds
  `idx_pe_pct_3y`, `idx_pb_pct_3y`, `idx_turnover_z` and `has_index_valuation`, all as of
  D-1, with the rolling percentile on the index's own history. Migration `0015`.

#### Decided:

- **The valuation feature family stops here.** TuShare's `index_dailybasic` covers six broad
  indices only: 292 of the 298 mapped codes return empty frames (verified through the MCP
  client too, so it is coverage, not throttling), which reaches 207/1,715 = **12.1%** of the
  pool. A model trained on a column that is 88% missing learns whether a fund has a
  broad-benchmark mapping, not a valuation signal. The four columns stay on the model as
  NULL and out of `FEATURE_COLUMNS`; the mapping table and the attach path are kept because
  a source with real coverage (or the P30 breadth work) can reuse them.

### version 0.1.30

研究计划 Phase 20/24/33/37（敏感性格栅、下行标签、闸门收紧）。数据：
`docs/reports/etf-rotation-cpcv-model-comparison-2026-10-25.md` §13。

#### Added:

- **Pre-registered sensitivity harness.** `measure_etf_rotation_sensitivity` refuses to run
  without a pre-registration file, refuses any grid cell the file does not declare, and
  requires the baseline arm to be in the grid. Results are aggregated per arm with the
  pre-registered three legs and a "passed in k/n panel starts" verdict; the file's sha256 is
  written into the report name. First grid: `docs/reports/sensitivity/sensitivity-veto-2026-10-25-604e01283f7c.md`.
- **Downside labels.** `fwd_max_drawdown`, `fwd_downside_deviation` and `label_dd_rank` (the
  safety rank: smallest drawdown ranks 1.0) plus a `fwd_downside_deviation` builder that does
  not pad missing bars; `--label {rank_pct, dd_rank}` on both the trainer and the CPCV
  retrain path, LightGBM only. Migration `etf_rotation/0012`; the full panel was recomputed
  (2,602,300 rows, 350,851 labelled).

#### Changed:

- **The gate tightened (plan section 9).** A CV pass now needs replication across >= 2 veto
  models or >= 2 panel starts, a purge >= the label horizon, and a ledger record carrying a
  pre-registration hash. The older "all three legs and done" wording is marked superseded.

#### Numbers:

- Sensitivity grid, three panel starts, fixed 20% quantile: `lgb-veto` passes 2/3 (fails
  2018 at -0.4900 against RULE 0.2306), `lstm-veto` passes 1/3. **The veto effect does not
  replicate**, so the 2016 reading in section 12 was a boundary artefact and the arm does
  not qualify for forward observation.
- Downside-label veto arm: median Sharpe -0.2292, positive blocks 50%, median maxDD 13.26%
  (the lowest of any arm) against RULE's 0.1881 / 83.33% / 17.97%. Predicting risk does
  reduce drawdown and destroys return; the hypothesis is falsified on the return side.

### version 0.1.29

研究计划 Phase 18-23（证据修正与测量基础设施）。计划：
`docs/plans/etf-rotation-next-phases-2026-10-24.md` §8-§9；数据：
`docs/reports/etf-rotation-cpcv-model-comparison-2026-10-25.md` §12。

#### Fixed:

- **CPCV purged one session instead of the label window.** `CPCV_LABEL_HORIZON = 1` meant
  a training date up to 19 sessions before a held-out block carried a `fwd_return` computed
  from bars *inside* that block, so a fold model was fitted on the answer key.
  `resolve_cpcv_purge` now gives RULE a one-session purge and an ML arm its
  `ETF_LABEL_HORIZON_DAYS`, and `--cpcv-purge` may raise the value but never lower it.
- **The purge also moved the block boundaries.** `combinatorial_purged_folds` truncated the
  panel tail by `horizon_days`, so a different purge re-partitioned the data and two arms
  could not be read side by side. `panel_tail_days` pins the layout to the label window:
  RULE (purge 1) and an ML arm (purge 20) now land on identical blocks.

#### Added:

- **Rolling folds.** `walk_forward.rolling_folds` keeps the expanding plan's validation
  calendar but trains on a fixed-length window that slides (`--fold-plan rolling`,
  `--rolling-train-days`, default 750 sessions). No embargo parameter: every training date
  is before its block by construction.
- **Measurement ledger.** `apps/etf_rotation/evidence.py` appends one JSON line per
  measurement (panel, fold plan, purge, embargo, arm, model, feature schema, label horizon,
  results, preregistration hash) to `docs/reports/evidence/etf-rotation-measurements.jsonl`;
  `report_etf_rotation_evidence` renders it and counts how often a context has been read.
  The panel is finite, so a repeat has to be visible.
- **LightGBM thread setting.** `ETF_LGB_NUM_THREADS` (default `cpu_count - 2`, capped at 16)
  replaces the hard-coded `num_threads: 4`; `ETF_LGB_PARAMS` still wins. `device`,
  `device_type`, `gpu_use_dp`, `gpu_platform_id` and `gpu_device_id` joined the native
  parameter allow-list, so a GPU-capable wheel can be selected from settings. The installed
  wheel has no OpenCL device, so LightGBM stays on CPU.

#### Numbers (same six blocks, purge 20 for ML, purge 1 for RULE):

| arm | median Sharpe | positive blocks | median maxDD | worst block |
| :--- | ---: | ---: | ---: | ---: |
| RULE | 0.1881 | 83.33% | 17.97% | -6.31% |
| LightGBM selector | -0.1041 | 50.00% | 18.44% | -13.82% |
| LSTM selector | 0.1177 | 66.67% | 19.32% | -13.72% |
| RULE + LightGBM veto | 0.5400 | 83.33% | 16.78% | -17.62% |
| RULE + LSTM veto | 0.0841 | 66.67% | 19.09% | -21.93% |

Both selector arms still fail; the LightGBM veto arm still passes and is stronger than
under the leaky purge (0.2906 -> 0.5400); the LSTM veto arm still fails, so the "ML as a
filter" hypothesis remains unreplicated. RULE stays in production, no model is activated.
Rolling folds, 2016-2026 with a 750-session window: LightGBM median Sharpe -0.2640 over six
folds (a tuning reading - the panel reaches 2026).

### version 0.1.28

holdout 释放、按折重训与四臂复赛。记录：
`docs/plans/etf-rotation-next-phases-2026-10-24.md` §8；
数据：`docs/reports/etf-rotation-cpcv-model-comparison-2026-10-25.md` §11。

#### Changed:

- **The 2024-2026 holdout was released into the training pool (2026-10-25).** It had
  already been read twice (the §7.7 weighting adjudication and the Phase D gate), so it
  could no longer serve as a clean test set, and keeping it out of training bought
  nothing. `LOCKED_HOLDOUT_START` became `RELEASED_HOLDOUT_START` plus
  `HOLDOUT_RELEASED_ON` in both `run_etf_rotation_backtest` and
  `sweep_etf_rotation_lstm`: a window reaching 2024-01-01 is now **labelled** a tuning
  measurement instead of being refused, and the label is printed on every path. The
  `--allow-holdout-overlap` escape hatch was retired with the guard it escaped. What the
  release does not buy back is an out-of-sample claim: the sealed forward window
  (the live ledger, accruing from 2026-10-25) is the only honest one left.
- **`train_etf_rotation_model --no-activate`.** The trainer defaults to `activate=True`,
  so the first post-release retrain would have silently become the serving model -- the
  exact state the activation gate forbids. The flag reaches both trainers and
  `register_ensemble_version`, and the report prints the resulting activation state.

#### Added:

- **Pinned shadow signals.** `signals.pinned_predictors` loads a named READY artifact
  without touching `is_active`, `generate_rotation_signal(model_version=...)` scores with
  it and records the key in `metadata['model_versions']`, and
  `generate_etf_rotation_signal --model-version` exposes it. `ETF_ML_SHADOW_MODEL` now
  accepts `LSTM:<version>` (a bare model type still resolves the active row). Before this,
  starting a shadow and passing the activation gate were the same action -- so neither
  could happen first.
- **Per-fold retraining for the CPCV rematch.** `walk_forward.train_dates_for_any_fold` /
  `validation_blocks_for_any_fold` read the split from either fold shape;
  `train_lightgbm.train_fold_predictors` fits one booster per fold;
  `train_lstm.train_walk_forward(folds=..., capture_fold_predictors=True)` returns the
  per-fold scorers. `run_etf_rotation_backtest --cpcv-retrain` then scores each held-out
  block with a model trained on its complement and discards it: no `ModelVersion` row, no
  artifact, no activation. RULE is allowed in the same mode so the baseline is measured
  the same way. A leak assertion refuses a fold whose training dates touch its own block.
- **`scripts/_check_migrations.sh`**, called by `verify_local_stack.sh`/`.ps1` and by
  `run_backend.sh`: a committed-but-unapplied migration now fails startup. On 2026-10-25
  four migrations were merged and never applied locally; every test passed (the test
  database is built from them) and the SPA showed only "ETF data failed to load".

#### Numbers:

- Per-fold rematch, 2016-01-29..2023-12-31, six blocks, each scored by a model that never
  saw it: RULE median Sharpe 0.2227 / 83.33% positive blocks / median maxDD 17.97%;
  LightGBM -0.0136 / 50.00% / 9.35%; LSTM see the report. **RULE stays**: the ML arms lose
  on return and consistency and win only on drawdown.
- **Fourth arm (RULE selects, ML vetoes) measured.** `--cpcv-retrain --cpcv-hybrid --model X`
  plus `--ml-veto-quantile` (fixed 0.2) removes the names the fold model likes least before
  RULE's own selection: LightGBM veto -> median Sharpe 0.2906 / 83.33% positive blocks /
  median maxDD 15.62% (beats RULE's 0.2227 / 83.33% / 17.97%); LSTM veto -> 0.1515 /
  83.33% / 17.99% (**worse** Sharpe). Two veto models, opposite answers: the "ML as a
  filter" hypothesis is supported by one arm and contradicted by the other, so it does not
  justify a production change. The engine side is `RotationConfig.ml_veto_quantile` plus a
  `__ml_score` column the backtest engine attaches per session.
- Post-release artifacts, both registered **inactive**: `etf-lgb-20d-lambdarank-2026-09-17-post-release`
  (pooled rank IC 0.0648, 5/6 folds positive) and `etf-lstm-2026-09-17-post-release`
  (0.0318, 4/6).

### version 0.1.27

Phases 6-12 of the ETF rotation follow-on work. Plan and full record:
`docs/plans/etf-rotation-next-phases-2026-10-24.md`.

#### Added:

- **Capital-base share sizing for the live ledger.** `ETF_SIM_CAPITAL_BASE`
  (default 1,000,000) turns each new signal leg's target weight into a whole-lot
  share count (`floor(base x weight / price / 100) * 100`), so
  `mark_etf_rotation_positions_daily` can state a yuan `pnl` instead of only a
  price return. `0` restores the previous `shares=NULL` / `pnl=NULL` behaviour.
  A carried holding keeps the shares it was opened with: a carry-forward
  refreshes the target weight, not the position's history.
- **Combinatorial purged cross-validation.** `walk_forward.combinatorial_purged_folds`
  cuts the panel into blocks, holds out every combination of `test_blocks`, and
  builds each training set by purging label-window overlap and embargoing the
  sessions after a held-out block. `audit_combinatorial_folds` re-derives and
  checks the plan. The CLI surface is
  `run_etf_rotation_backtest --cpcv-blocks N --cpcv-test-blocks K --cpcv-embargo D`:
  ephemeral, single-configuration, and it refuses any window reaching 2024-01-01.
- **ML rematch shadow book.** `ETFRotationSignal.SignalType.SHADOW` plus
  `ETF_ML_SHADOW_MODEL` (empty by default). When set, a 19:50 beat task writes a
  second daily signal under `SHADOW`; the ledger tracks it as its own book. The
  signal and position endpoints hide it unless `?signal_type=SHADOW` is passed,
  so production reads cannot mix in rematch performance. The task is fail-closed:
  an ML type with no active `ModelVersion` writes `REFUSED/model_registry_missing`
  and raises rather than degrading to the RULE blend.
- **Forward-track report and drift alert.** `report_etf_rotation_live_track` is a
  read-only per-book summary (realised/unrealised pnl, win rate, mean return).
  With a declared reference (`ETF_LIVE_REFERENCE_BACKTEST_ID`) it compares the
  live closed-trade distribution against that row and can write an asset-less
  `LIVE_DRIFT` alert event; the monthly beat task is the only caller that passes
  `--alert`. The report has no write path for signals, positions or parameters.

#### Changed:

- **`max_positions` default 5 -> 3**, aligning the live default with the
  production reference row 256 (`p3`) and the §7.7 holdout book. This is an
  alignment, not evidence that p3 is better: the §7.12 walk-forward selected
  p3/p3/p5/p8 across its four folds. Five sites now agree -
  `ETF_MAX_POSITIONS`, `DEFAULT_MAX_POSITIONS`, `RotationConfig`,
  `BacktestConfig` (now reads settings) and both model columns; `train_lstm`'s
  Top-N fallback follows. A deployer's `ETF_MAX_POSITIONS` override still wins.
- **Kelly and cash-yield knobs are backtest columns** (`kelly_fraction`,
  `kelly_cap`, `kelly_floor`, `kelly_lookback_trades`, `cash_yield_annual`,
  migration `etf_rotation/0009`). `--queue` can now carry a sized book. Defaults
  `0 / 1.0 / 0 / 60 / 0` reproduce the pre-promotion engine bit for bit; the
  resume fingerprint changes, so old PAUSED rows restart from the beginning.
- **`/etf/backtests/` accepts `?dedup_track_index=true|false`.** The 2026-10 rows
  written while `--no-dedup-track-index` was silently applied self-describe in
  that column; `true` reads the production-rule rows alone without hiding the
  intended dedup-off control when no filter is passed.
- `position_fees_paid` on a closed trade now carries a reader caution at the
  point it is written: pre-0.1.23 rows hold commission only, so cross-generation
  comparisons should use `total_fees` / `total_slippage_cost`.

- **Pinned-version inference for the ML rematch.** `load_predictor_for_version`
  in both trainers loads a READY artifact by version string while ignoring
  `is_active`, so a candidate can be measured without being deployed, and
  `run_etf_rotation_backtest --cpcv-blocks --model LIGHTGBM --model-version <v>`
  scores with it. `apps/etf_rotation/rank_ic.py` reports per-block Spearman IC
  next to the portfolio metrics (IC is signal quality, not alpha). A load never
  flips `is_active`; the tests pin that.

- **LSTM backtests no longer look ahead.** `rotation_engine._call_predictor` used
  to drop the `date` column, so the LSTM scorer (whose contract needs each row's
  as-of date to assemble its trailing window) fell back to the newest eligible
  snapshot for every historical session. The as-of date now travels with the
  scoring frame; LightGBM reindexes to its trained columns, ignores the extra
  key, and produces identical numbers. The fix has a test, and the LSTM CPCV arm
  logs zero "no date column" warnings. Phase D's stored LSTM numbers (row 209)
  were produced through the old path and must not be quoted as valid
  measurements; re-measuring them would re-query the spent holdout.

#### Notes:

- Migrations `etf_rotation/0010` (default 3), `0011` (SHADOW choice) and
  `analytics/0009` (LIVE_DRIFT choice) are column-default/choice changes only;
  no data migration and no existing row is rewritten.
- **CPCV evidence is now on the record.** The row-256 distribution and the
  p3-vs-p5 comparison ran against the dev panel; both are in
  `docs/plans/etf-rotation-next-phases-2026-10-24.md` §7.9/§7.10. p3 stays the
  default: p5 wins on median Sharpe and median drawdown but loses positive-block
  share (66.67% vs 83.33%), so the pre-registered three-leg rule did not fire.
- **The ML rematch now has a verdict, and it is a loss.** LSTM was adjudicated and
  failed the CV leg: Sharpe median 0.1238 vs RULE 0.1620, positive blocks 66.67%
  vs 83.33%, median drawdown 15.93% vs 17.96% (only the drawdown leg won). Its
  serving artifact is fold 5, trained through 2022-10-21, so blocks 1–5 were
  in-sample and block 6 was genuinely out of sample — the loss stands despite the
  advantage. The LightGBM arm is not adjudicable: its artifact was trained
  through 2023-11-30, covering every measured block, so its Sharpe 1.5–3.3 is
  in-sample fit. No `ETF_*` model version was activated, and the Phase 5
  ML-via-API surface is still not built.
- **Consolidated CPCV report.** `docs/reports/etf-rotation-cpcv-model-comparison-2026-10-25.md`
  puts all four arms (RULE p3, RULE p5, LightGBM, LSTM) in one place with a validity
  verdict per arm, the pre-registered criteria and their outcomes, and a cross-check
  against the registry's own holdout `deployment_note` records — which is what settles
  "the LightGBM numbers look great": that same artifact scored alpha −0.0141 against
  RULE's +0.1821 on data it never saw.
- The next live signal run will write shares and a `p3` book; until it runs, the
  ledger keeps rendering `--` for realised pnl, which is expected.
- Backend suite 650 -> 704. Frontend 77 -> 79.

### version 0.1.26

#### Added:

- **Live simulated ledger.** A new `mark_etf_rotation_positions_daily` command,
  Celery task and beat entry (20:15 on trading days, after the 19:45 signal)
  marks every OPEN leg at the newest close on or before the mark date. A leg the
  newest signal of its type no longer selects is closed at that bar with
  `exit_date`, `exit_price`, `pnl`, `return_pct`, `status=CLOSED` and
  `exit_reason='DISPLACED_BY_SIGNAL'`; `pnl` is
  `(exit − entry) × shares − both commissions`, or null when the leg has no
  shares (price return is still recorded). Neither bars nor signals after the
  mark date are ever read, so a backdated `--date` cannot use a future signal.
- **Carry-forward in signal regeneration.** A fund the new signal still selects
  is updated in place (weight and metadata) instead of being deleted and
  reopened; the summary and diagnostics gain `positions_carried`.
- **Live ledger card on `/etf`.** Realised PnL (summed from CLOSED rows alone),
  win rate, closed count, closed-trade details and recent signals; the positions
  table shows the latest price and unrealised PnL from `metadata.live_mark`.

#### Changed:

- `ETFRotationPosition` gains `exit_reason` and a `(status, exit_date)` index
  (migration `0008`). Neither is part of `to_parameters()`, so the resume
  fingerprint is unchanged.
- Regeneration now deletes only OPEN rows of the same `signal_type`; CLOSED
  history is preserved across signal regeneration.

#### Verified:

- Backend 645 → 650 tests; the five new ones pin displaced-leg close arithmetic,
  carry-forward, closed-history preservation, cumulative PnL from CLOSED rows
  alone, and the absence of any post-mark-date read. Frontend 76 → 77.
- `makemigrations --check` clean; frontend `tsc --noEmit` and lint/build
  baselines clean. No manual browser click-through was performed.
- The 12 OPEN position rows in the dev database — including the duplicates that
  pre-carry-forward regeneration produced and two stale 2018-12-28 rows — were
  deleted; the table is empty until the next signal runs. CLOSED history is
  untouched (there was none).

### version 0.1.25

#### Added:

- **RULE walk-forward, CLI-only.** `--grid --walk-forward-steps N` builds
  expanding folds over the session calendar, scores the full 144-configuration
  grid on each fold's training window only, selects by Sharpe (lower turnover
  breaks a tie), and trades those parameters in the following validation block.
  Nothing is persisted: `--backtest-id`, `--queue`, cost flags and non-RULE
  models are refused, and `--grid-plan` prints the plan without executing it.
  A short panel loses folds rather than gaining invented boundaries, and each
  fold's own rebalance count is printed because it is the reading floor.

#### Verified:

- 2016–2023, four folds: every fold has at least 20 out-of-sample rebalances
  (20/21/21/20). The reference configuration (row 256: BIWEEKLY / p3 / mom120 /
  absolute momentum off / equal) was selected in **0 of 4** folds; all four
  winners were MONTHLY + mom120, varying positions 3/5/8, absolute filter 2 on /
  2 off and weighting 3 equal / 1 score. The reference parameter set is therefore
  window-specific; **no default was changed**.
- N=6 was attempted first and abandoned under the pre-declared guardrail: fold 1
  produced only 14 out-of-sample rebalances.
- Full table and reading in plan §7.12.
- Backend 641 → 645 tests; frontend unchanged at 76.

### version 0.1.24

#### Added:

- **No-trade band at execution.** `drift_band_fraction` leaves a rebalance delta
  alone when it is below `max(0.5% of equity, fraction x target notional)`, so a
  book is not churned back to target over noise. Mandatory exits (`rotated_out`)
  are never suppressed, and `report['counters']['band_suppressed_deltas']` counts
  what the band caught. On the reference configuration a 2% band suppresses 49
  drift fills (364 → 323) and 41 commissions; a 1% band never binds because its
  threshold sits below the 0.5% minimum-trade floor. **Default 0.0: no curve
  changes.**
- **Capacity scaling at plan time.** `liquidity_mode='scale'` clips each leg to
  `adv_participation_cap` times trailing daily turnover instead of rejecting it;
  the freed weight is reallocated to the other legs up to their own caps before
  the defensive fallback, and a leg with no measurable turnover is still
  rejected. `report['capacity_scalings']` records every clipped leg.
  **Default `reject` / cap 0.0: the historical floor screen, unchanged.**
- **CLI flags** `--drift-band-fraction`, `--liquidity-mode`,
  `--adv-participation-cap`. `--grid` refuses them, since a sweep must share one
  execution model.

#### Changed:

- `BacktestConfig` grows three fields and `to_parameters()` records them, so the
  resume fingerprint changes; a PAUSED row written before this version restarts
  from scratch. Migration `0007` adds the three columns with defaults that
  reproduce every earlier run bit for bit.
- The defensive sleeve's idle-cash check is now `_idle_defensive_check`, shared
  by both liquidity modes; its rejection record, counters and cash fallback are
  unchanged.

#### Verified:

- Row 256 rerun under the new defaults: equity curve (1,926 points) and all 597
  fills byte-identical to the persisted row.
- Band evidence on the tuning window, both fee models, is in plan §7.11: 2% band
  is error-level (+0.19pp/+0.22pp return, +0.07pp/+0.04pp drawdown,
  +0.0005/+0.0006 Sharpe), so no default was changed.
- Backend 636 → 641 tests; frontend unchanged at 76.

### version 0.1.23

#### Added:

- **The backtest report explains its own turnover.** `report['turnover']` splits
  filled BUY+SELL notional by fill reason — `rebalance_up/down` is drift,
  `rotated_out` is a discretionary swap — and annualises each bucket exactly like
  `turnover_annual`. On the reference row it is 11.60x drift / 11.25x
  discretionary (22.85x total), so roughly half of the headline turnover is the
  book drifting rather than the selection layer trading.
- **One rebalance record per built plan.** `report['rebalances']` carries the
  selection layer's own `mode`, `swaps_used`, entries, exits, refills and
  displacements for every plan, including plans that filled nothing (213 on the
  reference row). This is the only place a "kept the book" decision is visible.
- **Sub-window facets.** `report['sub_windows']` cuts the persisted equity curve
  into three equal, non-overlapping blocks, each with return, max drawdown,
  Sharpe and turnover, so a full-window Sharpe cannot hide a dead regime. The
  reference run's third block (2021-05-14..2023-12-29) is -4.99% with 34.33%
  drawdown and 29.19x turnover; block 2 supplies almost all of the +99.69%.
- **The report names the model that scored the run.**
  `report['model_versions']` is `{'RULE': None}` for a rule run. ML loaders attach
  their `ModelVersion` id, version and type to the predictor callable; the scoring
  metadata carries that into the report, so it names the version that actually
  ranked the run instead of re-querying "whichever row is active now". ENSEMBLE
  records every source, and a skipped source is an explicit `None`.
- **Counters for the commission floor and the future no-trade band.**
  `min_commission_bound_fills` (113 on the reference run) and
  `band_suppressed_deltas` (0 until Phase 3) are in `report['counters']`.
- **The cost model is a CLI surface.** `--commission-rate`, `--min-commission`,
  `--slippage`, `--liquidity-floor-multiple` and
  `--min-trade-notional-fraction` run one configuration under an explicit cost
  model. `--grid` refuses them because every grid row must share the same model,
  and `--backtest-id` refuses an explicit value that disagrees with the row.
- **`min_trade_notional_fraction` is a column.** The 0.5% delta threshold was
  hardcoded in `_execute_plan`; it is now a `BacktestConfig` field, an
  `ETFRotationBacktest` column (migration `0006`, default 0.005) and a CLI flag.
  The default reproduces the old behaviour bit-for-bit; changing it changes the
  resume fingerprint, so a paused legacy row restarts from scratch.

#### Changed:

- **A closed leg's `position_fees_paid` now includes slippage.** It used to sum
  commissions only while `pnl` already carried the slipped execution price. On
  the reference row the 233 closed legs now report 168,901.10 (39,243.23
  commission + 129,657.86 slippage) instead of 39,243.23. `pnl`, cash and the
  equity curve are bit-identical; only the fee line moved. Rows written before
  this version keep the old field and are **not backfilled** — the old value never
  stored the split, so use the `total_fees` and `total_slippage_cost` metric
  columns for cross-version comparisons.

#### Verified:

- The reference configuration (row 256) reruns bit-identically: 1926 equity
  points and 597 fills with the same SHA-256 digests, 213 rebalances, 113
  floor-bound fills, commission 39,524.03 and slippage 130,592.93. The only
  changed stored field is the closed-leg fee line described above.
- Fee sensitivity on the same configuration: 万三/免五 versus 万一/不免五 is
  39,524.03 versus 13,805.52 commission, 113 versus 128 floor-bound fills,
  Sharpe 0.5357 versus 0.5547, total return +99.69% versus +105.88%. A
  single-window observation, not a reason to change a default.

### version 0.1.22

#### Added:

- **Every signal attempt leaves a row.** `ETFRotationSignalRun` (migration
  `0005`) is append-only evidence with `outcome ∈ OK|REFUSED|FAILED|FEED_STALE`,
  `reason_code`, a JSON `diagnostics` block, `task_id` and `duration_ms`. The
  command writes exactly one row per attempt; a refusal is persisted **and then
  re-raised**, so Celery still reports the task as FAILED while the next operator
  can see *which* input was empty. `diagnostics` names the newest snapshot, bar
  and benchmark dates, the benchmark/feature lag in sessions, the eligible row
  count and the null count of every feature the scorers need, so
  `no_eligible_rows` is no longer a sentence without a cause. `--dry-run` writes
  nothing, and a ledger failure is logged rather than allowed to mask the signal
  error.
- **A feed-health watchdog.** `check_etf_rotation_feed_health` runs at 17:45Z
  (before the 19:45Z signal) and compares the benchmark and the newest eligible
  feature snapshot against the newest `ETFDaily` bar. A lag writes one
  `HEALTH/FEED_STALE` row naming the lagging feed; a current feed writes nothing.
  A completely absent benchmark or feature snapshot is also stale rather than
  "lag zero", so a feed that never synced cannot pass the check. The row is
  self-clearing in the API: it is only reported while its date equals the newest
  bar, so yesterday's defect does not alarm forever.
- **Feed alerts reuse the alert plumbing.** `AlertRule.asset` and
  `AlertEvent.asset` are nullable, `condition_type` gains `FEED_STALE`, and
  `check_alert_rules` seeds an asset-less `ETF data feed health` rule whose
  channels come from settings. Dispatch, cooldown and the serializers'
  `asset_symbol`/`asset_name` are null-safe; the alert center names such events
  "data feed health" instead of rendering a null asset.
- **The freshness endpoint reports the attempt, not only the row.**
  `/etf/signals/freshness/` now returns `last_attempt` and `feed_stale`
  alongside `is_stale`. The `/etf` card warns on any non-`OK` attempt — a refused
  job that wrote no signal is visible — and separately on a defect at the newest
  bar. The defensive-asset dropdown blanks its 20-day turnover once the
  underlying snapshot is more than five sessions behind the newest bar.

#### Fixed:

- **The beat schedule's entry formatting.** The ETF block in
  `config/settings/base.py` carried malformed indentation around the new
  watchdog entry; it is rewritten so both ETF jobs are plain `crontab(...)`
  dictionaries and `manage.py check` can read the schedule.

### version 0.1.21

#### Added:

- **The `/etf` page says what data it is standing on.** `GET
  /api/v1/etf/signals/freshness/` (`apps/etf_rotation/views.py:463`) compares the
  newest signal's `signal_date` against the newest **eligible**
  `ETFFeatureSnapshot` and answers `lag_sessions` / `is_stale`; the signal card
  renders the as-of date, the row's `created_at` as "generated" and `updated_at`,
  and a warning banner the moment the pool has moved on without it. This is the
  answer to the question the page used to invite: the date on the card was never
  the model's generation date -- it is the trading session the features stop at,
  and generation is a separate timestamp. Unknown freshness now degrades to
  "unknown" rather than to a confident date.
- **`metadata.config` is a panel, not a caption.**
  `frontend/src/pages/EtfRotationPage.tsx:260` renders
  `Object.entries(metadata.config)`, so the rebalance frequency, position count,
  lookback, absolute-momentum gate, weighting mode and the two selection
  constraints appear from whatever the engine actually recorded, and a knob added
  later needs no frontend edit. The `RULE` chip is derived from the same metadata
  instead of being a hardcoded string, so the tag and the parameters cannot drift
  apart the way they had.
- **One fund per tracked index, and a cap per category**
  (`apps/etf_rotation/etf_universe.py` `track_index_key`, migration `0004`).
  `ETFUniverse.track_index_key` normalises the seven benchmark spellings TuShare
  uses, `dedup_track_index` (default on) keeps one wrapper per index and
  `max_per_category` (default 2) bounds a single sector, and the tie-break is now
  score → shares outstanding → `ts_code` rather than score → ticker string. The
  trigger was the 2026-10-20 book, in which four of five legs were bank ETFs and
  two had byte-identical scores.
- **份额 is in the database.** `pro.fund_share` feeds
  `shares_outstanding` / `share_date`, refreshed by `sync_etf_fund_shares` on
  Sunday 18:10Z on the `ops` queue, so the tie-break and any later capacity
  reasoning have a real number instead of a guess.
- **`invvol` weighting** (`rotation_engine.allocate_weights`): legs get
  `w_i ∝ 1/max(vol_i, ε)` clamped to `[0.5x, 1.5x]` of equal weight, and the mode
  degrades to `equal` with the reason written into `weight_method` when the
  volatility column is missing -- the same no-silent-zero rule `_rule_scores`
  already follows. The choices are in sync in all four places that carry them
  (`models.Weighting`, the serializer, the engine, the workbench form).
- **A fractional-Kelly exposure governor, off by default**
  (`apps/core/finance_metrics.py:payoff_ratio`, `kelly_fraction` / `kelly_cap` /
  `kelly_floor` / `kelly_lookback_trades`). It sizes the *whole book*, not each
  leg, from trades closed strictly before the session being sized, and publishes
  its state in `report['counters']['exposure_governor']` (win rate, payoff,
  `kelly_full`, mean scale, scaled sessions). `kelly_fraction=0` is a bit-identical
  no-op: the reference rerun with `--kelly-fraction 0` returned
  +99.69% / 35.41% / Sharpe 0.5357 / 22.8514x, byte-for-byte the reference row.
- **The defensive sleeve is validated and can earn interest.**
  `defensive_asset_problem()` rejects a sleeve with no bars over the requested
  window at create time (`400`), `GET /api/v1/etf/defensive_assets/` publishes the
  tradable list, `cash_yield_annual` accrues on uninvested cash and
  `defensive_sleeve_uninvested` counts the sessions it could not be placed. The
  four independent hardcoded `'511880.SH'` defaults (rotation config, backtest
  config, model column, workbench form) are collapsed onto
  `settings.ETF_DEFENSIVE_ASSET`, default unchanged.
- **A blind cross-section is a feed gap, not a signal**: `signals.py` raises
  `SignalGenerationError` rather than persisting an all-defensive plan computed
  over rows with nothing to rank on.
- **New production reference row: id 256**
  `grid-dedup-rule-biweekly-p3-mom120-noabs-equal` -- +99.69% total, 35.41%
  maxDD, Sharpe 0.5357, alpha +9.39%, 22.85x/yr turnover over
  2016-01-29..2023-12-31. It replaces row 82 (+18.21%, 80% banks). Full
  verification record in `docs/plans/etf-rotation-selection-sizing-2026-10-23.md`
  §7.

#### Changed:

- **`weighting` has one default.** The column, `BacktestConfig` and the CLI banner
  all say `score`; the banner used to print `equal(default)` while the engine ran
  `score`, which is exactly the kind of label that survives review because nobody
  checks the console against the config. Measured at the reference config the
  ordering is equal (+99.69% / Sharpe 0.5357) ≥ invvol (+81.86% / 35.24% maxDD /
  0.4961) ≥ score (+71.73% / 44.53% / 0.4437), so the choice of default is
  genuinely arguable and is logged in `BACKLOG.md` rather than decided silently.
- **Kelly, tried and left disabled.** The plan's rule was "if a positive fraction
  is indistinguishable from zero, ship it off and say so". It is distinguishable,
  but not in the intended direction: on the reference book the realised edge is
  **negative** -- 51 closed trades, win rate 43.1%, payoff ratio 1.29, so
  `f* = W - (1-W)/b = -0.009` and the raw scale floors at zero. At the default
  `kelly_floor=0` the governor is a kill switch, not a throttle: it delivers
  Sharpe 0.967 and 6.84% maxDD by holding beta 0.03 and turning 22.85x of turnover
  into 2.13x. Raising the floor makes it a throttle with a readable trade
  (floor 0.75: +86.32% / 26.55% / Sharpe 0.5878), but that is a tuning-window
  observation and the holdout was not spent on it, so the default stays 0 and the
  honest answer to "why isn't size tied to odds?" is that the strategy's own odds
  say the edge is not there yet.

#### Fixed:

- **The 2026-10-22 19:45Z signal job wrote no row.** `H00905.CSI` (the total-return
  benchmark) stopped at 10-21 while `000905.SH` had advanced, so
  `rs_vs_benchmark_60d` was null across all 488 eligible snapshots, every fund was
  excluded, and `build_rotation_plan` answered `no_scored_candidates`. The job had
  no log line to say so: `PYTHONUNBUFFERED` was unset and the restart script
  truncated rather than appended, so `tmp/cold_stack/beat.log` held no dispatch
  history. Both are fixed, and `docs/how-to/runbook-sync-failure.md` §2.3 / §5.1
  now carry the diagnosis and the three-command recovery.
- **`--no-dedup-track-index` had never worked**, and neither did the API path.
  The two selection knobs were passed as top-level `run_rotation_backtest()`
  arguments -- a `TypeError` on the first invocation -- while `tasks.py` and
  `--backtest-id` silently omitted them, so a row created with
  `dedup_track_index=false` reported a configuration its worker never ran. They
  now travel through `config_overrides` on every path, guarded by
  `SingleRunKwargForwardingTests`.
- **`bool(None)` was read as "off".** `--no-dedup-track-index` is a tri-state
  (`action='store_false', default=None`), and `bool(None) is False`, so *every*
  command-line run without the flag silently selected the dedup-off book: +55.61%
  / 47.72% / Sharpe 0.3788 for the same configuration whose grid row returned
  +99.69% / 35.41% / 0.5357. CLI and grid numbers for one config were not
  comparable, and every Phase-3 measurement taken that way was retaken on the
  fixed path (`docs/plans/…-2026-10-23.md` §7.5).

### version 0.1.20

#### Added:

- **ETF rotation has a page.** `GET /etf` renders `EtfRotationPage.tsx` --
  signal-of-the-day card (the date, model type, average turnover and the selected
  funds with their weights and scores), an open-positions card, a
  strategy-versus-benchmark chart and a universe tab with server pagination and an
  `is_rankable` filter. Before this, `frontend/src` contained zero ETF code and the
  only trace was a bare `ETF_LIGHTGBM` string on `/models`, so every question about
  the strategy required a shell and a `--grid` re-run. `/etf/signals/latest/`
  answers **404** when the daily job has produced nothing; `fetchEtfLatestSignal`
  resolves `null` rather than throwing, and the page renders the empty state, which
  is the difference between "no signal yet" and a red error banner. The universe
  browser is a tab rather than a fourth page.
- **`GET /api/v1/etf/backtests/{id}/comparison_curve/`**
  (`apps/etf_rotation/views.py`, built by the new `apps/etf_rotation/comparison.py`)
  returns the stock payload shape -- `{run, series, available_series_keys,
  message}` -- so `BacktestComparisonChart.tsx` is reused rather than copied. The
  benchmark series is **recomputed from `BenchmarkIndexDaily` and rescaled to the
  row's opening equity**, because `_comparison_bundle` derives `benchmark_equity`
  and never persists it: reading `report` would have returned scalars only, and
  nothing in the API exposed an index time series at all. Recompute is what makes
  the endpoint work for all 168 rows already in the table without rerunning them --
  verified against row 82, run months before this change: 641 points per series,
  `H00905.CSI` +32.68% total return against the strategy's +77.56%. Caching mirrors
  the stock side (`BACKTEST_PAYLOAD_CACHE_SECONDS`, terminal statuses only); the
  second request answered from cache. `?extra_ids=` overlays other runs, and both
  that and the stock component's repeated `?extra_compare_run_id=` spelling are
  accepted. Detail-only, never in a list serializer.
- **ETF-aware model monitoring.** `ModelMonitoringPage` maps the three
  `ETF_*` model types to labels and renders a card for them in which each row's
  `deployment_note` is the headline, not a tooltip. That note is the honest record
  of why all three are `READY` and `is_active=False` (LightGBM and ENSEMBLE lost
  all three configs on the holdout; LSTM passed two of three at beta 1.08--1.19);
  a bare enum string made the page look uninformed when it was actually reporting
  a decision. No backend work was needed -- `ModelVersionSerializer` already
  carries `metadata`.
- **`POST /api/v1/etf/backtests/` launches a RULE backtest and answers 202.**
  `ETFRotationBacktestViewSet` is create-enabled, and the order inside `create` is
  the load-bearing part: validate, then look for an in-flight row with the same
  `BacktestConfig.fingerprint()`, then check the concurrency cap, then persist,
  then enqueue under `transaction.on_commit`. A duplicate therefore returns **200
  with the existing row** and consumes no slot -- a client that retries a POST it
  already has running gets something it can act on rather than a 429 it cannot.
  Exceeding the cap raises 429 with the count, the cap and the three ways out
  (`ETF_MAX_CONCURRENT_BACKTESTS`, default 2). Four guardrails bound the surface,
  all server-side, because these endpoints are public on an internal network and
  the threat is an accidental loop rather than an attacker: ML is refused with a
  400 that names `model_type` (a backtest needs a registered, non-bleeding
  booster, which the API cannot create), the date range is sanity-checked,
  identical in-flight configs dedupe, and the cap bounds blast radius. DRF
  throttling was deleted with the auth module, so an application semaphore is what
  was available without reintroducing it repo-wide. `http_method_names` stays
  without PUT/PATCH: a launched row is immutable, and `rerun`/`restart` are how you
  change one's outcome instead of its config.
- **ETF learned the stock backtest's lifecycle.** `ETFRotationBacktest` had no
  `PAUSED` status at all, so one migration adds it plus `pending_control_action`
  (NONE/PAUSE/RESTART/DELETE), `current_task_id` and **`weighting` promoted to a
  real column**, data-migrated out of `parameters['weighting']` (168 rows → 85
  `score` / 83 `equal`). The backfill uses `QuerySet.update()` deliberately:
  `updated_at` is `auto_now`, and a row-by-row save would have stamped every ETF
  backtest as just-modified and made the workbench's "last touched" column
  meaningless for the table's whole history. `backtest_engine` now reads
  `pending_control_action` at the 120-session chunk boundary it already persisted
  `runtime_state` on: PAUSE stores state, sets PAUSED and returns without
  finishing; RESTART discards `runtime_state` and starts clean; DELETE removes the
  row. `pause` on PENDING or a dead task owner sets PAUSED immediately, `pause` on
  RUNNING returns 202 and waits for the boundary, and `resume` is legal only from
  PAUSED. `get_backtest_run_task_owner_state` moved from `apps/backtest` to a new
  **`apps/core/task_health.py`** with a status-agnostic comparison -- on ETF the
  old code worked only because both enums happen to spell `'RUNNING'` the same
  way, and two consumers is the point to extract.
- **`/etf/workbench` (`EtfBacktestWorkbenchPage.tsx`) drives that lifecycle.** It
  is the first page to pass a **server-side** `sort=` param (`alpha`, `sharpe`,
  `return`, `drawdown`, `created`), so `useApiResource` is re-keyed on it, and
  `drawdown` sorts
  ascending because a smaller drawdown is the better row. Progress comes from
  `report.runtime_state.cursor`, which is **detail-only** -- the list serializer
  drops `equity_curve`/`trade_log`/`report`, so the panel polls the retrieve key
  every 5s while the row is non-terminal and stops otherwise. Lifecycle buttons
  are gated on `status` + `pending_control_action` under the same conditions the
  server enforces, so a disabled button and a 400 are not both reachable.
  Eight page tests, forty-nine backend tests (18 create guardrails, 15 lifecycle
  transitions, 7 chunk-boundary controls, 9 task lifecycle); CI floors rose to
  **514** backend and **66** frontend. `docs/reference/api.md` is hand-edited --
  it claimed the ETF routes are read-only, which this falsifies.
  **The lifecycle was then driven over real HTTP against the real worker, not only
  in tests.** Twenty-one checks pass across two runs; the single early failure was
  the script's own sequencing -- it re-POSTed a config the cap had already refused,
  so there was no row to dedupe against -- and it was rewritten as the adjacent
  cap probe described below. A POST answered 202 (`id=179`,
  PENDING) and the identical payload answered 200 with the same id; the cursor
  reached `120/2056 sessions` with `chunked run in progress: 120/2056 sessions`;
  `resume` on a PENDING row was rejected 400 ("Only paused ETF backtests can be
  resumed"); `pause` on a RUNNING row returned 202 "will pause after the current
  chunk" and the row landed in PAUSED at exactly `360/2056`, the chunk boundary,
  with the pause note persisted. `resume` then re-queued and the cursor ran 360 →
  600 -- **it continued rather than restarting**, which is the whole point: a
  restart would have shown 120 again -- and the equity curve grew 360 → 600 points
  through the pause. The resumed row finished COMPLETED with real metrics (alpha
  0.0441, beta 0.5704, 2056 points, 46s end to end). With the consumer parked, a
  distinct config was rejected 429 at the cap while a same-fingerprint POST in the
  same second returned 200 with its existing row, which is the dedupe-before-cap
  ordering observed rather than asserted; `pause` on PENDING became PAUSED
  immediately (202 "ETF backtest paused."), DELETE of a PAUSED row was 204 then
  404, and DELETE of a RUNNING row was the 202 intent ("It will be removed after
  the current chunk.") with the row gone at the next boundary. Three things worth
  knowing from doing it: the deterministic PENDING windows came from `celery
  control cancel_consumer/add_consumer etf-rotation`, which parks a queue without
  touching any process; the fingerprint covers **parameters, not `name`**, so two
  differently-named rows with identical parameters are the same run and the second
  POST gets the first row back; and a running sweep keeps one `tune-lstm-*` PENDING
  placeholder in flight, so those rows occupy slots in the cap and the workbench
  answers 429 until the sweep ends. That is the cap doing its stated job -- binding
  accidental load -- and it is self-clearing, but an operator should expect it.
- **`manage.py sweep_etf_rotation_lstm` is the tuning surface, and it is honest by
  construction.** One command runs a bounded, resumable 32-trial grid (2
  `hidden_size` x 2 `num_layers` x 2 `dropout` x 2 `loss` x 2
  (`learning_rate`, `epochs`)) through `train_walk_forward`'s keyword-only seam, on
  the tuning window ending **2023-12-31**, and the locked holdout is never read: an
  `--end` that reaches into 2024-01-01+ is refused unless `--allow-holdout-overlap`
  is passed, which exists for the operator and is not a default. Resumability is
  `get_or_create` on the trial name plus a fingerprint that covers the grid
  *and* the resolved window *and* every protocol knob, so a COMPLETED row is reused
  only when it is comparable; change `--folds` or `--sequence-length` and the stored
  digest no longer matches, the trial re-runs, and the command says so. A trial that
  raises or returns `insufficient_data` is marked FAILED and the sweep continues --
  one bad fold plan must not cost the other thirty-one.
  Contamination is closed at three points. Every ledger row is `tune-lstm-`prefixed
  and carries `report['tuning_only']` with the per-fold rank ICs and **no portfolio
  metrics at all**, because a mean rank IC is not alpha and the workbench must not be
  able to show it as one. Every trial registers with `activate=False` (Phase 0) and
  `sweep={...}`, and `train_lstm` now writes `metadata['pooled_holdout']` only when
  `sweep is None` -- so a sweep result cannot be read as out-of-sample evidence, and
  `_stamp_winner` raises if that ever stops being true. Then the command checks its
  own work: it snapshots the active `ETF_LSTM` set before training and raises
  `CommandError` if the set differs afterwards, on the success path and the
  mid-sweep-failure path alike. `_select_winner` ranks by mean fold rank IC then
  positive-fold count and prints the Phase D single-shot retrain command, with the
  training window stopped at the holdout boundary -- the overlap that produced the
  +513.76% row that had to be relabelled `INSAMPLE-`.
  **Two deliberate deviations from the plan.** The grid's fourth axis is
  `loss ∈ {bce_top_bucket, pairwise_rank}`, not `objective ∈ {binary, lambdarank}`:
  those are LightGBM's objective names, and `train_lstm` has no such parameter -- a
  test asserts the axis equals `train_lstm.LOSS_BCE`/`LOSS_PAIRWISE` so the two
  cannot drift apart silently. And the trials run **sequentially in-process**, not
  "through the `train-lstm` queue": a sweep needs to see each result before deciding
  the next, to hold one torch import rather than thirty-two, and to own its
  cancellation; the queue's 1-2-slot VRAM ceiling from Phase 0 was the reason for the
  sequential *schedule*, which in-process iteration satisfies without thirty-two
  serialized task payloads. A test asserts `_run_sweep` contains no `apply_async`,
  `delay(`, `queue_etf`, `send_task` or `chord`, so nobody reintroduces the dispatch
  without noticing. `--plan` and `--dry-run` read the grid and the panel without
  importing torch or writing a row, which is what CI can run. Thirty-five backend
  tests; the backend floor rose to **549**.
  **Measured on the WSL2 venv, not only asserted in tests.** A `--limit 2 --folds 2`
  smoke run wrote rows 174 and 175 in 1.7 min. Both carry the `tune-lstm-` prefix,
  `report['tuning_only']` with the tuning window and per-fold ICs, a `note` that says
  outright that no portfolio simulation ran, and **every metric column NULL** with an
  empty `equity_curve` and `trade_log` -- so the workbench cannot draw a curve that
  was never simulated. `ModelVersion` rows 66 and 67 came back `READY` with
  `is_active=False`, `metadata['sweep']` populated (`campaign`, `fingerprint`,
  `protocol`, `grid`, `tuning_window`, `per_trial_ic`, `selected_config`,
  `activated: False`) and no `pooled_holdout` key, which remained absent from every
  sweep row while still present on the four pre-existing oos/live rows. The active
  `ModelVersion` count was 5 before and 5 after, none of them ETF, and the command's
  own guard printed `activation guard: OK -- the active ETF_LSTM set is unchanged
  ((none))`. Then the full 32-trial x 6-fold run printed, on its first trial, `stored
  fingerprint fd4deb2197916c3b does not match cf1276f13897866f; the window or protocol
  changed, so this trial is re-run` -- the resumability rule is not merely covered by
  a test, it correctly refused to reuse the smoke run's rows for a different fold
  count.
  **The full 32-trial run: 32/32 trials produced results in 48.6 min, 0 reused from
  persisted rows, 0 that produced nothing.** Selected
  `tune-lstm-h64-l1-d020-pair-lr1e-3-e50` (row 176): mean fold rank IC **0.0650**,
  ICIR 0.1996, **5/6** folds positive, per-fold ICs
  `[0.134, 0.040, -0.082, 0.043, 0.142, 0.113]` -> `ModelVersion` 70
  `etf-lstm-2023-12-29-t-1becb8db`, `is_active=False` and stamped with the full
  32-trial provenance. Runner-up row 191 (`h64-l2-d035-pair`) is close: 0.0626 with a
  marginally *better* ICIR of 0.2017.
  **One trade-off the selection rule makes, stated plainly because the data does not
  settle it.** Row 184 (`h64-l2-d020-bce`) is the only config in the grid that is
  positive in **all 6** folds, yet it ranks 22nd: its mean IC is 0.0424, i.e.
  consistent but weak. `_select_winner` ranks by mean IC *then* positive-fold count,
  so breadth of signal beats consistency of sign. That is the plan's rule, and the
  Phase D gate asked for `>=5/6` folds -- not 6/6 -- so both candidates clear the fold
  leg and only the mean IC distinguishes them. A future sweep should consider a
  second-order tie-break on ICIR, which would have picked row 191.
  After the run, all **14** contamination checks re-passed on the full 32-row ledger:
  every row `tune-lstm-`prefixed with `report['tuning_only']`, **zero** non-null metric
  columns, empty `equity_curve` and `trade_log`, 34 `metadata['sweep']` `ModelVersion`
  rows all inactive and none carrying `pooled_holdout`, the `-t-` tag appearing exactly
  where `metadata['sweep']` does, the active ETF set empty while the 5 stock models
  (ids 55-59) stayed untouched, one single `training_window_end` of 2023-12-29 across
  the whole sweep, and no sweep row scored into the holdout window.

- **Phase D: the locked holdout was spent once, and the LSTM failed the gate.** The
  sweep-selected config was retrained as one shot over 2016-01-29..2023-12-31
  (`etf-lstm-2023-12-29-oos-lstm-v2`, `ModelVersion` 100, `activate=False`, 95 s, and
  it reproduced the sweep's tuning numbers exactly -- mean rank IC 0.06503 vs 0.0650,
  5/6 folds positive -- which is the replay determinism you want before spending an
  unrecoverable window). Then one backtest, `oos-lstm-v2-p3-mom20-monthly-abson-score`
  (row 209), over **2024-01-01..2026-10-20** with the portfolio rules held identical to
  the RULE reference on all ten config fields, checked before the run rather than
  assumed. Against RULE row 82: alpha **0.0888 vs 0.1821**, max drawdown **0.4567 vs
  0.2000**, beta **1.1038 vs 0.6070**, IR 0.4366 vs 0.4580, Sharpe 0.6685 vs 0.9080.
  Three gate legs fail, one passes (5/6 folds). `PHASE D GATE: FAIL` -> RULE stays
  production, the candidate is deactivated, and `deployment_note` plus `phase_d_gate`
  carry the numbers into `ModelVersion` 100's metadata, which `/models` renders.
  **The plan's gate bands did not contain the reference row's own values, so they were
  widened before scoring rather than after.** The plan specifies maxDD 0.18-0.20 and
  beta 0.61-0.69; row 82's actuals are 0.19998 and 0.60704, which sit *outside* those
  ranges. A literal reading would fail a model for matching RULE more closely than
  RULE matches the band. The gate therefore anchored each band on the reference +-0.01
  (0.170-0.210, 0.597-0.700) -- a fairness fix, not a softening: the widened beta band
  still rejected 1.1038 by a mile. Recorded here because a reviewer comparing the
  driver to the plan text should find the difference intentional.
  **Two caveats that travel with these numbers.** The serving artifact is the *last*
  fold's model, which trains through about 2022-10-21 rather than 2023-12-31
  (`train_lstm.py:1454` takes `payloads[-1]`) -- conservative, not leaky, but it means
  the deployed model saw less data than the aggregate metrics imply. And RULE's +18.21%
  was itself selected on this same holdout, so it is mildly optimistic in RULE's
  favour; that asymmetry is the reason Phase D is one shot, not a search.
  **The restore path is proven, not asserted.** The driver ran the temporary activation
  inside a `try` with an emergency deactivate in `finally`, and its last act is to
  re-read the authoritative active sets from the database rather than trust its own
  bookkeeping: it finished with `authoritative active ETF rows now: []` and the same 5
  stock rows as before. This mattered on the first attempt, which died in a `NameError`
  before any activation -- `json.dumps(None)` emits JSON `null`, which is not Python,
  so the replayed `horizon_days`/`min_train_days` arrived as bare `null` tokens. That
  abort was contained (no ETF row went live, the locked holdout stayed unspent), and the
  fix came with two durable artifacts: a helper that renders *Python* literals, and a
  separate render check that `compile()`s the generated shell source before GPU time is
  spent. The step-1 guards were also moved inside the `try`, because the
  "candidate registered active despite `activate=False`" abort -- the Phase 0
  regression guard -- sat outside it and would have escaped the emergency restore.
  Fallback deliverable stands as planned: Phases 0, A, B and C are the product here,
  not a consolation prize.

#### Changed:

- **A trainer can now register without activating.** `_register_model_version`
  gained `activate: bool = True` in both `train_lstm.py:1079` and
  `train_lightgbm.py:650`, threaded through each `train_walk_forward`. Until then
  it hardcoded `'is_active': True` and flipped every prior row of that type
  inactive, so the last trial of any hyperparameter sweep became the model
  `rotation_engine.load_active_predictor` serves -- regardless of its rank IC.
  That is precisely the contamination the `INSAMPLE-` relabelling (rows 153--161)
  cleaned up, and it was one argument away from happening again. `activate=False`
  writes the row inactive and touches no other row, which is now asserted by a
  test rather than reasoned about. This is a prerequisite for Phase C, not a
  follow-up to it.
- **The local stack pins the train worker to 2 slots and refuses to start a
  second beat.** `run_celery_worker.sh` leaves `CELERY_WORKER_CONCURRENCY` unset,
  so the train queue was getting 16 prefork slots (`nproc`) for jobs whose real
  ceiling is 12 GB of VRAM -- a second concurrent LSTM needs its own memory, so
  this is a hardware limit that GPU availability does not lift. `run_local_stack.sh`
  now passes `STACK_TRAIN_CONCURRENCY` (default 2) to that one service. Separately,
  this script had been run twice while the first stack was still up, producing two
  schedulers sharing `celerybeat.pid` and therefore **double-dispatching every
  entry in `CELERY_BEAT_SCHEDULE`** -- not just ETF ones. A pidfile check cannot
  catch it because `run_celery_beat.sh` deletes the pidfile on start, so a newer
  beat clears the older one's entry and both then look stopped; the guard matches
  the live command line (`pgrep -f '[-]A config\.celery beat'`) and refuses the
  start, with `STACK_SKIP_BEAT=1` for when a scheduler is deliberately running
  elsewhere. `--check` is unaffected: a live beat only blocks a real start. Both
  guards were then **relocated to the scripts that actually start the processes**
  -- `run_celery_beat.sh` and `run_celery_worker.sh` -- because a guard that lives
  in one caller is not a guard: `restart_cold_stack.sh` starts beat and the
  workers directly and walked straight past them.
- **Two things the cold-restart script does not do, both found verifying this
  lifecycle.** `restart_cold_stack.sh` never runs `migrate`, so a schema change
  can be green across 514 tests -- Django's runner builds its own database -- and
  still 500 in production. It did exactly that: `GET /etf/backtests/?page_size=1`
  returned `ProgrammingError: column ...weighting does not exist` until
  `0003_etfrotationbacktest_lifecycle_and_weighting` was applied. Probing with
  `?status=` hid it, because `count` compiles to `COUNT(*)` and an empty page never
  reaches the serializer. Separately, its stop sequence cannot stop a wedged beat:
  the old scheduler survived `kill -TERM` and the 30-iteration drain loop because
  SIGTERM is *caught* and the process was blocked in a futex with 32 threads, so
  the script reported a clean stop and restarted beside it -- which is how the
  double beat was born in the first place. Not fixed here; the beat guard is what
  turns it from silent double-dispatch into a refusal.
- **The lifecycle was verified against the running stack, not only in tests.**
  A 2,586-session RULE run (22 chunks) was created via `POST` (202), and the same
  fingerprint re-POSTed while it was in flight returned **200 with the same id**;
  the third distinct run was refused with **429** quoting the cap; `model_type`
  `LSTM` was refused with a 400 naming the field. `pause` on the running row landed
  at the chunk boundary within 2s (`paused by request at 120/2586 sessions`), and
  `resume` continued to **960** rather than restarting -- the persisted cursor is
  what the queue picks up, not a fresh run. `resume` on a RUNNING row and on a
  COMPLETED one both 400; `DELETE` on a RUNNING row returned 202 with the removal
  intent and the row was gone at the next boundary. `?weighting=` filtered the
  promoted column (83 `equal` / 85 `score`) and `sort=alpha` / `sort=drawdown`
  ordered server-side. One branch is test-only: pausing a row that is still
  PENDING, because on a live worker the queue took the row into RUNNING inside the
  round trip.
- **The page-load harnesses had to learn that `/etf` exists.** Adding a ninth
  routed page broke `apps/core/tests_frontend.PageLoadLatencyHarnessTests` in three
  places -- group count, an unmeasured route, and the browser harness's route list
  -- which is the intended behaviour of tests that derive coverage from `App.tsx`
  instead of restating it. Both scripts and `EXPECTED_GROUPS` now carry ETF, with
  the counts de-counted in prose so the comments cannot go stale the next time a
  page lands. Measured: ETF's four calls sum to 0.571s p50 with a 0.197s max p95,
  and a cold one-iteration browser pass over all nine routes put `/etf` at **649 ms
  (stamped, not a shell fallback)** against the 3.0s budget, worst page overall
  `/indicator-board` at 1597 ms; `FRONTEND_OVERHEAD_SECONDS` is now 0.588 rather
  than the 0.000 the previous release had to caveat.
- **`frontend/src/test/setup.ts` stubs `ResizeObserver`.** recharts'
  `ResponsiveContainer` reads it during effect, jsdom has none, and an exception
  there unmounts the whole tree -- so every chart test failed with an empty
  document rather than a chart assertion. The no-op is test-environment plumbing
  only.

### version 0.1.19

#### Changed:

- **A page-load request may read stored rows and Redis. It may not run model
  inference.** This is now the contract, not an aspiration, and it is the whole
  of why the cold numbers below moved. `GET /dashboard/stocks/` ranked its
  candidate list by *computing* predictions for the universe when rows were
  missing -- 45.6s measured for a cold Dashboard -- because the cache that hid
  that cost was `_MATRIX_SIGNAL_CACHE`, a process-local `OrderedDict` in
  `apps/backtest/tasks.py` that no external invalidation can reach and a worker
  cannot warm. Candidate ranking now reads `LightGBMPrediction` /
  `PredictionResult` and applies `candidate_mode`, `trade_score_scope`,
  `up_threshold`, `top_n_metric`, `max_positions` and the macro multiplier
  itself, mirroring `_pick_candidates` semantics. `apps/backtest/tasks.py` is
  untouched: backtests keep exact behaviour, and the matrix cache stays
  process-local and backtest-only on purpose -- sharing a universe-wide mapping
  through Redis would buy a large payload and an invalidation surface for nothing.
- **The three prediction stock endpoints queue instead of inferring.**
  `apps/prediction/views.py`, `views_lightgbm.py` and `views_lstm.py` each had
  `if not qs.exists(): generate_..._for_asset(...)` inline -- a cold Stock Detail
  paid for a model pass before answering. They now return **HTTP 200** with
  stored rows if present, else empty plus
  `generation: {'status': 'queued' | 'unavailable', 'scope': ..., 'already_queued': ...}`,
  dispatching the task under a Redis `SET NX` lock
  (`PREDICTION_GENERATION_LOCK_SECONDS`, 120) so a polling SPA cannot stack
  universe-sized jobs. A broker that is unreachable is caught, logged and
  reported as `unavailable` -- it does not fail the page. `StockDetailPage`
  renders a one-line "generating" notice in both locales and re-reads the two
  prediction endpoints until rows land, which is what makes "will appear
  shortly" true (see the refresh entry under Fixed).
- **`PREDICTION_RECONCILE_MAX_ASSETS_PER_RUN` 20 → 250.** Twenty assets per
  hourly run repairs 480 rows a day against an effective universe in the
  thousands, so gaps persisted for days and any read-stored-rows path quietly
  fell back to computing them. Reading stored rows is only a contract if the
  rows exist.
- **The dashboard payload reports coverage instead of hiding it.** A date with
  120 of 500 universes stored now returns the partial rows plus
  `prediction_coverage: {missing: n, prediction_coverage_incomplete: true}` and
  queues one `reconcile_prediction_coverage` pass, rather than spending 45
  seconds making the difference invisible.
- **`staticfiles/` is build output, so it is no longer source-controlled.** The 170
  tracked files were one past `collectstatic` run's admin and DRF statics, while the
  SPA's own hashed bundle was never committed -- a fresh clone already had no
  JavaScript to serve. The multi-stage Dockerfile builds `frontend/dist` in a node
  stage and `entrypoint.sh` runs `collectstatic`, so nothing ever deployed from the
  tracked copy. `/staticfiles/` is now gitignored and those files untracked, left on
  disk; regenerate with `npm run build` then `python manage.py collectstatic`.

#### Fixed:

- **The Indicator Board's indicator lookup left its index.** The per-asset
  technical-indicator query used `timestamp__date__lte`, a function-wrapped
  predicate Postgres cannot satisfy with the
  `(asset, indicator_type, timestamp)` index, so the `DISTINCT ON` sorted every
  historical indicator row for every asset on the page: 151.7s measured for a
  500-asset universe, and the whole cold-page timeout. It is now a plain
  timestamp range bounded by `DASHBOARD_INDICATOR_LOOKBACK_DAYS` (default 60).
  The window was chosen from measured staleness rather than guessed -- 14 days
  drops 5 of 2000 `(asset, indicator_type)` pairs, 30 days drops 4, 60 days
  drops none, so the window is indistinguishable from unbounded while staying
  inside the index. An asset whose backfill is older than the window reads as
  absent rather than as months stale.
- **A page-load payload assembled the whole universe before truncating.** Every
  per-asset lookup is a `DISTINCT ON` keyed on `asset_id`, so its cost is the number
  of ids handed to it; the unfiltered board materialized 500 assets to return 120.
  The page slice now happens in SQL when the row filters and the ordering can be
  answered there (no candidate filter, no `search`, no `suggested_only`, ordering on
  `composite_score`), cutting cold Indicator Board from 2.615s (`cold-all`) to 1.184s
  (`cold-head2`). The same
  fix applied to `/screener/bottom-candidates/`, whose default ordering is already
  the queryset's own, and to its macro-context branch, which had been left sorting
  in Python: the three weights are scalars, so the re-score is now one annotated
  expression ordered and sliced in SQL, and
  `Phase11FactorTests.test_bottom_candidates_macro_context_ranks_by_the_sql_weighted_sum`
  pins that the annotated figure still equals the same sum done in `Decimal`.
- **`docs/reference/env.md` no longer documents a setting that does not exist**
  (`DASHBOARD_MATRIX_SIGNAL_SCOPE`, removed with the request-time candidate
  inference it opted into).
- **The time-to-data instrument latched early and under-reported the wait.**
  `stampView` marked a view stamped at its first quiet moment, so a chained
  resource -- the stock page cannot ask for predictions until the asset request
  has returned its id -- was never waited for, and the page reported as ready
  while it was still filling in. A view now reopens when a key mounts after the
  stamp, which is the difference between a budget that can be trusted and one
  biased toward passing.
- **Two of the eight routes had no time-to-data at all.** `StockDetailPage` and
  `BacktestWorkbenchPage` compose their requests with `cachedGet` directly --
  status-specific error copy and a live progress feed -- so nothing registered
  them with the view, `data-tti` never appeared, and the browser pass could only
  report "no measurement" for the two heaviest pages. `useApiResource.ts` now
  exports the registering half of the hook as `trackViewResource`, both pages use
  it, and `PageLoadLatencyHarnessTests` fails if any page that reads
  `frontend/src/lib/api.ts` stops registering. The probe's `settle` rule would
  have hidden this behind an approximation; the guard is what keeps it visible.
- **The page promised a queued generation and never delivered it.** "Generation is
  queued and will appear shortly" is in both locales, and the endpoints honour it --
  200 with `generation.status='queued'` instead of a synchronous model run -- but
  nothing re-read the rows, so "shortly" meant "on the next navigation". The task
  writes rows; it does not push them. `StockDetailPage` now polls the two prediction
  endpoints every 5s while a queued payload has nothing to show, merges whatever
  landed back into the same composed cache entry the page reads from, and stops the
  moment rows arrive or after twelve ticks -- a cold sweep that outruns a minute
  should leave the notice up, not a background loop on the page. An `unavailable`
  queue is deliberately not polled: that status means the broker is down, and
  re-asking is not the fix.
- **`useApiResource` could lose the one number the browser budget is read from.**
  It called `performance.measure` before writing `document.body.dataset`, so an
  engine without UserTiming -- jsdom, which ships no `mark`/`measure` at all, or
  any engine that throws on the options form -- took the record down with the
  mark. The attributes are now written first and on their own, and
  `frontend/src/hooks/useApiResource.test.tsx` pins both halves.

#### Added:

- **A cold page-load measurement harness.** `scripts/measure_page_api_latency.sh`
  covers all eight routed pages with the request shapes the SPA actually sends,
  and `COLD=1` restarts the stack through `scripts/restart_cold_stack.sh` --
  Django, beat and all five worker pools, with Redis emptied through Django's
  `cache.clear()` so the `REDIS_URL` password never lands on a command line. It
  reports `sum(p50)` (total server work) and `max(p95)` (the realistic critical
  path, because a page fires its calls concurrently) against a 3.0s budget, and
  states rather than hides that at two or three iterations an interpolated p95 is
  the maximum.
- **A real-browser time-to-data pass.**
  `scripts/measure_browser_time_to_data.sh` plus
  `scripts/browser_time_to_data.mjs` load each of the 8 routes in a fresh Chrome
  profile against the production build and read the stamps the app writes, which
  is the term `curl` cannot see. It runs the browser that is already installed on
  the Windows host through WSL interop rather than adding Playwright: Chrome's
  DevTools port is unreachable from WSL in NAT networking and
  `--virtual-time-budget` distorts the `performance.now()` deltas being measured,
  so the probe proxies the prod build through a one-page server, injects an
  observer, and holds an `async` subresource open so the load event waits for the
  stamp. Without a discoverable browser the script exits rather than reporting an
  assumed overhead. Its output is the number
  `FRONTEND_OVERHEAD_SECONDS` in the API harness is filled from.
- **Two fallback rules in the probe, gated so they can only ever be late.** If no
  `data-tti` appears, the probe reports the end of the first `/api/` burst from
  Resource Timing (`settle`), or first paint for a page that asks for nothing
  (`shell`, which is Settings by design). Both add the settle window the probe
  actually waited, so a fallback figure can be late but never early -- and two
  guards hold that promise, because firing one early is the only way this harness
  could report a page faster than it is. Nothing may be in flight: the first warm
  run labelled `/indicator-board` `shell` while its one request was still open. And
  a fallback is only taken on a route the wrapper declares makes no page-load
  request -- on a route that does, a fallback cannot be told apart from a page whose
  first request had not started yet, so it waits for the deadline, exits non-zero,
  and the wrapper re-runs the iteration once (resetting the stack first in cold
  mode, since an attempt that navigated before hanging had already warmed Redis).
  A route with no number is reported as **no measurement** and fails the run rather
  than passing on a mixture of two probes' fields.
- **Browser-side timing hooks.** `lib/queryCache.ts` emits a
  `performance.measure` per resolve source, and `useApiResource` stamps
  `data-tti-open` (navigationStart to the view opening: bundle, route chunk and
  pre-fetch render), `data-tti` (that instant to the last first value, request
  included) and `data-tti-path` on `<body>`, plus a `page-data-ready` measure.
  Splitting the two intervals is what makes the browser term subtractable from an
  API number instead of billing the same round trip twice.
- **Page-load budget guards in CI** (`.github/workflows/ci.yml`, "Page-load
  budget guards", run ahead of the full suite because they take seconds): no
  request-time inference in `DashboardStocksCacheTests` /
  `PredictionEndpointInferenceGuardTests`, and `PageLoadLatencyHarnessTests`,
  which derives the harness's required coverage from `frontend/src` -- every
  route in `App.tsx` in both harnesses, every page-load endpoint in the curl one,
  every page that reads the API registered with the instrument -- so a new
  page-load call or an uninstrumented page fails CI instead of going unmeasured.
  The harness had already gone stale for a whole release: it kept timing the
  seven calls of `fetchDashboardDataLegacy` after the SPA moved to one summary
  endpoint.

#### Result: every page under 3s on a cold stack

`ITERATIONS=3 COLD=1 FRONTEND_OVERHEAD_SECONDS=0.583 LABEL=cold-head2`. `max(p95)` is
the API critical path; `+browser` charges it with the browser term measured in a real
browser against the production build, which is the quantity the budget is stated
in. 66 calls, 66 HTTP 200.

| Page | Cold before | Cold after (p95) | +browser | Warm (p95) |
| --- | --- | --- | --- | --- |
| Dashboard | 46.3s | 0.500s | 1.083s | 0.315s |
| Indicator Board | timed out (>151.7s in one query) | 1.184s | 1.767s | 0.192s |
| Stock Detail | inferred on missing rows | 0.393s | 0.976s | 0.401s |
| Macro Context | not measured | 0.169s | 0.752s | 0.125s |
| Models | not measured | 0.216s | 0.799s | 0.157s |
| Backtest | not measured | 0.397s | 0.980s | 0.324s |
| Alert Center | not measured | 0.115s | 0.698s | 0.073s |
| Settings | not measured | 0.048s | 0.631s | 0.086s |

**All eight PASS against 3.0s, and independently so in the browser itself**: the
cold pass over the eight routes -- two iterations each, full stack restart, fresh
Chrome profile, prod build, sixteen probes all of which stamped or were labelled as
Settings' shell -- put the worst page (`/indicator-board`) at **1652 ms**, so the
tightest margin is 1.35s of headroom rather than the 1.82s an API-only figure would
have claimed. Each cold API figure is three iterations of a restarted stack, and at
n=3 an interpolated p95 is the maximum. Full tables in
`docs/reports/page-load-latency-cold-2026-10-22.md` and
`docs/reports/page-load-latency-browser-2026-10-22.md`.

### version 0.1.18

#### Added:

- **ETF rotation module** (`apps/etf_rotation/`, ~10,800 lines) — a standalone
  cross-sectional rotation pipeline for exchange-traded funds. It deliberately
  does **not** reuse the `apps/prediction` stock pipeline: ETFs are not in
  `Asset`, and putting them there would have polluted the CSI 500 fan-out in
  `apps/markets/tasks.py:sync_daily_a_shares`. Six models — `ETFUniverse`,
  `ETFDaily`, `ETFFeatureSnapshot`, `ETFRotationSignal`,
  `ETFRotationPosition`, `ETFRotationBacktest` — plus `etf_universe.py`
  (classification + point-in-time pool), `features.py`, `walk_forward.py`,
  `train_lightgbm.py`, `train_lstm.py`, `rotation_engine.py`,
  `backtest_engine.py`, `signals.py`, `tasks.py`.
- **Ingestion by trade date, not by fund.** TuShare's `fund_daily(trade_date=)`
  returns the whole market in one call (~2,139 rows, 0.16 s), so the primary
  path is `sync_etf_daily_by_date()`; per-`ts_code` syncing survives only as a
  gap-filler. Backfill 2016-01-04 → 2026-10-20 wrote **2,606,858 `ETFDaily`
  rows** across 2,586 sessions and 2,727 funds. `sync_etf_universe` never
  filters on `status`, so delisted funds keep their history and the pool can be
  reconstructed point-in-time (25 of 40 sampled delisted funds still carry bars)
  — survivorship bias is designed out rather than adjusted for later.
- **Unit conversion at ingest.** `fund_daily` publishes `vol` in 手 and
  `amount` in 千元; both are converted on the way in (×100 → 份, ×1000 → 元) so
  the stored identity `amount / (volume / 100) ≈ close` holds (4.2168 vs 4.22 on
  510300.SH, 0.077%). `TuShareDailyIngestUnitTests` pins this permanently — a
  silent 1000× error here would have made the 50,000,000 元 liquidity floor
  either admit every fund or none.
- **Features** (`features.py`): 24 whitelisted columns — momentum 5/10/20/60/
  120/250d, momentum acceleration, volatility 20/60d, Sharpe, relative strength
  vs the total-return benchmark, cross-sectional `rank_pct`, RSI-14, MA ratios,
  Bollinger position, 20d average amount, volume ratio, drawdown from high,
  60d correlation. TA-Lib is called directly on DataFrames rather than through
  `apps/analytics/tasks.py`, whose wrappers are `asset_id`-bound. Cross-sectional
  ranks use only the day's eligible pool, and labels
  (`fwd_return`/`label_rank_pct`/`label_top_bucket`) are strictly forward-shifted.
  Full run: 359,236 eligible and 349,353 labelled snapshots in 429 s, with
  `benchmark_missing_sessions=0`. `PointInTimeFeatureLeakTests` recomputes
  date-`D` features from bars `<= D` only and asserts the labels come from `> D`.
- **Walk-forward framework** (`walk_forward.py`) — 100% new; there was no
  rolling-retrain code anywhere in the repo. Expanding window, 6 folds, with a
  purge of at least the 20-session label horizon between train and validation so
  adjacent folds cannot leak.
- **Celery queue `etf-rotation`** with per-task routes (the repo routes by exact
  task name, not by `tasks.*` wildcard), beat slots at **16:45** sync / **17:20**
  features / **19:45** signal — chosen to avoid the existing 17:00 sentiment
  pipeline and to land after the 19:15 cache warm. Every long task overrides the
  global `CELERY_TASK_SOFT_TIME_LIMIT = 60` / `TIME_LIMIT = 300`, which would
  otherwise have killed the backfill, backtest and training tasks silently.
  `compose/local/django/start-celeryworker` and `scripts/run_local_stack.sh`
  (+ `check_stack()`) were updated, because a queue with no consumer fails
  silently.
- **`apps/core/finance_metrics.py`** — shared Calmar, alpha/beta, information
  ratio, annualized turnover, win rate, max drawdown and Sharpe. Previously
  `_calc_max_drawdown` and `_calc_sharpe` existed only as module-private helpers
  in `apps/backtest/tasks.py`; they are not duplicated inside the ETF app.
- **ETF-specific backtest engine and fee model.** Commission 万三 with a ¥5
  minimum, 10 bps slippage, and **no stamp duty on sells** — copying
  `apps/backtest/tasks.py:_resolve_fee_config` verbatim would have
  systematically overstated cost and understated alpha, since ETF sales are
  stamp-duty exempt. A liquidity constraint rejects any signal whose trade value
  exceeds 1/10 of that day's turnover. Verified by hand on a single-config run:
  `total_return=-8.09%`, `sharpe=-1.1854`, `total_fees=1,724.23`,
  `slippage=5,699.56`, 64 trades over 243 sessions, `fee_model.stamp_duty=0.0`.
- **Read-only API** under `/etf/` — `universe`, `daily`, `features`, `signals`,
  `positions`, `backtests`, registered on the single `config/urls.py`
  `DefaultRouter` (no app-level `urls.py`, per repo convention). The `/etf/`
  prefix is not cosmetic: `signals` and `positions` are already taken by the
  analytics `SignalEventViewSet` and the prediction surface. Contracts the schema
  cannot express are documented in a new `docs/reference/api.md` section. The
  `backtests` leaderboard sorts (`alpha`, `sharpe`, `return`, `drawdown`) pin
  nulls last, because PostgreSQL defaults to `NULLS FIRST` for `DESC` and an
  aborted run would otherwise head the table as the best result ever recorded.
- **`ModelType` gains `ETF_LIGHTGBM` / `ETF_LSTM` / `ETF_ENSEMBLE`**
  (migration `prediction.0006`). Per the design decision, the shared
  `apps/prediction/models.py:ModelVersion` registry is reused instead of adding
  an `ETFRotationModelVersion` table; validation windows live in the existing
  `metadata` JSONField and artifacts under
  `BASE_DIR/models/etf_rotation/{model_type}/{version}/` via `artifact_store`.

#### Changed:

- **`H00905.CSI` (CSI 500 Total Return) is now synced and is the alpha
  benchmark**; the `000905.SH` price index is display-only. Measuring alpha
  against a price index would have credited the strategy with the dividend yield
  it never earned. Both series carry 4,076 rows (bounded by
  `HISTORICAL_DATA_FLOOR` 2010-01-01), cumulative total return 0.8376 vs price
  0.5168, 0 coverage violations.
- **`validate_data_quality` handles alpha benchmarks.** `INDEX_CODE_LABELS`
  learns `H00905.CSI`, and a new `ALPHA_BENCHMARK_INDEX_CODES` +
  `_required_benchmark_trade_dates_by_code()` require daily coverage for a
  total-return code from the moment ingestion writes its first row, so a
  half-synced benchmark surfaces as a named backfill gap instead of passing
  unnoticed. These codes stay **out** of
  `required_pit_index_codes_for_date()`: that set also drives the
  `IndexMembership` checks and TuShare publishes no constituents or weights for
  a total-return series, so requiring it there would report a critical gap that
  can never be closed. A scoped run (2026-09-01 → 2026-10-20, 1,783 assets,
  ~7 min) writes zero rows to `benchmark_index_daily_gaps.csv` and
  `pit_benchmark_daily_gaps.csv`, and no report mentions `H00905` — the change
  adds no new failures. The 51,633 criticals it does report are pre-existing
  stock-side gaps (`missing_factor_score` 34,239, `missing_ohlcv` 17,394).

#### Result: the rule baseline ships, the ML models do not

This is the part worth reading before trusting any number above.

The RULE grid ran 144 combinations (140 executed, 4 reused) in 1,116 s. The
winner — monthly rebalance, max 3 positions, `momentum_lookback=20`,
absolute-momentum filter on, score weighting — returns **+77.56%** over the
2024-01-01 → 2026-10-20 holdout: annualized **+25.37%**, alpha **+18.21%** vs
`H00905.CSI`, information ratio 0.458, Sharpe 0.908, max drawdown 20.0%, beta
0.607, ¥11,737 in fees.

**Neither ML ranker cleared the gates that were specified for it:**

- *Holdout rank IC* was required to be positive in **all 6** folds. LightGBM was
  positive in 4/6 (mean 0.0306), LSTM in 4/6 (mean 0.0384). Both fail.
- *Incremental alpha* — ML had to beat RULE on the same fold split, or not ship.
  LightGBM lost all 3 grid configs (alpha −0.0141 / −0.0026 / +0.0338 against
  RULE's +0.1821 / +0.1757 / +0.1752). ENSEMBLE lost all 3 (+0.0641 / +0.0712 /
  +0.0355) — the 0.3 LightGBM weight drags the 0.3 LSTM weight below the pure
  rule baseline. LSTM passed 2 of 3 but **lost the grid's #1 config** (+0.1646 vs
  +0.1821), and bought its two passes with beta 1.08–1.19 and max drawdown
  0.41–0.42 against RULE's 0.61–0.69 and 0.18–0.20. Its information ratio did
  improve (0.74/0.88/1.02 vs 0.458/0.483/0.460), which makes it a candidate for
  further work rather than a result to discard.

All three ETF `ModelVersion` rows are therefore `status=READY` but
**`is_active=False`**, each carrying a `deployment_note` recording exactly these
numbers. With nothing active, `rotation_engine` resolves no predictor and serves
RULE, and an ENSEMBLE request renormalises to `{'RULE': 1.0}` instead of
crashing — behaviour pinned by `RegistryFallbackScoringTests` (10 tests), so the
19:45 beat task cannot fail on a cold environment.

Backtest rows 153–161 are retained for audit but must **not** be quoted: their
models were trained through 2026-09-14, overlapping the holdout, so their
apparent +513.76% / alpha +68.55% is in-sample memorisation. The honest numbers
come from the `--end 2023-11-30` retrain (versions `*-oos23`, rows 162–170),
which also purges the 20-session label horizon. Deleting the contaminated rows
would be irreversible, so they are labelled instead: each of the nine carries an
`INSAMPLE-` name prefix and a `report.holdout_integrity` note, so sorting
`/etf/backtests/?sort=alpha` shows them as what they are rather than as the best
result the module ever produced.

Backend test count is 434 against a `MIN_TEST_COUNT` floor of 362.

### version 0.1.17

#### Removed:

- **The entire authentication module.** This is a single-user experimental project,
  so the JWT + HttpOnly-cookie refresh stack, registration, email verification,
  password reset, subscription tiers, usage metering and developer API keys had no
  second user to protect and were actively costing correctness. **Breaking API
  change**: `SPECTACULAR_SETTINGS['VERSION']` goes `0.1.13` → `0.2.0`.
  - **Endpoints deleted** (all now 404): `/api/v1/auth/token{,/refresh,/verify}/`,
    `/api/v1/auth/{login,refresh,logout,session}/`,
    `/api/v1/users/{register,verify-email,password-reset,password-reset-confirm}/`,
    `/api/v1/users/{profile,subscriptions,usage}/`, and `/api/v1/developer/keys/`.
    `/api-auth/` is no longer routed either, but it is **not** a 404: the SPA
    catch-all's negative lookahead no longer excludes that prefix, so the path
    renders the app shell. Nothing remains that issues or accepts a credential.
  - `REST_FRAMEWORK` is now `DEFAULT_PERMISSION_CLASSES = [AllowAny]` with
    `DEFAULT_AUTHENTICATION_CLASSES = []`. SessionAuthentication was deliberately
    left out rather than demoted to last: with JWT gone it would become the first
    authenticator to engage for a browser holding an `/admin/` session cookie, and
    its CSRF enforcement would 403 same-origin SPA writes — a new bug the removal
    would otherwise have introduced. All 23 per-route `IsAuthenticated` gates and the
    implicit `IsAuthenticatedOrReadOnly` default are gone. **This opens writes, not
    just reads**: twenty `@action` routes declared `methods=['post']` (model
    training, news and macro ingestion, screener and factor recalculation, backtest
    pause / resume / restart) plus every viewset's create/update/destroy used to
    refuse an anonymous caller and now accept one, with no throttling left to bound
    them. `docs/reference/api.md` §2 states the deployment consequence — keep this
    API on localhost or a trusted network.
  - `apps/users` deleted outright (`UserProfile`, `Subscription`, `APIUsage`,
    `APIUsageMiddleware`, the cookie/JWT view layer, signals, admin overrides) and
    `apps/developer` reduced to `ChangelogEntry` (`DeveloperAPIKey` +
    `APIKeyAuthentication` deleted). App count ten → nine. `django.contrib.auth` and
    `/admin/` stay, so `createsuperuser` still works — now for admin access only.
  - **All DRF throttling removed** (`apps/core/throttling.py` deleted, no
    `DEFAULT_THROTTLE_CLASSES`/`_RATES`). The tier throttles keyed on
    `request.user.profile.subscription_tier`, so they were the last consumer of the
    subscription model. No 429 responses anymore.
  - **User foreign keys dropped from domain models**: `ScreenerTemplate.owner`,
    `AlertRule.owner` (made nullable first, in its own commit, so the permission
    change and the schema change could land separately), `BacktestRun.user`.
    `AlertRule.__str__` returned `"{owner.username} - {name}"` and now returns the
    name. `owner`/`owner_username`/`user`/`user_username` leave the API payloads, and
    `run_summary.csv` loses its `user_id`/`user_email` columns.
  - **Alerting is now settings-driven**: new `ALERT_NOTIFY_EMAILS` replaces
    `rule.owner.email`; the SMS webhook payload carries `alert_rule_id` instead of
    `user_id`; the Channels fan-out collapses from per-user `alerts_user_{id}` to one
    global `alerts` group. `SIMPLE_JWT`, all `AUTH_COOKIE_*`, and `FRONTEND_URL`
    leave settings (only `DJANGO_SECRET_KEY` remains, still as Django's `SECRET_KEY`),
    and `djangorestframework-simplejwt` leaves `requirements/base.txt`.
    **Configure `ALERT_NOTIFY_EMAILS` or the email channel goes quiet**: it defaults
    to `[]`, and an event is only marked `SENT` when at least one channel actually
    dispatched, so a rule whose only channel is `email` now records `FAILED` instead
    of mailing its owner. Existing rules are affected silently — there is no error,
    just a `FAILED` row per trigger. `test_email_channel_without_recipients_records_failed_dispatch`
    asserts that outcome so it cannot be softened into a silent success by accident.
  - **WebSockets are open**: `AuthMiddlewareStack` removed from `config/asgi.py`, so
    `/ws/alerts/` needs no session and no `?token=` — which also drops a per-connection
    DB round-trip. `config/routing.py` was deleted: it duplicated this wiring and was
    imported by nothing, so edits to it had never had any effect.
  - **Frontend**: `lib/api.ts` sheds ~430 lines (in-memory access token,
    `BroadcastChannel('fa-auth')` cross-tab refresh mutex, proactive-refresh timer,
    hand-rolled `decodeJwtExp`, the 401→refresh→retry interceptor, login/logout/
    session-bootstrap, API-key minting) and stops sending `credentials: 'include'`,
    so the SPA no longer transmits `sessionid` to the API either. `main.tsx` renders
    synchronously instead of blocking first paint on a `/auth/session/` probe plus a
    possible `/auth/refresh/`. `AuthSettingsPanel.tsx` deleted; `/settings` is now
    just the language selector.
  - **Two silent bugs fixed as a side effect.** `useAlertsSocket.ts` suppressed
    reconnection when no credential was present, and `BacktestWorkbenchPage.tsx`
    early-returned from both `setInterval` callbacks on the same check — so an
    unauthenticated visitor's alert stream never reconnected and backtest progress
    never live-updated, with no console error either time. A test had even been
    written asserting the polling suppression as desired behaviour; it is deleted.
    Both paths are now asserted directly instead: `useAlertsSocket.test.ts` drives a
    fake socket through open → close → backoff → give-up (it had no tests at all),
    and the surviving workbench test covers unconditional polling.
  - `scripts/smoke_api_check.sh` and `scripts/measure_page_api_latency.sh` no longer
    require `SMOKE_USERNAME`/`SMOKE_PASSWORD` (both keys are now dead in `.env`).
    The smoke check gained two assertions: `/auth/login/` and `/developer/keys/`
    must return 404, and an **unauthenticated `POST /api/v1/alerts/` must return
    201 with no `owner` in the response body** — followed by a `DELETE` that must
    return 204, so the probe cleans up after itself. That write path had no
    automated coverage at all before, only a hand-run `curl`.
  - **Deliberately untouched**: `has_stale_task_owner` and its helpers. "Task owner"
    in `apps/backtest/task_health.py` means **Celery worker** ownership, not
    authentication, and is unrelated to this removal.

### version 0.1.16

#### Changed:

- **Model artifacts moved out of git into a MinIO S3 object store** — completing the
  git-bloat half of the artifact-storage backlog (the storage abstraction + the
  migrate/sync/verify commands were merged earlier). MinIO is deployed on the NAS
  (`finance_artifacts_minio` @ `192.168.31.8:9000`, ZFS dataset `DefaultMirror/minio`,
  LAN-scoped `DOCKER-USER` firewall, bucket `finance-analysis-artifacts` with versioning,
  a least-privilege scoped key). All 170 on-disk artifacts (38 families, ~86 MB) were
  migrated with `migrate_artifacts_to_store --execute --normalize-registry`; the app now
  runs `ARTIFACT_STORE_BACKEND=s3`, and `verify_artifact_store` confirms every ACTIVE
  artifact resolves in the store (cold-load from MinIO verified via `ensure_dir_local`).
  `models/` is untracked and gitignored — now a retrainable local cache; fresh clones
  repopulate via `sync_artifacts_from_store`. Forward-only: existing history keeps its
  ~58 MB of blobs, but `.git` stops growing one retained family per retrain (§6.2). The
  `models.md` reference column was renamed `Path resolves here?` -> `Present in store?`
  to match the store-aware check. Docs: `docs/how-to/artifact-store.md`; NAS runbook:
  HomeServer `docs/runbooks/minio.md`.

### version 0.1.15

#### Fixed:

- `apps/core/management/commands/validate_data_quality.py`: every order-independent streaming scan now calls `.order_by()` to clear its model's default `Meta.ordering` — the six `_write_effective_universe_daily_coverage` loops (OHLCV, TechnicalIndicator, SentimentScore, FundamentalFactorSnapshot, CapitalFlowSnapshot, FactorScore), the two `_write_cross_section_audit` scans (TechnicalIndicator `RS_SCORE`, FactorScore), and the `_validate_capital_flow_null_fields` AssetMoneyFlowSnapshot scan. Without it, `TechnicalIndicator.Meta.ordering = ['-timestamp', 'asset', 'indicator_type']` forced PostgreSQL to fully sort the ~140M rows matched by a 16-year scan of the ~99 GB / ~168M-row `analytics_technicalindicator` table before the server-side cursor could stream the first row. That multi-minute silent sort is what dropped the connection mid-run as `django.db.utils.OperationalError: server closed the connection unexpectedly` (the trigger behind the CSI 500 Step-4 validation failure). The loops only OR bits into per-date feature bitmaps, so row order is irrelevant and clearing it is result-equivalent — verified by `DataQualityValidationCommandTests` + `TechnicalIndicatorValidationRegressionTests` (18 tests) and a full-range live run that now streams past the coverage step. `EXPLAIN` confirms the Sort disappears: `Gather Merge` + `Sort` + `Parallel Seq Scan` (cost ~32.8M) becomes a plain streaming `Seq Scan` (cost ~7.3M). This complements the 2026-09-29 sargable-`timestamp` rewrite, which fixed the non-sargable WHERE clause but left this implicit ORDER BY.
- `validate_data_quality` also caps the validation session's PostgreSQL `work_mem` (`SET work_mem = '32MB'`, next to the existing `SET jit = off`) so a heavy step cannot balloon a single backend on the shared NAS host — defence-in-depth now that the coverage scans stream without sorting.
- The coverage + cross-section scans are also wrapped in `transaction.atomic()`. In autocommit, psycopg2 declares `.iterator()`'s server-side cursor `WITH HOLD`, so PostgreSQL materialised the entire ~100M-row TechnicalIndicator scan before the client received a row; the client sat app-idle for >10 min and the WSL2↔NAS connection timed out (server log: `could not receive data from client: Connection timed out`), orphaning the run and leaving the client blocked on a half-open socket. Inside a transaction psycopg2 uses a plain streaming cursor (`FETCH` per chunk), so rows flow continuously and the connection never goes idle long enough to drop. This — not the sort alone — was why the connection kept dying after the sargable + `.order_by()` fixes.

### version 0.1.14

#### Changed:

- Switched the effective stock-pool universe from the CSI 300 + CSI A500 point-in-time union to a single-index CSI 500 (`000905.SH`) universe, simplifying the cross-sectional contract and improving the risk/return balance.
- `apps/markets/benchmarking.py`: single-index contract from the `2010-01-01` floor; removed the CSI A500 launch-date branch; renamed the internal PIT benchmark to `CSI500_PIT` ("CSI 500 PIT").
- Ingestion (`apps/markets/tasks.py`) now syncs CSI 500 (`000905.SH`) constituents and benchmark history; the membership tag is `CSI500`.
- Backtest comparison curves now plot a single official `CSI 500` series (`csi500`).
- Retired `onboard_csi_a500_universe` / `rollout_csi_a500_universe`; added `onboard_csi500_universe` for the end-to-end universe onboarding + retrain workflow.
- Added migration `markets.0013` to purge legacy CSI 300 / CSI A500 membership rows, official benchmark series, the old PIT union benchmark rows, and the `CSI300`/`CSIA500` asset tags.
- Requires a data re-sync, raw backfill for CSI 500 constituents, PIT benchmark rebuild, and LightGBM/LSTM retrain (see `docs/how-to/backfill.md` and `docs/how-to/retrain.md`).

#### Fixed:

- Pure-Vite HMR flow restored at `http://localhost:5173/`: removed `base: '/static/'` from `frontend/vite.config.ts`. That base moved the entire dev server under `/static/`, where `createBrowserRouter` (which has no basename) matched no route and rendered the router's 404 error boundary — `http://<host>:5173/static/` showed "Unexpected Application Error! 404 Not Found". Production asset URLs never depended on `base`: django-vite resolves manifest paths through `staticfiles_storage.url()`, which prepends `STATIC_URL` itself, and `collectstatic` picks up `frontend/dist` via `STATICFILES_DIRS`.
- `config/settings/base.py`: `DJANGO_VITE['default']['app_client_class']` now points at the new `apps/core/vite_client.py::RootOriginDevClient`, which emits dev-mode asset URLs against the Vite origin root (`http://localhost:5173/src/main.tsx`, `http://localhost:5173/@vite/client`, `/@react-refresh`) instead of `STATIC_URL` + path — the only reason `base: '/static/'` existed. The Django-served dev mode (`DJANGO_VITE_DEV_MODE=True`) and the prod manifest mode are unchanged in behaviour.

### version 0.1.13

> **Note**: This entry is an audit checklist, not release notes. It documents the
> validation workflow used during the 0.1.13 development cycle. For proper release
> notes, see the structured entries in 0.1.12 and earlier. This section will be
> converted to standard release-note format before the 0.1.13 tag is created.

#### Frontend / Deployment: Django now serves the built SPA

**Objective**: collapse the two-origin dev/deploy topology into a single Django
origin without touching the React codebase.

**Implemented**:

- Django renders the SPA shell through `django-vite==3.1.0` +
  `whitenoise==6.8.2`. New files: `apps/core/views.py` (`SPAFallbackView`),
  `apps/templates/frontend/index.html`, `apps/core/tests_frontend.py`.
- `config/urls.py` mounts the shell at `/` and at a negative-lookahead
  catch-all `^(?!api/|admin/|api-auth/|static/|media/|ws/).*$` so every
  client-side route resolves to the SPA while DRF, admin, static, and Channels
  paths keep their own handlers.
- `frontend/vite.config.ts`: added `base: '/static/'`, `server.origin`,
  `build.manifest: true`, and `build.rollupOptions.input` pinned to
  `src/main.tsx` so the manifest keys match the Django template tag.
- `config/settings/base.py`: added `django_vite` to `INSTALLED_APPS`, inserted
  `whitenoise.middleware.WhiteNoiseMiddleware` after `SecurityMiddleware`,
  guarded-append `frontend/dist` to `STATICFILES_DIRS`, and introduced the
  `DJANGO_VITE` config block plus `DJANGO_VITE_DEV_MODE` env var (defaults to
  `DEBUG`).
- `config/settings/production.py`: enabled
  `whitenoise.storage.CompressedManifestStaticFilesStorage` so prod assets are
  pre-compressed and content-hashed.
- `compose/local/django/Dockerfile`: added a `node:22-alpine` `frontend-build`
  stage that runs `npm ci && npm run build`; the runtime stage overlays
  `/frontend/dist` onto `/app/frontend/dist`. `.dockerignore` excludes
  `frontend/node_modules`, `frontend/dist`, and `frontend/coverage`.
- `docker-compose.yml`: added an anonymous volume at `/app/frontend/dist` on
  the `django` service so the `.:/app` bind-mount does not hide the
  image-baked build.
- Mirrored `frontend/public/favicon.svg` and `frontend/public/icons.svg` into
  `apps/static/` so `{% static %}` resolves them in both dev and prod modes.

**Breaking change**:

- `FRONTEND_URL` default flipped from `http://localhost:5173` to
  `http://localhost:8000` in `config/settings/base.py` and `.env.example`.
  Verification and password-reset email links now land on Django's origin,
  which serves the SPA. **Operators who still run the pure-Vite HMR flow and
  want email links to open on `:5173` must set `FRONTEND_URL=http://localhost:5173`
  explicitly in their `.env`.**

**New env vars** (all optional; defaults shown):

- `DJANGO_VITE_DEV_MODE` = `DJANGO_DEBUG` -- when True, Django-rendered pages
  point `<script>` tags at the Vite dev server on `:5173` so HMR keeps working
  through the Django origin.
- `DJANGO_VITE_DEV_SERVER_HOST` = `localhost`.
- `DJANGO_VITE_DEV_SERVER_PORT` = `5173`.

**Unchanged**: every React component, hook, page, and `lib/api.ts` call. The
pure-Vite HMR flow at `http://localhost:5173/` still works exactly as before;
`scripts/run_frontend.ps1` / `.sh` remain the canonical entry point for it.
Same-origin serving means `fetch('/api/…')` and `ws://…/ws/alerts/` keep
working with no client-side changes.

#### Validation Checklist:

Workflow Audit Checklist

先定一条总规则

先把这条写进文档、代码注释、测试用例里：

effective_universe(date)

• overall date range: 2010-01-04 <= date < 2026-04-30
• 2010-01-04 <= date < 2024-09-23 → CSI300 only
• date >= 2024-09-23 → CSI300 ∪ A500
• 一切横截面计算、训练样本过滤、回测候选池、benchmark 构建、daily prediction，都必须调用同一套 universe 规则
• 禁止静默 fallback 到 all assets
• 如果发生 fallback，必须：
   • 打 warning

这条是总闸门。没有它，后面都容易脏。

一. fundamental data coverage

B. 因子/特征原始源

• [x] backfill pre-warmup ohlcv data for pre 2010-1-4 and pre index listing for calculation of ohlcv-derived data
1. backfill the pre 20100104 and pre index-listing data for 18 months for technical data calculation and rs_score calculation
• [x] fina_indicator 类季度财务指标是否正确 as-of 对齐，不用未来财报
• [x] 技术指标是否只用到当日及过去数据
• [x] macro snapshot validation and backfill: DXY, CNY/USD, China 6M/1Y/3Y/5Y/7Y/10Y/30Y Yield, PMI Manufacturing, PMI Non-Manufacturing, and CPI YoY
1. backfill the pre 201607 data from manually downloaded csv from gov site
2. switch from curve term = [10, 2] to [0.5, 1, 3, 5, 7, 10, 30]
3. switch runtime yield_curve to cn10y_yield - cn3y_yield
4. stick to curve type = 曲线类型：0-到期
5. rescheduled the syncing to the second day of each month at 0:10 am
6. maps the Yahoo monthly open onto month-start MacroSnapshot.cny_usd rows for 2010-01 through 2012-02 
• [x] market context validation and backfill 
• [x] Capital Flow Snapshots validation and backfill: 1311 gap periods due to upstream data source blackouts. otherwise the coverage should be complete since 2010-01-04 for CSI300 constituents, and since 2024-09-23 for A500 constituents.
• [x] technical indicator validation and backfill
• [x] fundamental snapshot validation and backfill
1. switched pe to pe_ttm
2. 55 assets have null pe_ttm rows due to TuShare upstream data source blackouts
• [x] added stale gate to localy computed indicators
1. refer to the updated technicalguide.md for the stale gate rules for each technical indicator
• [x] added calendar date validation if there are duplicated rows from the upstream
• [x] dispose pretrade_date from the system: `ExchangeTradingCalendar` stores TuShare `cal_date` as `trade_date` plus `is_open`; open-day consumers filter on `is_open=True`


要抽查

• [x] PE/PB/ROE 在 2010-2026 是否不是大面积空值
• [x] 任取一个日期，检查该日 snapshot 是否引用了未来季度财报或未来宏观值：抽查 2024-04-15，300 个 effective-universe assets 均未引用未来 `fina_indicator_ann_date` / report period / daily-basic source date；MacroContext 指向 2024-04-01 MacroSnapshot（当前 MacroSnapshot 尚未存 per-field release date）



───

C. 横截面特征

• [x] rs_score: missing values
• [x] factor score: Composite Score, Bottom Probability Score, Fundamental Score, Capital Flow Score, Technical Score

都必须确认：

• [x] 只在 当日 effective_universe 内计算
• [x] 不是拿全市场算
• [x] 不是拿未来扩大后的 universe 倒推过去
• [x] 不是 membership 缺失时直接退化成 all assets 且无告警

check random 10 dates, 每个日期核查：

• [x] universe size
• [x] rank 分位数分布
• [x] 参与横截面排名的股票清单
• [x] 是否与 membership 一致

抽查 10 个交易日：2010-01-04、2011-05-20、2013-12-31、2016-06-01、2020-03-16、2024-09-20、2024-09-23、2025-01-02、2025-12-31、2026-03-02。

结果：`FactorScore` 五个核心字段（`composite_score`、`bottom_probability_score`、`fundamental_score`、`capital_flow_score`、`technical_score`）在 10 个日期均与 `effective_universe(date)` 精确一致，`unexpected_outside_universe_count = 0`、`missing_from_universe_count = 0`；`RS_SCORE` 仍有少量 missing rows，但 10 个日期均无 outside-universe participants，且所有抽查字段取值均落在 `[0, 1]`。

D. 数据覆盖率输出

• [x] 每类 snapshot 都有 coverage report
• [x] 每类 snapshot 至少包含：
  • 日期
  • effective_universe_count
  • feature_non_null_count
  • usable_asset_count
  • dropped_asset_count
  • missing_by_feature

当前这些字段已统一出现在 `effective_universe_daily_coverage.csv` 中。

红灯项：

• [x] 未发现某关键日期 usable_asset_count 突然断崖式下降
• [x] 某大类特征在长时间段大量空值: `margin_balance_change_5d` 和 `pe_ttm_percentile_score` 主要来自 TuShare/source-side 缺口；抽查到的 `RS_SCORE` missing 则是停牌导致 exact-window 不成立
• [x] cross-sectional ranking 的母集和 effective_universe 不一致

当前 bundle 未出现 `usable_asset_count_cliff` 红灯；但 `margin_balance_change_5d` 仍存在长时间段稀疏覆盖，`null_reason_buckets.csv` 记录了 `820934` 条 `missing_margin_detail_source_row` 和总计 `826243` 条 `margin_balance_change_5d.expected_field_null`。这类缺口主要是原始 `margin_detail` 源缺失，5 日差分 warmup 已由单独的 `margin_diff_5_warmup_insufficient` reason 统计。`pe_ttm_percentile_score` 的抽查 missing 则来自最新 `FundamentalFactorSnapshot.pe_ttm = NULL`，对应 TuShare `daily_basic.pe_ttm` 的 source-side null / blackout。另一方面，当前抽查到的 `RS_SCORE` missing participants 不是 universe 泄漏，而是 full-day suspension 让 exact 20-trading-day window 不成立；这部分属于预期跳过而不是 TuShare blackout。

二、membership 层 audit

这一层查的是：谁在 universe 里、从什么时候开始在、你有没有把未来名单拿回过去用。

A. IndexMembership 数据完整性

• [x] CSI300 历史快照是否覆盖训练窗和回测窗
• [x] A500 历史快照是否只从真实发布时间开始使用
• [x] 每次成分调整是否能落到最近有效日期

你现在尤其要查

• [x] 2010-2026 期间，CSI300 是否连续可用
• [x] 2024-09-23 之后，A500 是否连续可用

当前 DB 审计结果（2026-05-16）：

• `000300.SH`：最早 snapshot = `2010-01-04`，最新 snapshot = `2026-05-06`，distinct snapshot dates = `725`
• `000510.CSI`：最早 snapshot = `2024-09-23`，最新 snapshot = `2026-04-30`，distinct snapshot dates = `20`，`2024-09-23` 之前 row count = `0`
• 对 `2010-01-04 .. 2026-04-30` 的官方开市日运行 `pit_membership_coverage_gaps(...)`，总 gap count = `0`
• 对 `2024-09-23 .. 2026-04-30` 的 A500 专项检查，gap count = `0`
• 所有 snapshot 日期都落在 SSE 开市日；resolver 使用 `latest snapshot <= target_date` 取最近有效快照；两条指数的最大快照 carry gap 均为 `22` 个交易日

因此，Section A 就“历史覆盖、A500 起始边界、连续可用性、最近有效快照可解析”这几个完整性问题可以先关闭。`公告日生效` vs `调样生效日` 的制度解释仍留到 Section B 处理。

───

B. membership 解释规则

• [x] 用的是“公告日生效”还是“调样生效日”
• [x] 对两次 snapshot 之间的日期，是否采用 forward fill 到下次变更前
• [x] 对 snapshot 缺失的日期，是 fail、skip，还是 fallback
• [x] 如果 fallback，是否记录并阻断主流程
• [x] 允许 forward fill 最近一次有效 snapshot
• [x] 不允许在没有历史起点的时期凭空 forward fill
• [x] A500 在 2024-09-23 之前应视为 不存在，不是“空表时默认今天名单”

当前实现规则（2026-05-16 代码审计）：

• `sync_index_constituent_universe()` 直接把 TuShare `index_weight.trade_date` 落到 `IndexMembership.trade_date`；系统没有单独存 `announcement_date` / `effective_date` 双字段。因此当前实现实际上是“按 provider 给出的 snapshot/trade_date 口径生效”，不是“先存公告日再另算生效日”的双口径模型。
• `resolve_effective_index_snapshot_dates()` 对每个指数执行 `trade_date <= target_date` 且取最新一条，所以两次 snapshot 之间采用“最近一次有效快照 forward fill，直到下一次 snapshot 替换”为止。
• `pit_membership_coverage_gaps()` / `ensure_pit_membership_coverage()` 的规则是：如果某个 required index 在目标日期之前从未出现过历史起点，则判为缺失并直接报错；canonical workflow 不是 skip，也不是 fallback 到 all assets。
• `backfill_model_data`、训练特征构建、backtest universe 选择等主流程都先调用 `ensure_pit_membership_coverage(...)`；缺历史时抛 `PITMembershipCoverageError`，命令层再转成 `CommandError` 终止流程。validator 会把 gap 写进 `index_membership_history_gaps`，但不会静默放大 universe。
• 允许 forward fill 最近一次有效 snapshot，但只允许在“已有历史起点”的前提下 forward fill；没有历史起点时，`point_in_time_union_asset_ids()` 最多返回空，而 canonical `effective_universe_asset_ids()` 会先做 coverage guard，不允许凭空造出母集。
• `required_pit_index_codes_for_date()` 明确规定：`2010-01-04 <= date < 2024-09-23` 只要求 `000300.SH`，`date >= 2024-09-23` 才额外要求 `000510.CSI`。因此 A500 在 `2024-09-23` 之前被视为“不存在/不要求”，不是“空表时默认今天名单”。

因此，Section B 可以先按当前实现关闭：规则是“provider `trade_date` 作为生效 snapshot date + 最近有效快照 forward fill + 缺历史即 fail fast”。如果后续要严格区分“中证公告日”与“调样正式生效日”，需要先引入独立字段和对应 source 证据，当前表结构还不支持这两种制度并存。

───

C. effective_universe 验证

• [x] 2024-09-22：只能有 CSI300
• [x] 2024-09-23：开始允许 CSI300 ∪ A500
• [x] 去重逻辑正确，同一股票不会在 union 中重复计数

要输出

• [x] 每个抽查日的 constituent_count
• [x] union_count
• [x] overlap_count
• [x] added_by_a500_count

当前 DB 抽查结果（2026-05-16）：

• `2024-09-22`：`CSI300 snapshot=2024-09-02`，`A500 snapshot=None`，`CSI300 constituent_count=300`，`A500 constituent_count=0`，`union_count=300`，`overlap_count=0`，`added_by_a500_count=0`；`union_equals_csi300=True`
• `2024-09-23`：`CSI300 snapshot=2024-09-02`，`A500 snapshot=2024-09-23`，`CSI300 constituent_count=300`，`A500 constituent_count=500`，`union_count=566`，`overlap_count=234`，`added_by_a500_count=266`
• `2025-01-02`（补一个 post-launch 抽查日）：`CSI300 snapshot=2025-01-02`，`A500 snapshot=2024-12-31`，`CSI300 constituent_count=300`，`A500 constituent_count=500`，`union_count=564`，`overlap_count=236`，`added_by_a500_count=264`
• 三个抽查日都满足 `duplicates=False`；A500 已存在的抽查日满足 `union_count = csi300_count + a500_count - overlap_count`

因此，Section C 可以先关闭：`2024-09-22` 的 effective universe 仍然严格等于 CSI300，`2024-09-23` 起开始并入 A500，且 union 去重逻辑工作正常，没有重复资产计数。

───

D. 禁止的behavior

• [x] 用 2024-09-23 的 A500 名单外推到 2010-2024
• [x] membership 缺失时静默回退到 all assets
• [x] benchmark 和 candidate pool 使用不同 universe 口径
• [x] training 用一个 universe，daily prediction 用另一个，但没有版本标记

当前代码/样本审计结果（2026-05-16）：

• “用 `2024-09-23` 的 A500 名单外推到 `2010-2024`” 这一红灯当前未发现：`required_pit_index_codes_for_date()` 在 `2024-09-23` 之前只要求 `000300.SH`，Section C 抽查也证明 `2024-09-22` 的 union 仍然严格等于 CSI300。
• “membership 缺失时静默回退到 all assets” 当前未发现：训练特征/标签构建、`backfill_model_data`、factor/RS 刷新、backtest candidate 选择都先调用 `ensure_pit_membership_coverage(...)`；缺历史时会抛 `PITMembershipCoverageError` / `CommandError`，validator 也会把 gap 写入 `index_membership_history_gaps`，不是 silent fallback。
• “benchmark 和 candidate pool 使用不同 universe 口径” 当前样本未发现：backtest candidate pool 先取 `point_in_time_union_asset_ids(dt)` 再与当日可交易 `OHLCV` 交集；PIT benchmark 行也基于同一个 `resolve_point_in_time_union_membership()` 生成。抽查 `2024-09-23`、`2025-01-02`、`2025-12-31` 时，benchmark `constituent_count` / `overlap_count` 与 live PIT resolver 完全一致；benchmark 额外记录的 `weighted_constituent_count` 和 `missing_prices` 只是价格可得性层，不是另一套 membership 规则。
• “training 用一个 universe，daily prediction 用另一个，但没有版本标记” 当前未发现“口径分叉”：训练的 `_create_feature_matrix()` / `_create_labels_for_training()` 用 `ensure_pit_membership_coverage(...) + point_in_time_union_asset_ids_by_dates(...)`，日常 heuristic / LightGBM / LSTM prediction 都用 `effective_universe_assets(as_of, ...)`。这几条路径都落在同一套 PIT helper 上。

当前可保留的 follow-up：

• LightGBM / generic `ModelVersion` metadata 里还没有显式的 `effective_universe_policy` / `universe_version` 字段，所以“当前没有观察到训练/日推 universe 分叉”可以关闭，但“把 universe policy 做成 artifact provenance” 仍应留到训练层 Section 3C 继续补。
• PIT benchmark builder 当前和 candidate pool 使用同一 membership resolver，但 benchmark build path 本身没有额外调用 `ensure_pit_membership_coverage()` 做对称 fail-fast；在当前数据完整时不会造成口径错位，但如果要把 guard 做到完全一致，后续可以再补这一层防线。

───

三、training 层 audit

这一层查的是：模型到底学了什么样本，标签是不是对的，训练时是否混入了不该进来的股票和未来信息。

A. 训练样本过滤

• [x] 每条训练样本 (asset, date) 在该日必须属于 effective_universe(date)
• [x] 对 pre-2024-09-23，不允许 A500-only 资产混入
• [x] 对 post-2024-09-23，允许 CSI300 ∪ A500

要抽查

• [x] 随机抽 50 条训练样本，验证资产是否属于当日 universe
• [x] 抽 10 条 2024-09-23 前的样本，确认没有 A500-only 资产
• [x] 抽 10 条 2024-09-23 后的样本，确认 union 生效

当前代码/样本审计结果（2026-05-16）：

• LightGBM 的 `_create_feature_matrix()` 会先对目标交易日执行 `ensure_pit_membership_coverage(...)`，再用 `point_in_time_union_asset_ids_by_dates(...)` 构造 `membership_by_date`，最后逐行执行 `pit_in_universe = asset_id in membership_by_date[date]` 并过滤非 PIT 成员行；`_create_labels_for_training()` 在写入 label 前也有同一条 `if asset_id not in membership_by_date[target_date]: continue` 守卫。LSTM 训练直接复用这两个 helper，因此样本口径一致。
• `rebuild_lightgbm_pipeline` / `rebuild_lstm_pipeline` 在进入训练前，会先对整个训练窗口做一次 `ensure_pit_membership_coverage(...)`；如果 membership 历史缺口覆盖到训练窗口，命令层会直接失败，不会带着坏 window 继续训练。
• 对当前 live 训练窗口 `2016-06-01 -> 2024-12-31` 抽取 7d 训练标签样本做 read-only 审计：`training_sample_count=635139`，`full_membership_violations=0`。
• 随机抽 50 条训练样本，结果 `random50_bad=0`；50/50 都属于各自日期的 `effective_universe(date)`。
• 抽 10 条 `2024-09-23` 前样本，结果 `pre10_bad=0`，且全量 pre-launch 样本满足 `pre_launch_non_csi300_violations=0`；也就是说 `2024-09-23` 前训练样本仍然严格等于 CSI300 口径，没有 A500-only 资产混入。
• 抽 10 条 `2024-09-23` 后样本时，专门从 “A500 新增而非 CSI300” 的 post-launch 样本里取样，结果 `post_added10_bad=0`；样本中可见 `000009.SZ`、`000690.SZ`、`688099.SH`、`600008.SH` 等 `in_union=True` 且 `in_csi300=False` 的资产。全量 post-launch 训练样本里共有 `post_launch_a500_added_sample_count=17801` 条这类记录，说明 union 已实际进入训练集，不只是代码层声明。

因此，Section 3A 的前三条样本过滤要求和三个抽查项可以先关闭

───

B. 标签生成

• [x] 3d / 7d / 14d / 30d 的标签逻辑分别明确
• [x] 标签窗口只使用未来收益本身，不带任何未来特征
• [x] label 与 feature date 对齐，没有 off-by-one
• [x] 停牌、退市、缺失价格样本如何处理有明文规则

要抽查

• [x] 任取一个 asset/date，手工算一次 3d 和 7d label
• [x] 对边界日（如年末、停牌前）检查 label 是否异常
• [x] 标签生成前后样本数变化是否合理

当前代码/样本审计结果（2026-05-16）：

• `_create_labels_for_training(start_date, end_date, horizon_days)` 的公式是统一的：先取 `(asset, target_date)` 当日 `close` 作为 `start_price`，再取该 asset 第一条满足 `date >= target_date + horizon_days` 的未来 `close` 作为 `end_price`，只用 `forward_return = (end_price - start_price) / start_price` 打标签：`>= +2% -> UP`，`<= -2% -> DOWN`，其余为 `FLAT`。因此 3d / 7d / 14d / 30d 的标签定义本质上是同一公式，只是 `horizon_days` 不同。
• 需要区分“helper 能算”与“当前训练入口支持”：直接调用 `_create_labels_for_training(..., 14)` 时，当前 DB 可生成 `635056` 条 14d label；但 `train_lightgbm_models()`、`train_lstm_models()`、`rebuild_lightgbm_pipeline`、`rebuild_lstm_pipeline` 以及当前 API / 推理入口都只 expose `3 / 7 / 30`，不接受 14d 作为 active horizon。
• 标签窗口只使用 future price 本身，不引用任何 future feature：LightGBM 训练先构造 `labels_df(date, asset_id, label)`，再用 `X_df.merge(labels_df, on=['date', 'asset_id'], how='left')` 对齐并丢掉 `label is null` 的行；LSTM `_build_sequences()` 则拿“以 `target_date` 结尾的历史 sequence”去查 `labels_dict[(target_date, asset_id, horizon)]`。因此 label key 与 feature row 使用的是同一个 `(date, asset_id)`，没有 off-by-one，也没有把未来特征拼进训练样本。
• 对 live 训练窗口 `2016-06-01 -> 2024-12-31` 做 read-only 审计，PIT feature rows 共 `635229` 条；对应标签数分别是 `3d=635185`、`7d=635139`、`30d=634850`。样本减少仅 `44 / 90 / 379` 条，占比约 `0.0069% / 0.0142% / 0.0597%`，随 horizon 变长而增加，变化量合理。
• 上述缺 label 的行，当前样本里本质上都属于“在 helper 查询窗口 `end_date + horizon_days + 7` 内找不到满足 `date >= target_date + horizon_days` 的 future OHLCV” 而被跳过；`start_price <= 0` 当前样本里为 `0`。因此对停牌、退市、缺失价格、以及训练窗口尾部靠近年末的样本，当前规则是：拿得到未来价格就按第一条满足阈值的 future price 打 label，拿不到就直接 drop，不会硬造 label。
• 手工抽查一个普通样本：`601899.SH` 在 `2016-06-01` 的 `start_price=2.51`。3d 标签使用 `future_threshold=2016-06-04` 之后第一条价格 `2016-06-06 -> 2.55`，`forward_return=+1.5936%`，所以是 `FLAT`；7d 标签使用 `future_threshold=2016-06-08 -> 2.54`，`forward_return=+1.1952%`，也是 `FLAT`。
• 年末边界抽查：`300308.SZ` 在 `2024-12-31` 的 `start_price=122.89`。3d 标签使用 `2025-01-03 -> 122.39`，`forward_return=-0.4069%`，为 `FLAT`；7d 标签使用 `2025-01-07 -> 127.71`，`forward_return=+3.9222%`，为 `UP`。跨年没有观察到 off-by-one 异常。
• 缺 future price 的边界样本也能对上代码规则：`002123.SZ` 在 `2024-12-30` 的 3d label 需要 `future_threshold=2025-01-02` 之后的价格，但该 asset 下一条 OHLCV 到 `2025-01-16` 才出现，已经超出 3d helper 的查询上界 `2025-01-10`；因此这条样本会被视为“无可用 future price”而跳过。7d 下同理，`2024-12-25` 到 `2024-12-30` 的几条末端样本也都会因下一条价格晚到 `2025-01-16` 而被 drop。

因此，Section 3B 当前可以先关闭：label 定义本身是“同日 feature + 未来第一条满足阈值的价格”的纯 future-return 规则，没有观察到 off-by-one 或 future-feature 泄漏；需要额外记住的实现细节只有两点，一是 active horizon 仍然只有 `3 / 7 / 30`，二是长停牌/退市/窗口尾部缺未来价时样本会被直接丢弃。

───

C. 特征 schema 一致性

• [x] 训练时 feature list 固化
• [x] 不允许 feature 缺失后静默补 0 而不记录

需要保存到 artifact 的信息
• [x] feature_names
• [x] feature_count
• [x] training_window_start/end

当前代码/实现状态（2026-05-16，null-handling 改造已落代码，但 active artifact 仍需重训才能完全切到新口径）：

• 对“训练时 feature list 固化”这一条，当前 real ML 训练路径可以先关闭：`_create_feature_matrix()` 在返回的 DataFrame attrs 里固定 `feature_names`；LightGBM 训练对 `selected_feature_names` 精确取列训练，并把同一份列表写进磁盘 `metadata.json`、`LightGBMModelArtifact.feature_names`、以及按 horizon 注册的 `ModelVersion.feature_schema`。当前 active `LightGBMModelArtifact` 的 `lgb-3d/7d/30d-2024-12-31` 三个 artifact 都是 `feature_name_count=39`，且都满足 `feature_schema_size=39`、`training_window_start=2016-06-01`、`training_window_end=2024-12-31`。
• LightGBM 新训练路径现在会显式使用 `missing_value_strategy='native_nan'`：upstream missing、财报未披露、warm-up gap、跨表 join miss、以及 stale technical value 不再被统一改写成 `0.0 / 0.5 / 1.0 / 50.0`，而是作为 `NaN` 留给树模型自己学 split direction。与此同时，artifact metadata / disk `metadata.json` 会记录 `missing_value_strategy`，因此新 artifact 已经具备缺失值口径的 provenance 字段。
• LSTM 训练不再直接吞 raw `NaN` tensor，而是复用同一份 raw-NaN feature matrix 后，把每个 base feature 扩成 `[feature, feature__is_missing]` 两列；numeric branch 在 scaler 之后才把 `NaN -> 0.0`，missingness 本身通过 mask 列保留下来。因此 LSTM 现在的 missingness contract 是“mask-and-zero-impute after scaling”，不是旧版的无区分 `0.0` / `0.5` neutral fill。
• LSTM 训练也因此不再只是复用“同一份 feature schema”，而是复用“同一份 base feature schema + 明确的 missingness mask 扩展”。新的 `ModelVersion.feature_schema` 会包含这些 `__is_missing` 列，metadata 里也会记录 `missing_value_strategy='mask_and_zero_impute'`。线上运行是否已经切到这套 schema，取决于环境里当前 active/READY 的真实 LSTM artifact；如果数据库里还残留历史 stub row，LSTM runtime 现在会优先跳过 stub，而 `train_prediction_models()` 也会在刷新 ensemble baseline 时把 active stub 退役。
• 因此，`feature_names` / `feature_count` / `training_window_start/end` / `horizon` 这些信息，在当前 real LightGBM / LSTM artifact surface 上已经实际落盘或落库；一个代表性 host 路径 `models/lightgbm/3d_lgb-3d-2024-12-31/metadata.json` 当前存在，键集至少包括 `feature_names`、`horizon_days`、`training_window_start`、`training_window_end`、`version`、`trained_at`、`lgb_params`、`pruning`。
• 但 “artifact 保存 feature schema、训练窗口、universe policy、horizon” 这一整条还不能关闭，因为 `effective_universe_policy` 还没有被显式保存；与此同时，`label_definition`、`code_version` / `git_commit`、`data_snapshot_version` 也都没有出现在当前 active `LightGBMModelArtifact` metadata 里。
• 推理侧仍然不满足 strict schema validation。新 LightGBM artifact 会按 `missing_value_strategy` 走 raw-NaN path，而 legacy artifact 仍保持旧 sentinel fallback；LSTM 新 artifact 会把缺失值编码成 mask + finite tensor，但这仍然不是“遇到 schema drift 直接报错”的 fail-fast contract。也就是说，“strictly validate schema” 这一条还不能关闭。
• “不允许 feature 缺失后静默补 0 而不记录” 这条目前也只能算部分修复：LightGBM 新 artifact 已不再把 genuine missingness 无差别补 `0.0`，LSTM 新 artifact 会显式记录 `missing_value_strategy` 并把 missingness 编进 `__is_missing` 列；但 legacy artifact 仍会沿用旧行为，而且当前代码还没有统一的 warning / audit event 去记录所有 runtime schema mismatch。
• registry split 的持续污染源已经移除：`train_prediction_models()` 不再生成 `phase14_training_stub` 的 generic LightGBM/LSTM `ModelVersion` 行，而是只刷新 ensemble baseline；同时，它会把历史 active stub 行退役。对于老数据库里残留的 inactive stub 历史记录，现在也可以用一次性管理命令 `python manage.py purge_prediction_model_stubs --apply` 直接清掉。因此，这个问题现在主要收缩成 provenance 字段仍不完整的审计问题，而不是 runtime 还会继续被新的假 registry 行污染。

因此，Section 3C 当前仍然只能部分关闭：真实训练 artifact 已经能固定并保存 feature list / feature count / training window，并且新代码已经把 genuine missingness 从“统一 sentinel fill”推进到了 “LightGBM raw NaN / LSTM mask-aware finite tensor” 的更合理契约；`train_prediction_models()` 这条 registry pollution 路径也已经被移除。但 strict schema validation、统一 warning/audit、以及 `effective_universe_policy` / `label_definition` / `code_version` / `data_snapshot_version` 等 provenance 字段都还没补齐。另一个实际落地动作仍然是：需要重训并切换 active artifact，才能让 live 预测全面采用这套新 missingness contract。

───

D. 模型质量

所以要查：

• [x] 训练集/验证集/样本外测试集是否严格分时: manually checked
• [x] 没有用 2025 的信息去选 2024 前的模型
• [x] 不同 horizon 没有共用错误标签或错误 artifact

如果前 3 个漂亮，第 4 个长期不行，问题大概率还是：

• 特征无效
• 标签不对
• 或横截面口径脏

当前代码/样本审计结果（2026-05-17）：

• LightGBM 当前不满足“训练集 / 验证集 / 样本外测试集严格分时”。`train_lightgbm_models()` 会先把整窗 `X_df` 与 `labels_df` 对齐成 `X_train_aligned`，随后直接对全量矩阵执行 `scaler.fit_transform(X_train)`、`lgb.train(...)`，最后再在同一份 `X_train_scaled` 上回算 `accuracy = mean(y_pred == y_train)`。代码里没有独立 validation split，也没有 out-of-sample test slice。因此当前 active `LightGBMModelArtifact.metrics_json['accuracy']` 本质上是 in-sample training accuracy，不是严格时间切分下的 validation / OOS 指标。
• LightGBM 的 calibration 也不是 time-aware validation：训练后直接 `CalibratedClassifierCV(model, method='sigmoid', cv=5)` 作用在同一份训练矩阵上，代码里没有传入任何基于日期的 splitter。也就是说，即使这里不一定显式 random shuffle，它仍然不是“按时间顺序切 train / val / test”的协议。
• LSTM 的 split 逻辑相对更干净：`_train_single_horizon_lstm()` 会先按 `sample_dates` 升序排序，再做前 80% 训练、后 20% 验证；scaler 也只在 `X_train` 上 fit，再 transform `X_train / X_val`。因此它满足“训练 / 验证严格先后切开”，但仍然没有独立样本外测试集，所以第一条在系统层面还不能关闭。
• LSTM 仍然存在训练阶段的随机打乱：虽然 train / val 边界是按时间切开的，但 `DataLoader(train_ds, batch_size=128, shuffle=True)` 会把训练切片内部的 batch 顺序打乱。因此如果 checklist 的要求是“整个训练过程完全不打乱时间顺序”，第二条也还不能关闭；当前最多只能说“没有观察到 train / val 穿越式泄漏”。
• “没有用 2025 的信息去选 2024 前的模型” 当前主路径未发现明显时间穿越：active LightGBM artifacts 是 `lgb-3d/7d/30d-2024-12-31`，训练窗口都结束于 `2024-12-31`；LSTM registry 虽然仍挂着 active stub `lstm-2026-05-16`，但 runtime `_resolve_lstm_model_version(2026-05-16)` 实际解析到真实训练行 `lstm-2024-12-31`，其 `training_window_end` 同样是 `2024-12-31`。因此“post-2024 data 直接参与当前 main ML runtime artifact selection” 这一条当前未观察到。
• “不同 horizon 没有共用错误 label / artifact” 这一条当前可以先关闭：LightGBM 每个 horizon 都单独调用 `_create_labels_for_training(..., horizon)`、单独生成 `lgb-{horizon}d-...` version、单独注册 `LightGBMModelArtifact(horizon_days=...)`；LSTM 则按 `labels_by_horizon[horizon]` 构造标签，并在同一 version 目录下分别落 `3d_model.pt`、`7d_model.pt`、`30d_model.pt`。当前 host 文件系统里也确实存在 `models/lstm/lstm-2024-12-31/3d_model.pt`、`7d_model.pt`、`30d_model.pt` 三套独立 artifact。

因此，Section 3D 当前只能部分关闭：`不同 horizon 串 label / 串 artifact` 这一类错误当前未发现，`post-2024 information 直接选 pre-2025 model` 在主路径上也未发现；但更根本的验证纪律仍然不达标，因为 LightGBM 还没有真正的时间切分 validation / OOS，LSTM 也还没有独立 OOS test，而且训练阶段仍有 batch shuffle。换句话说，Section 3D 现在暴露的主问题不是 horizon 混线，而是 validation protocol 还不够硬。

───

四、backtest 层 audit

这一层查的是：回测有没有按你以为的方式执行。

A. 候选池生成

• [x] 每个交易日的 candidate pool 只来自 effective_universe(date)
• [x] top_n_metric 与 horizon 一致
• [x] up_threshold 真正生效
• [x] candidate_mode=top_n 真正生效
• [x] trade_score_scope=independent 真正生效

要抽查

• [x] 随机抽 5 个交易日，导出候选股票清单
• [x] 检查候选股票是否都属于当日 universe
• [x] 检查排序分数是否来自对应模型输出

当前代码/样本审计结果（2026-05-17）：

• 候选池的总闸门是 `_eligible_backtest_asset_ids(dt, cache)`：先取当日有 `OHLCV` 的 `asset_id`，再执行 `ensure_pit_membership_coverage([dt], ...)`，最后只保留 `point_in_time_union_asset_ids(dt)` 内的资产。`_build_heuristic_prediction_map()`、`_build_lightgbm_prediction_map()`、`_build_lstm_prediction_map()` 三条 prediction-source 路径都逐个迭代这同一个 helper，因此 candidate pool 的 universe gate 在 heuristic / LightGBM / LSTM 三条路径上是一致的。
• `top_n_metric` / `horizon` 对齐当前可以关闭：serializer 层 `BacktestRunSerializer.validate()` 会把 `up_prob_3d/7d/30d` 对齐到对应 `horizon_days`；runtime 侧 `_top_n_metric(run, fallback_horizon)` 也会再次把 `metric_horizon` 解析成 `3 / 7 / 30`。已有 regression `Phase15BacktestTests.test_backtest_serializer_aligns_horizon_with_top_n_metric` 覆盖了这条规范化逻辑。
• `up_threshold` 当前确实生效：`_build_heuristic_candidates()`、`_build_lightgbm_candidates()`、LSTM top-n 分支、以及 `trade_score` 分支都会先过滤 `up_probability < up_threshold` 的资产。对 read-only 样本日 `2024-02-06`，同一套 heuristic top-n 配置在 `up_threshold=0.55` 时返回 `5` 个 candidate，而把阈值提高到 `0.80` 后只剩 `3` 个，说明阈值不是写在参数里但没执行。
• `candidate_mode=top_n` 当前也确实生效：top-n 路径直接按 `top_n` 截断，`_max_positions()` 只在 `trade_score` 模式下生效。已有 regression `Phase15BacktestTests.test_top_n_mode_ignores_max_positions_but_trade_score_mode_honors_it` 已经覆盖“`top_n=2`、`max_positions=1` 仍返回 2 笔 top-n candidate，而 trade_score 模式只保留 1 笔”的差异；对样本日 `2024-02-06` 的 unsaved run 复核也得到 `rows=2`、`candidate_mode=['top_n']`。
• `trade_score_scope=independent` 当前可以关闭：`_build_trade_score_candidates()` 在 `scope != 'combined'` 时会明确写入 `trade_score_scope='independent'`，并按单一 prediction source 的 `trade_score` 排序。已有 regression `Phase15BacktestTests.test_trade_score_mode_supports_runtime_lstm_candidates_without_stored_predictions` 明确断言 payload 里的 `trade_score_scope='independent'`；对样本日 `2024-02-06` 的 unsaved heuristic run 复核结果是 `rows=3`，且所有 payload scope 都等于 `independent`。
• 对 `2024-01-01 .. 2026-04-30` 的 distinct trading dates 设 `random.seed(42)` 做 read-only 抽样，得到 5 个样本日：`2024-02-06`、`2024-06-26`、`2024-12-12`、`2025-01-14`、`2025-03-06`。使用 unsaved heuristic run（`candidate_mode='top_n'`、`top_n_metric='up_prob_7d'`、`top_n=5`、`horizon_days=7`、`up_threshold=0.55`）直接调用 `_pick_candidates()`，各日结果分别是：
• `2024-02-06`：`candidate_count=5`，`eligible_universe_count=300`，`violations=0`；candidate list = `688041, 300308, 300896, 688036, 300760`
• `2024-06-26`：`candidate_count=5`，`eligible_universe_count=300`，`violations=0`；candidate list = `688041, 002594, 688008, 600941, 002371`
• `2024-12-12`：`candidate_count=5`，`eligible_universe_count=566`，`violations=0`；candidate list = `603236, 002607, 600115, 002292, 300442`
• `2025-01-14`：`candidate_count=5`，`eligible_universe_count=563`，`violations=0`；candidate list = `688099, 603986, 002050, 603129, 002625`
• `2025-03-06`：`candidate_count=5`，`eligible_universe_count=562`，`violations=0`；candidate list = `688041, 002195, 300454, 603882, 688072`
• 上述 25 条 sampled candidates 都满足 `rank_value == signal_payload.up_probability`，同时 `signal_payload.top_n_metric='up_prob_7d'`、`signal_payload.horizon_days=7`，说明排序分数确实来自对应模型输出而不是别的列。另做一条 mismatch 复核：在 `2024-02-06` 上显式传 `top_n_metric='up_prob_30d'`、`horizon_days=7` 的 unsaved run，返回 payload 明确是 `top_n_metric='up_prob_30d'` 且 `horizon_days=30`，说明 top-n 排序真正跟 metric 对应 horizon 走，不会悄悄退回 7d。
• 当前唯一还值得记住的限制不是 candidate picker 本身，而是 validation harness coverage：`run_validation_backtests` 命令默认只生成 `top_n + horizon_days + up_threshold + prediction_source` 这一条基本路径，不会主动覆盖 `candidate_mode='trade_score'` 或自定义 `top_n_metric` 组合。这些非默认分支当前是靠单元测试 + 上面的 read-only manual audit 兜住的，而不是默认验证命令每天都跑到。

───

B. 交易执行逻辑

• [x] 开仓日只在 entry_weekdays
• [x] 平仓优先级明确：先 TP/SL 还是先持有期结束
• [x] holding_period_days 真正生效
• [x] 仓位上限、单笔资金比例、手续费、滑点都生效
• [x] 停牌/无法成交的处理有规则
• [x] create a fee model for transaction fees replacing the current .1% flat fee

你之前已经踩过：

• [x] TP/SL 配置传下去了没有
• [x] LightGBM 路径和 heuristic 路径执行逻辑是否一致
• [x] 参数写进 report 但实际上没执行，这种假生效必须排除

当前代码/测试审计结果（2026-05-17）：

• `run_backtest()` 的日循环固定是“先平仓，再判断是否开仓”。开仓只经过 `_should_enter_position(current_date, entry_weekdays)`；`entry_weekdays` 由 `_normalize_entry_weekdays()` 统一成 weekday index。现有 regression `test_backtest_supports_tuesday_thursday_top3_seven_day_hold` 复核到 buy dates 只出现在 `2026-04-07`、`2026-04-09` 两个 Tue/Thu 日，report 里也记录 `entry_weekdays=[1, 3]`。
• `holding_period_days` 当前真实生效：`_resolve_exit_date()` 会取“第一条 `>= entry_date + holding_period_days` 的交易日”作为计划平仓日，而不是简单按自然日强平。上面的 Tue/Thu 样本里，`holding_period_days=7` 对应 sell dates 是 `2026-04-14`、`2026-04-16`，与计划持有天数一致。
• 平仓优先级当前可以明确关闭：`_close_positions_for_date()` 先读当日 close；若启用 `enable_stop_target_exit`，先判 `STOP_LOSS`，再判 `TARGET_PRICE`，最后才落到 `SCHEDULED`。因此 TP/SL 在计划平仓日也会优先于普通持有期卖出。已有/新增 regressions 分别覆盖了 `TARGET_PRICE` 和 `STOP_LOSS` 分支。
• “停牌/无法成交” 现在也有明确规则，而且这一轮刚修掉一个真实缺口：开仓日如果 `buy_close` 缺失或 `<= 0`，`_open_positions_for_date()` 会直接 skip，不造假成交；平仓扫描日如果 `sell_close` 缺失或 `<= 0`，仓位会继续保留，直到后续出现第一条可成交 close 再执行卖出。原先这里存在“错过 scheduled exit 后不再重试”的缺陷，现已修复，并由 focused regression `test_scheduled_exit_retries_on_next_tradeable_price_after_invalid_close` 锁住。
• 仓位/成本参数当前确实执行，但要区分“默认真实费率”与“legacy override”两层语义：默认 backtest fee model 现在按 A 股现货费率走，买入是 `max(佣金 0.1‰, 5)` 加上 `经手费 0.0341‰ + 监管费 0.02‰ + 过户费 0.01‰`，卖出则在此基础上额外加 `印花税 0.5‰`；如果显式传 `fee_rate`，则回退到旧的对称 flat-fee override 供历史实验兼容。`capital_fraction_per_entry` 仍然通过 `deployable_capital = min(cash, initial_capital * capital_fraction)` 生效；`max_positions` 只在 `candidate_mode='trade_score'` 时作为并发持仓上限生效，`top_n` 模式仍由 `top_n` 控制。focused regressions 现在同时覆盖了默认 A 股费率（含 `5` 元最低佣金与卖出单边印花税）和 `fee_rate=1%` 的 legacy flat-fee override；后者样本里单笔总买入成本约等于 `25000`，买入成交价从 `10.00` 变成 `10.10`，卖出成交价从 `11.00` 变成 `10.89`。
• “TP/SL 配置传下去了没有” 当前可以关闭：LightGBM / heuristic / LSTM 三条 candidate builder 都把 `target_price` / `stop_loss_price` / `trade_score` 归一到同一份 `signal_payload`，`_open_positions_for_date()` 在落 BUY 前还会调用 `_backfill_prediction_trade_decision()` 补齐缺字段。现有 regressions `test_lightgbm_top_n_stop_target_exit_uses_propagated_levels`、`test_lightgbm_top_n_applies_trade_decision_policy_to_payload`、`test_open_positions_backfills_missing_prediction_trade_decision_fields` 都覆盖了这一点。
• “LightGBM 路径和 heuristic 路径执行逻辑是否一致” 当前未发现分叉：source-specific 差异只在 candidate/prediction payload 生成，真正的成交、持仓、TP/SL、scheduled exit、fee/slippage 都走同一个 `_open_positions_for_date()` / `_close_positions_for_date()` 执行层。上面的 LightGBM target test 和 heuristic stop-loss / invalid-entry tests 实际都落在同一执行分支。
• “参数写进 report 但实际上没执行” 当前未观察到假生效：`entry_weekdays` / `holding_period_days` / `enable_stop_target_exit` 仍会写进 `run.report`；这一轮又把 `fee_model` 和 `fee_parameters` 也写进了 report，同时 BUY/SELL trade metadata 会保存 `fee_breakdown`。`capital_fraction_per_entry`、structured fee 参数、legacy `fee_rate`、以及 `slippage_bps` 仍然由 `run_backtest()` 直接读取执行，`export_backtest_runs` 也会把 fee 参数导出到 `run_config_results.csv`。上面的 focused regressions 已经直接验证这些参数会改变成交价、手续费组成、和平仓后的实际 PnL。

───

D. 结果可追溯性

每个 backtest run 最好都能追到：

• [x] compare_backtest_run_id
• [x] model artifact id / model version id
• [x] horizon

这样你以后看到结果变化，能知道是：

• 数据变了
• membership 变了
• 训练变了
• 还是回测逻辑变了

当前代码/测试审计结果（2026-05-17）：

• `compare_backtest_run_id` 当前可以关闭：serializer 已经强校验 compare target 必须存在、已完成、同 strategy type、且 prediction source 兼容；comparison payload 也会把它回传给前端。这一轮又把它补进了 `run.report['compare_backtest_run_id']`，并补进 `export_backtest_runs -> run_config_results.csv`，所以 run-level 和 export-level 两条 traceability 路径现在是一致的。
• `horizon` 当前可以关闭：`BacktestRun.parameters['horizon_days']` 仍是 canonical config，trade signal payload 里每笔 BUY/SELL 也都带 `horizon_days`；这一轮又把归一化后的 `run.report['horizon_days']` 固化下来，因此单看 run 本身也能知道这是 3d / 7d / 30d 的哪一档回测。
• “model artifact id” 这一条在当前系统里应该更准确地理解为 “model artifact / model version reference”：LightGBM 路径用 `model_artifact_id`，heuristic / LSTM 路径用 `model_version_id`。这两类 reference 原本就在 trade `signal_payload` 里、也会被 `model_references.csv` 导出；这一轮又新增了 `run.report['model_references']` + `model_reference_count`，把当前 run 实际打到的 registry reference、version、horizon、feature_count、training_window_start/end 聚合到 run-level summary 上。因此这条可以按“reference id 可追溯”关闭，而不是狭义地只盯 LightGBM artifact id。
• benchmark 侧当前只能算部分可追：`run.report['benchmark']` 已经会保存 `strategy`、`source`、`benchmark_code`、`weighting_method`，足以区分“预计算 PIT benchmark” vs “runtime equal-weight fallback”；但还没有独立的 `benchmark_build_version` / benchmark artifact id，所以“benchmark build version” 仍不能关闭。
• feature schema / universe policy 也仍是部分可追：新加的 `run.report['model_references']` 会尽量从 registry metadata 里带出 `schema_version`、`effective_universe_policy`、`universe_version`、`label_definition`、`data_snapshot_version`、`code_version` / `git_commit` 等字段；但这些键是否存在取决于上游 LightGBM / LSTM artifact metadata 本身。当前 active artifacts 还没有把这些 provenance 字段补齐到位，所以 4D 后三条仍应继续保持 open。
• focused regressions 已覆盖这轮 traceability 改动：`test_run_backtest_supports_lightgbm_prediction_source` 现在会断言 run report 里确实写入 `compare_backtest_run_id`、`horizon_days`、`model_references`；`test_export_backtest_runs_includes_compare_backtest_run_id` 会断言 compare target 真实导出到 `run_config_results.csv`。因此，Section 4D 当前可以按“基础比较/模型/周期 provenance 已落 run-level + export-level，schema/universe/benchmark version 仍未补齐”来收口。


───

五、daily prediction & backtest 层 audit

这一层查的是：你每天真正给出的信号，跟训练/回测是不是同一种东西。

A. 当日 universe

• [x] daily prediction 的输入 universe 使用 effective_universe(today)
• [x] pre-2024-09-23 和 post-2024-09-23 逻辑一致
• [x] 不允许 default all-assets 扩大推理池

要抽查

• [x] 随机选一个历史日重放 daily prediction
• [x] 检查该日输入股票池是否与 backtest 同口径

当前代码/测试审计结果（2026-05-17）：

• 这一轮先确认了 ownership：heuristic `generate_predictions_for_date()`、LightGBM `generate_lightgbm_predictions_for_date()`、LSTM `generate_lstm_predictions_for_date()` 原本都直接遍历 `effective_universe_assets(as_of, ...)`，因此 membership 口径本来就走的是同一套 `effective_universe(date)` / PIT helper，不存在 default all-assets 的显式 fallback。已有 regression `test_generate_predictions_task_raises_when_required_pit_membership_is_missing` 也证明缺历史覆盖时会抛 `PITMembershipCoverageError`，不是静默放大推理池。
• 但 5A 审计时发现了一个真实 drift：backtest candidate pool 用的是“PIT effective universe ∩ 当日有 OHLCV 可交易资产”，而 daily prediction 之前只用 membership gate，没有再扣同日可交易性，因此会比 backtest 多出少量当日无有效 OHLCV 的名字。边界抽查时，这个差异在 `2024-09-20` 是 `300 vs 298`，缺口资产为 `600837.SH`、`601211.SH`；在 `2024-09-23` 是 `566 vs 564`，仍是同两只；在 `2025-01-14` 是 `564 vs 563`，缺口资产为 `000408.SZ`。
• 这个 drift 已在代码层修正：新增共享 helper `effective_universe_tradeable_asset_ids()` / `effective_universe_tradeable_assets()`，先走 `effective_universe(date)` 的 PIT membership guard，再与当日 `OHLCV(date)` 交集。现在 backtest `_eligible_backtest_asset_ids()` 和三条 daily prediction generator 都委托给同一套 helper，因此 daily prediction 与 backtest 已经回到同口径。
• pre-launch / post-launch 边界逻辑当前可以关闭：read-only 抽查显示 `2024-09-20` 只要求 `000300.SH`，有效 snapshot=`2024-09-02`，member count=`300`；`2024-09-23` 起同时要求 `000300.SH + 000510.CSI`，snapshot 分别为 `2024-09-02` / `2024-09-23`，member count=`300 + 500`，union count=`566`；`2025-01-14` 则解析到 `2025-01-02` / `2024-12-31` 两个 snapshot，union count=`564`。这些数都远小于当前 `Asset` 总量 `962`，因此“默认回退到 all assets” 当前未发现。
• “随机选一个历史日重放 daily prediction” 这一条也已补验证：在事务里对 `2025-01-14` 重放一次 heuristic daily prediction（`horizon=7`，最后 rollback），实际生成 `563` 条 prediction row；同日 `effective_universe_tradeable_asset_ids(2025-01-14)` 也是 `563`，`matches=True`。因此重放结果已经与 backtest 入口股票池一致，而不会再比 backtest 多出停牌/缺当日价格资产。
• focused regressions 现在覆盖了这轮 5A 修复：`test_generate_predictions_task_skips_effective_universe_assets_without_same_day_ohlcv` 锁住“daily prediction 不再给当日无 OHLCV 的 PIT 成员写预测”；`test_generate_lstm_predictions_task_filters_to_point_in_time_effective_universe` 和 `test_generate_lightgbm_predictions_task_filters_to_point_in_time_effective_universe` 锁住 LSTM / LightGBM 也不会脱离同一套 PIT universe gate。配合现有的缺 coverage fail-fast test，Section 5A 当前可以先关闭。

───

B. 当日特征快照

• [x] 缺失值处理与训练一致: implementation completed, pending for runtime artifact audit
• [x] macro/factor 数据使用当日可得版本
• [x] 没有因为某列缺失而整批 silent fallback

当前代码/测试审计结果（2026-05-17）：

• `macro/factor 数据使用当日可得版本` 当前可以关闭。heuristic `_feature_snapshot()` 取 `FactorScore` / `SentimentScore` 都是 `date__lte=as_of` 的最近一条；technical side 的 `latest_rsi` / `latest_momentum` / `latest_rs_score` 也都是 `date<=as_of` 再叠 freshness guard。LightGBM 单资产推理 `_extract_features_for_asset()` 对 `FactorScore`、`MacroSnapshot`、`SentimentScore` 同样都取 `<= as_of`；LSTM 推理则通过 `_build_inference_sequence() -> _create_feature_matrix(start_date, target_date, ...)`，对 factor / macro / sentiment 全部走 backward `merge_asof`。这一轮发现的真实问题是 heuristic `_resolve_context()` 之前没有带 `target_date`，历史重放时会错误读取“最新 active MarketContext”而不是历史当日 context；现已修正为按 `starts_at/ends_at` 解析 as-of context，并由 regression `test_generate_predictions_task_uses_market_context_as_of_target_date` 锁住。
• `缺失值处理与训练一致` 目前还不能关闭，但原因已经收敛得很明确：代码路径本身已经支持新契约，live artifact 还没有完全切过去。LightGBM 训练 `_create_feature_matrix(..., missing_value_strategy='native_nan')` 与推理 `_extract_features_for_asset(..., missing_value_strategy=artifact_metadata)` 在代码上是对齐的，现有 regression `test_extract_features_for_asset_preserves_nan_for_gappy_recent_history_under_native_nan_strategy` 也证明 gappy recent history 在 native-NaN 模式下会保留真实缺失而不是硬补 `0/1/50`。LSTM 训练同样先用 raw-NaN feature matrix，再扩出 `__is_missing` mask，最后才在 scaler 后 `NaN -> 0.0`；这一轮新增 regression `test_build_inference_sequence_masks_missing_feature_columns_without_aborting` 也证明 inference 端会为缺列补 mask，而不是直接失败。
• 但 runtime artifact audit 说明“代码支持”还不等于“线上当前就在用”：对 `2026-05-17` 读当前 inference surface，LightGBM `lgb-3d-2024-12-31`、`lgb-7d-2024-12-31`、`lgb-30d-2024-12-31` 通过 `_load_model_artifacts()` 读出来的 metadata 里都没有 `missing_value_strategy`；LSTM runtime resolve 到的 artifact 也同样没有这个键。因此推理时会落回 `legacy_neutral_fill` / legacy 路径，而不是新约定的 `native_nan` / `mask_and_zero_impute`。换句话说，5B 的剩余问题已经不是“代码没有 missingness contract”，而是“当前 active artifact metadata 还没把 contract 带进 runtime”，所以这条仍应保持 open，直到重新训练/激活带完整 metadata 的 LightGBM / LSTM artifact。
• `没有因为某列缺失而整批 silent fallback` 当前可以关闭。LightGBM 推理对缺失 feature key 走的是 `_resolve_prediction_feature_value()` 的逐列补值 / `NaN` 路径，不会因为某一列不存在就让整只股票或整批预测退回另一套逻辑；LSTM 推理 `_build_inference_sequence()` 会对缺失 base feature 自动补列，并生成 `__is_missing` mask。新加的 `test_build_inference_sequence_masks_missing_feature_columns_without_aborting` 已经直接覆盖“缺一个 feature column 仍然能建 sequence”这条规则。因此当前 remaining risk 是 legacy artifact 的静默 neutral fill，不是“因为缺一列就整批不推理/偷偷 fallback 到别的批处理路径”。

───

C. 模型选择

• [x] daily prediction 使用的 artifact id 明确
• [x] 3d/7d/30d 不会串模型
• [x] LightGBM、LSTM 版本切换有 registry 记录
• [x] active model registry 与实际调用一致

要抽查

• [x] 一次 daily prediction 结果里打印：
  • model_version
  • artifact_id
  • horizon
  • feature_count
  • universe_size

当前代码/数据审计结果（2026-05-18）：

• `daily prediction 使用的 artifact id 明确` 当前可以关闭。`2026-05-15` 已刷新 reference daily prediction，tradeable universe 是 567。LightGBM 每个 horizon 都直接落到 `LightGBMPrediction.model_artifact_id`，对应 `16/17/18 -> lgb-3d/7d/30d-2024-12-31-sec5b-v1`；heuristic / LSTM 则通过 `PredictionResult.model_version_id` 明确绑定 registry，分别是 active ensemble `14 -> ensemble-2024-12-31` 与 active LSTM `37 -> lstm-2024-12-31-sec5b-v1`。LSTM 每行 metadata 还保留了 horizon-specific `artifact_path`，可直接回溯到 `3d_model.pt` / `7d_model.pt` / `30d_model.pt`。
• `3d/7d/30d 不会串模型` 当前可以关闭。`2026-05-15` 抽样结果里，heuristic 3/7/30d 全部落到同一个 ensemble registry `14`；LightGBM 3/7/30d 只分别落到 artifact `16/17/18`，row `horizon_days` 与 artifact `horizon_days` 一致；LSTM 虽共用 model version `37`，但 metadata `artifact_path` 分别指向 `.../3d_model.pt`、`.../7d_model.pt`、`.../30d_model.pt`，没有串 horizon。
• `LightGBM、LSTM 版本切换有 registry 记录` 当前可以关闭。LSTM registry 当前保留了 inactive `lstm-2024-12-31`、`lstm-2026-05-16` 和 active `lstm-2024-12-31-sec5b-v1`；LightGBM registry 也保留了多轮 inactive 版本（`...-2024-12-31`、`...-core80-v1`、`...-regstrong-v1`、`2026-04-*`）以及 active `sec5b-v1` family，因此版本切换是有留痕的，不是覆盖写入。
• `active model registry 与实际调用一致` 当前可以关闭。daily prediction `2026-05-15` 的落库结果与 active registry 完全一致：ensemble `14`、LSTM `37`、LightGBM `16/17/18`。backtest 559/564/566 的 `model_references` 都指向 active ensemble `14`；560/565/562/563 的 `model_references` 分别指向 active LightGBM `16/17/18`。LSTM rerun `561` 在 worker restart 后已完成，但因为整段回测 `0 trade`，当前 `_collect_run_model_references()` 只从成交 `signal_payload` 汇总 provenance，所以 report 里仍是 `model_reference_count=0`。这说明当前 remaining gap 是 zero-trade run 的报告盲点，不是 runtime 选错模型。
• 本轮 `daily prediction` sample 已补齐：heuristic `model_version=ensemble-2024-12-31`、`artifact_id=n/a`、`feature_count=6`、`universe_size=567`；LSTM `model_version=lstm-2024-12-31-sec5b-v1`、`artifact_id=metadata.artifact_path`、`feature_count=78`、`universe_size=567`；LightGBM `model_version=lgb-{3,7,30}d-2024-12-31-sec5b-v1`、`artifact_id=16/17/18`、`feature_count=39`、`universe_size=567`。

───

D. 输出结果与落库

• [x] 每只股票的预测分数落库
• [ ] 入选 top_n 的原因可解释
• [ ] 预测结果可回放
• [ ] 第二天可以对照真实收益做 prediction audit

最好额外保存

• [x] raw score
• [ ] rank
• [ ] threshold pass/fail
• [ ] candidate selected true/false
• [x] prediction timestamp
• [ ] input data version

当前代码/数据审计结果（2026-05-18）：

• `每只股票的预测分数落库` 当前可以关闭。daily prediction 三条主路径都已经把每只股票、每个 horizon 的输出写入库里：heuristic / LSTM 走 `PredictionResult`，LightGBM 走 `LightGBMPrediction`。`2026-05-15` reference refresh 已验证三套 surface 都达到 `567 * 3` rows。当前 row 级持久化内容已经包含 `up/flat/down/confidence/predicted_label`，以及 trade-decision 层的 `target_price/stop_loss_price/risk_reward_ratio/trade_score/suggested`。
• `入选 top_n 的原因可解释` 目前还不能完全关闭，但本轮已经补上最缺的一段：对真正成交的 backtest BUY trade，`signal_payload` 现在会保存 `candidate_rank`、`candidate_rank_value/rank_value`、`candidate_selected=True`、`up_threshold/passed_up_threshold`，以及 trade-score mode 下的阈值判断，`export_backtest_runs` 也会把这些字段导出。因此“为什么这只已成交股票进了 top_n/portfolio”现在可以解释；但未成交的候选、被阈值筛掉的名字、以及 daily prediction 全量横截面排名仍没有单独的 selection snapshot，所以这一条仍应保持 open。
• `预测结果可回放` 目前还不能关闭。storage 层已经保留了 date、horizon、model_version / model_artifact、feature snapshot 和 timestamp；heuristic 也有 regression `test_generate_predictions_ignores_future_ohlcv_rows` 锁住历史重放不读未来数据。但系统还缺一个正式的 replay/audit workflow，可以指定 historical date + model reference，重算 daily prediction 并自动对比已存 rows。现在更多是“可手工重放”，还不是“有标准 replay 流程”。
• `第二天可以对照真实收益做 prediction audit` 目前不能关闭。代码里还没有 dedicated prediction-audit model / command / report，去把 `T` 日 prediction 与 `T+1` 或 horizon 到期后的真实收益自动 join、打标签、输出命中率 / 分层收益 / 校准结果；现在只能临时用 shell / SQL 或借 backtest 间接观察。
• `raw score` 对 ML surfaces 当前可以关闭。LightGBM 直接存 `raw_scores` 与 `calibrated_scores`；LSTM 把 `raw_scores` / `calibrated_scores` 放在 `PredictionResult.metadata`。heuristic 是规则模型，没有同类 logit/raw score 概念。
• `rank` 目前还不能关闭。dashboard list 会 on-the-fly 计算 `candidate_rank/candidate_rank_value`，选中的 backtest BUY trade 现在也会持久化 rank；但 daily prediction rows 没有保存全市场横截面 rank，未入选股票也没有统一的 selection snapshot。
• `threshold pass/fail` 目前还不能关闭。已成交的 backtest BUY trade 现在会保存 `passed_up_threshold`，trade-score mode 也会保存 `passed_trade_score_threshold`；但 daily prediction rows 以及未入选股票还没有统一保存 pass/fail 状态。
• `candidate selected true/false` 目前还不能关闭。已成交的 backtest BUY trade 现在会写 `candidate_selected=True`，但系统没有全量 candidate snapshot 去保存那些未入选或未成交股票的 `False`。
• `prediction timestamp` 当前可以关闭。`PredictionResult` / `LightGBMPrediction` 都自带 `created_at` / `updated_at`，serializer 也已经暴露这些字段，因此每条预测都有明确落库时间。
• `input data version` 目前还不能关闭。虽然 row 里已经保留 feature snapshot 和 model/artifact reference，但还没有把 `schema_version`、`effective_universe_policy`、`label_definition`、`data_snapshot_version`、`code_version/git_commit` 作为 prediction/backtest output metadata 的强制字段落库。

### version 0.1.12

**Objective**: package the current release candidate around lifecycle-aware historical data governance, strict point-in-time universe enforcement, expanded data-quality audits, richer macro term-structure coverage, and Linux-native local operations.

**Implemented Features**:

- Added lifecycle-aware market data surfaces and repair tooling:
  - extended `Asset` with `delist_date`; added official `ExchangeTradingCalendar` and `AssetSuspension` models, admin surfaces, and API exposure
  - added `backfill_trading_calendar`, `backfill_asset_suspensions`, and `reconcile_suspension_ohlcv_overlaps`
  - updated `sync_daily_a_shares` and `sync_asset_history` to sync official trading calendars, suspension days, explicit repair windows, and asset lifecycle metadata
  - expanded `backfill_ohlcv_history` to support report-driven repair windows, technical-indicator warm-up prefills, and effective-universe-entry warm-up backfills

- Enforced one canonical point-in-time universe contract across scoring, training, prediction, and backtests:
  - documented and codified `effective_universe(date)` as `CSI300` from `2010-01-04` through `2024-09-22`, then `CSI300 ∪ CSI A500` from `2024-09-23` onward
  - added `ensure_pit_membership_coverage`, required-index resolution, and explicit PIT coverage gap helpers in `apps/markets/benchmarking.py`
  - removed silent fallback-to-all-assets behavior from daily RS score refresh, factor scoring, runtime prediction, backtest asset selection, model-data backfill, and LightGBM/LSTM training windows
  - routed daily prediction through date-aware `effective_universe_assets(...)` instead of current tag-based asset selection

- Expanded data-quality validation from simple gap scans into lifecycle and dependency audits:
  - rewrote `validate_data_quality` around official exchange calendars instead of inferring trading dates from OHLCV
  - added reports for index-membership history gaps, monthly blank membership windows, benchmark and PIT benchmark gaps, OHLCV excused gaps, price anomalies, lifecycle issues, feature dependency gaps, feature-source as-of issues, effective-universe daily coverage, cross-sectional participant audits, and optional report scoping
  - added sampled upstream fundamental reconciliation against shared TuShare `daily_basic` / `fina_indicator` materialization logic
  - distinguished expected vs suspicious capital-flow nulls and labeled continuity gap reasons for moneyflow- and margin-derived features

- Refined feature backfills and technical-indicator persistence:
  - extracted shared fundamental materialization helpers so backfill and audit paths recompute the same as-of rows
  - made capital-flow backfills reuse existing raw lookback windows for single-day reruns instead of losing derived 5-day calculations
  - added `backfill_technical_indicators` for non-`RS_SCORE` indicator history and widened `TechnicalIndicator.value` precision so large `OBV` rows persist without overflow
  - added indicator warm-up helpers, RS-score prefill logic, and checkpoint/resume support with stage timing summaries for long-running `backfill_model_data` runs

- Expanded macro history and runtime macro semantics:
  - replaced the old `10Y - 2Y` slope with a fuller China yield surface (`6M/1Y/3Y/5Y/7Y/10Y/30Y`) and runtime `10Y - 3Y` spread
  - backfilled pre-`2016-07` monthly government yields from ChinaBond CSV, and pre-`2012-03` `CNY/USD` month-start rows from Yahoo CSV before TuShare takes over
  - constrained TuShare yield ingestion to `curve_type=0`, persisted per-field source metadata, and normalized monthly macro rows onto month-start dates
  - updated admin, serializer, runtime phase inference, and regression tests to match the new yield fields

- Improved native local-dev and ops workflows outside Docker:
  - moved Django settings and env loading onto `.envs/.local` plus `.venv` defaults, including `TUSHARE_TOKEN` and localhost Redis broker/cache defaults
    > **Note (0.1.13)**: This was later reversed by the single-`.env` consolidation.
    > The project now uses a single `.env` file at the repository root instead of
    > `.envs/.local`. See `docs/reference/env.md` for the current configuration.
  - added native helper scripts for backend, Celery worker, Celery beat, environment bootstrapping, and local stack verification
  - updated Vite/API/WebSocket defaults to use relative `/api` and `/ws` paths with local proxying
  - rewired smoke and staged-news-backfill shell helpers to run against the native virtualenv stack instead of `docker compose exec`

- Refreshed operator documentation:
  - updated `README.md` for the Linux-native local stack, benchmark-aware system status, and validated command inventory for backfill, validation, and training flows
  - updated `TechnicalGuide.md` to reflect the canonical effective-universe rules and the expanded macro yield surface



**Current Notes**:

- `validate_data_quality` now treats `ExchangeTradingCalendar` as the official source of opening days; OHLCV continuity excludes pre-listing dates, on/after-delist dates, and suspension-covered dates instead of inferring expectations from stored bars.
- Normal PIT-aware workflows now fail fast when required `IndexMembership` history is missing rather than silently widening to the full asset table.
- Historical warm-up repairs may intentionally reach before the shared floor only when the command is explicitly running a bounded repair or feature warm-up window.
- Stored `PredictionResult` and `LightGBMPrediction` rows remain available as the live daily snapshot layer for stock-level prediction flows, but backtests continue to regenerate heuristic, `LightGBM`, and `LSTM` candidates at runtime and no longer rely on prediction-history list/admin surfaces.

**Key Files**:
- `apps/markets/benchmarking.py`
- `apps/markets/tasks.py`
- `apps/markets/models.py`
- `apps/markets/management/commands/backfill_ohlcv_history.py`
- `apps/core/management/commands/validate_data_quality.py`
- `apps/analytics/management/commands/backfill_technical_indicators.py`
- `apps/factors/fundamental_materialization.py`
- `apps/macro/management/commands/backfill_macro_snapshots.py`
- `apps/prediction/management/commands/backfill_model_data.py`
- `scripts/_native_env.sh`
- `README.md`
- `TechnicalGuide.md`

**Focused Coverage Added**:
- Added or expanded regression coverage across `apps.core.tests`, `apps.markets.tests`, `apps.analytics.tests`, `apps.factors.tests`, `apps.macro.tests`, `apps.prediction.tests`, `apps.prediction.tests_lightgbm`, `apps.backtest.tests`, and `apps.sentiment.tests` for lifecycle-aware validation, PIT coverage enforcement, macro backfills, technical-indicator backfills, capital-flow warmups, checkpointed model-data backfills, and native runtime behavior.


### version 0.1.11

**Objective**: package the current `80`-file release candidate into a commit-ready `v0.1.11` focused on dual-index benchmark operations, point-in-time benchmark infrastructure, benchmark-aware backtest comparison, historical data-floor hardening, and refreshed model artifacts.

**Implemented Features**:

- Expanded the runtime universe from a single CSI 300 path into a CSI 300 + CSI A500 operational workflow:
  - added `membership_tags` on assets plus historical `IndexMembership`, official `BenchmarkIndexDaily`, and internal `PointInTimeBenchmarkDaily` storage
  - added `sync_index_constituents`, `sync_benchmark_index_history`, `build_pit_union_benchmark`, `onboard_csi_a500_universe`, and `rollout_csi_a500_universe`
  - rewired `sync_daily_a_shares` so it refreshes benchmark history, fans out OHLCV syncs, and then runs a post-sync universal refresh for PIT benchmark, capital flow, factor scores, and signals
  - added a monthly membership refresh path so constituent changes can backfill only affected assets instead of replaying the full universe every time

- Made model inputs and backtests respect the effective benchmark universe instead of the broad stored asset table:
  - factor scoring, heuristic predictions, LightGBM/LSTM runtime inference, RS score generation, and model-data backfills now filter to active or point-in-time union assets when membership history exists
  - LightGBM feature-matrix and label generation now apply PIT membership filters by trade date
  - backtest candidate selection now routes through one eligible-asset helper so heuristic, LightGBM, LSTM, and bottom-candidate flows all exclude out-of-universe assets on historical dates
  - unified `HIGH_RS_SCORE` persistence so historical backfills generate the same indicator and signal pattern as the daily RS score task

- Added an end-to-end benchmark comparison workflow for backtests:
  - new `comparison_curve` payloads now combine the selected run, stored compare target, extra overlay runs, and official CSI 300 / CSI A500 benchmark series
  - serializer validation now enforces compatible `compare_backtest_run_id` targets before a rerun is created
  - added `rerun_backtests_for_comparison` and `run_reference_benchmark_suite` to clone comparison reruns and export rolling benchmark bundles under `reports/`
  - Backtest Workbench now renders comparison charts, keeps compare-target state derived from reused runs, and supports chart-only extra overlays without mutating the stored run config

- Hardened historical data governance around one shared project floor:
  - centralized `get_historical_data_floor()` with default `2010-01-01`
  - backfill, rebuild, validation, and macro helpers now clamp or reject pre-floor windows consistently
  - added `purge_pre_floor_historical_data` for dry-run or destructive cleanup of legacy rows before the floor date
  - extended `FundamentalFactorSnapshot` with free-float and market-cap fields so PIT benchmark weighting and reprocessing no longer depend on stale partial snapshots

- Refreshed tracked model artifacts and local reporting conventions:
  - updated checked-in LightGBM `3d/7d/30d` artifacts with richer metadata, explicit version fields, and the current `2016-06-01..2024-12-31` training window
  - refreshed LSTM `3d/7d/30d` metrics and summary outputs for the current `2024-12-31` artifact family
  - `reports/` remains the local output root for benchmark suites and exports, and is now treated as generated workspace output instead of committed source

**Current Notes**:

- When PIT benchmark coverage is complete, backtests now use the precomputed `CSI300_CSIA500_PIT_UNION` equity curve; otherwise they fall back to the equal-weight daily-return benchmark.
- The frontend comparison panel is chart-only for extra overlays: adding extra runs does not mutate the stored backtest configuration.
- The default historical floor is now `2010-01-01`; older rows can be audited and purged, but new backfill and retrain windows will not expand below that floor without code changes.

**Key Files**:
- `apps/markets/tasks.py`
- `apps/markets/benchmarking.py`
- `apps/markets/management/commands/onboard_csi_a500_universe.py`
- `apps/markets/management/commands/rollout_csi_a500_universe.py`
- `apps/backtest/tasks.py`
- `apps/backtest/comparison.py`
- `apps/backtest/management/commands/run_reference_benchmark_suite.py`
- `apps/backtest/management/commands/rerun_backtests_for_comparison.py`
- `apps/prediction/tasks_lightgbm.py`
- `apps/core/management/commands/purge_pre_floor_historical_data.py`
- `frontend/src/pages/BacktestWorkbenchPage.tsx`
- `frontend/src/components/charts/BacktestComparisonChart.tsx`

**Focused Coverage Added**:
- Added regressions across `apps.markets.tests`, `apps.backtest.tests`, `apps.prediction.tests`, `apps.analytics.tests`, `apps.factors.tests`, and `apps.core.tests` for index sync, PIT benchmark refresh, comparison payloads, PIT candidate filtering, RS-score backfill parity, fundamental market-cap backfills, and pre-floor purge behavior.


### version 0.1.10

**Objective**: package the current `97`-file release candidate into a commit-ready `v0.1.10` with deterministic LightGBM retrains, trade-decision policy experiments, operator-facing dashboard improvements, data-quality validation tooling, and refreshed validation/report artifacts.

**Implemented Features**:

- Strengthened the LightGBM retrain path for repeat experiments on the same data window:
  - tightened the default regularization profile to `num_leaves=15`, `min_data_in_leaf=50`, `feature_fraction=0.6`, `lambda_l1=1.0`, and `lambda_l2=1.0`
  - added `version_tag` support to rebuild/training flows so `2016-06-01..2024-12-31` can be retrained without overwriting earlier artifact families
  - persisted tagged retrain metadata and saved parallel `core80-v1` and `regstrong-v1` artifact families under `models/lightgbm/`
  - added snapshot-driven cumulative-importance pruning with the `latest_snapshot_cumulative_80_core20_25` rule, producing active `core80-v1` artifacts with `20` retained features per horizon

- Extended trade-decision experimentation through the full backtest stack:
  - added policy-aware TP/SL controls (`include_near_round_target`, `min_target_return_pct`, `min_stop_distance_pct`) to backtest parameters, candidate generation, and stored signal payloads
  - applied the same policy path across heuristic, LightGBM, and LSTM runtime candidates so policy experiments stay comparable across model families
  - tightened serializer validation and regression tests around propagated TP/SL payloads and policy bounds

- Expanded release validation and report artifacts for current experiments:
  - generated validation packs under `reports/backtests_113_116_lightgbm_core80_v1/`, `reports/backtests_113_128_tpsl_rerun_20260427/`, `reports/backtests_117_128_grid12_tpsl_rerun_20260427/`, and `reports/tpsl_policy_experiment_123_118_20260427/`
  - exported run summary/config, model references, active LightGBM artifact lineage, macro monthly rows, comparison CSVs, and trade-level detail where needed
  - aligned the checked-in `reports/backtests_89_112_v0_1_9/` folder with the current light-export default instead of keeping stale detail-export files there

- Added operational data-quality validation tooling:
  - new command `apps/core/management/commands/validate_data_quality.py` writes CSV/JSON audit reports under `reports/` without mutating model data
  - added `apps/core/tests.py` coverage for report generation, alert delivery, and fail-on-critical behavior
  - added `apps/factors/management/commands/audit_model_data_quality.py` for targeted factor/fundamental/capital-flow/default-bucket audits during model debugging

- Improved the operator-facing frontend workflow:
  - added `IndicatorBoardPage` with sortable/filterable all-stocks monitoring across factor, indicator, and heuristic/LightGBM trade-decision columns
  - extracted reusable dashboard/backtest filter helpers into `frontend/src/lib/dashboardCandidateFilters.ts`
  - refreshed dashboard/backtest routing, API glue, i18n labels, and test coverage so dashboard candidate views stay aligned with the current backtest configuration model

- Refreshed release documentation:
  - updated `TechnicalGuide.md` with the new Data Metrics Sheet structure, explicit TP/SL mechanism/formulas, current export semantics, and current model/input coverage
  - updated `README.md` system-status counts and release highlights for the current `v0.1.10` candidate

**Current Notes**:

- The active `core80-v1` LightGBM family prunes `northbound_flow` and `northbound_flow_x_mom_5d` from all horizons while preserving older artifact families for audit.
- `PredictionResult` is now effectively the generic stored prediction surface for heuristic-style and LSTM outputs; stored LightGBM history remains in `LightGBMPrediction`.
- `export_backtest_runs` now defaults to light export (`run_summary.csv`, `run_config_results.csv`, `model_references.csv`); detailed trade/macro/comparison CSVs are opt-in.

**Key Files**:
- `apps/prediction/tasks_lightgbm.py`
- `apps/prediction/management/commands/rebuild_lightgbm_pipeline.py`
- `apps/prediction/odds.py`
- `apps/backtest/tasks.py`
- `apps/backtest/serializers.py`
- `apps/backtest/tests.py`
- `apps/backtest/management/commands/export_backtest_runs.py`
- `apps/core/management/commands/validate_data_quality.py`
- `apps/core/tests.py`
- `apps/factors/management/commands/audit_model_data_quality.py`
- `frontend/src/pages/DashboardPage.tsx`
- `frontend/src/pages/IndicatorBoardPage.tsx`
- `frontend/src/lib/dashboardCandidateFilters.ts`
- `TechnicalGuide.md`
- `README.md`

**Test Coverage / Verification**:
- `docker exec -i finance_analysis_django python manage.py test apps.backtest.tests.Phase15BacktestTests.test_run_backtest_supports_lightgbm_prediction_source apps.backtest.tests.Phase15BacktestTests.test_lightgbm_top_n_stop_target_exit_uses_propagated_levels apps.backtest.tests.Phase15BacktestTests.test_open_positions_backfills_missing_prediction_trade_decision_fields`
- `docker exec -i finance_analysis_django python manage.py test apps.factors.tests`
- Live Django-shell coverage snapshots refreshed on `2026-04-27` for `README.md` and `TechnicalGuide.md`
- `git --no-pager diff --check -- TechnicalGuide.md`

### version 0.1.9: Full Backfill + Runtime Backtest Validation ✓
**Objective**: 收尾 backfill/backtest 阶段，让数据、模型、文档和导出报告与当前实现保持一致，准备 v0.1.9 提交

**Implemented Features**:

- Refreshed the production data surface after the latest backfill:
  - confirmed 300 active listed assets with complete `list_date` coverage
  - extended OHLCV to 1,145,611 rows through `2026-04-24`
  - populated fundamental, moneyflow, margin-detail, factor-score, macro, sentiment, and LightGBM prediction history
  - refreshed TechnicalGuide data ranges with usage and missing-data impact notes
- Cleaned stale per-stock northbound fields:
  - removed `northbound_net_5d`, `northbound_net_10d`, and `northbound_net_20d` from `CapitalFlowSnapshot`
  - removed `northbound_flow_score` from `FactorScore`, dashboard DTOs, fixtures, and factor/backfill code paths
  - added cleanup migration `apps/factors/migrations/0004_remove_northbound_fields.py`
  - kept LightGBM `northbound_flow=0.5` as a neutral artifact-compatibility placeholder until active artifacts are retrained without that feature name
- Hardened release migration state:
  - pinned existing `FeatureImportanceSnapshot` index names to avoid generated-name drift
  - applied the northbound cleanup migration successfully in the live Docker database
- Completed model/backtest validation reporting:
  - documented active LightGBM artifacts trained on `2016-06-01..2024-12-31` with 3d/7d/30d accuracies `0.569238`, `0.513034`, and `0.593609`
  - documented active LSTM registry metrics: aggregate `0.465278`, 3d `0.547333`, 7d `0.428000`, 30d `0.420500`
  - captured post-retrain validation backtests 107-112 and comparison deltas against 101-106
- Added detailed backtest CSV export tooling:
  - new command `export_backtest_runs` exports run summary, flattened config/results, trades, macro monthly rows, model references, and comparison deltas
  - generated `reports/backtests_89_112_v0_1_9/` with 6 CSV files for BacktestRun IDs 89-112
  - summary/config sheets explicitly mark missing IDs 95-100 instead of silently skipping them
- Updated release documentation:
  - removed stale admin error text and stale factor assumptions from TechnicalGuide
  - cleaned README scratch notes and refreshed release status/counts
  - added v0.1.9 changelog notes for commit readiness

**Current Notes**:
- Backtests now generate heuristic, LightGBM, and LSTM candidates at runtime; stored LightGBM prediction coverage remains useful for API/dashboard/history but is not required by the backtest runner.
- `FactorScore.sentiment_score` is still stored, but default composite weighting keeps sentiment at `0.0` unless weights are changed.
- `northbound_flow` in LightGBM features is deliberately neutral and should disappear after the next artifact family is trained on a schema that omits it.

**Key Files**:
- `TechnicalGuide.md`
- `README.md`
- `apps/factors/models.py`
- `apps/factors/tasks.py`
- `apps/factors/migrations/0004_remove_northbound_fields.py`
- `apps/backtest/management/commands/export_backtest_runs.py`
- `apps/prediction/tasks_lightgbm.py`
- `apps/prediction/models_lightgbm.py`
- `apps/prediction/management/commands/backfill_model_data.py`
- `frontend/src/lib/api.ts`
- `reports/backtests_89_112_v0_1_9/`

**Test Coverage / Verification**:
- `docker exec -i finance_analysis_django python manage.py makemigrations --check`: no changes detected after pinning existing prediction index names
- `docker exec -i finance_analysis_django python manage.py migrate --plan`: confirmed only `factors.0004_remove_northbound_fields`
- `docker exec -i finance_analysis_django python manage.py migrate`: applied `factors.0004_remove_northbound_fields` successfully
- Database introspection: confirmed removed northbound columns are absent from `factors_capitalflowsnapshot` and `factors_factorscore`
- `docker exec -i finance_analysis_django python manage.py export_backtest_runs --start-id 89 --end-id 112 --output-dir reports/backtests_89_112_v0_1_9`: generated 6 CSV files; summary/config 24 requested run rows, comparison 6 rows, trades 11,255 data rows
- `docker exec -i finance_analysis_django python manage.py test apps.factors.tests apps.prediction.tests apps.prediction.tests_lightgbm apps.backtest.tests apps.macro.tests apps.markets.tests`: ran 64 tests, OK
- `cd frontend && npm test`: 1 test file passed, 3 tests passed
- Broad app-label Django test discovery still hits a namespace-package loader issue, so release verification uses explicit test modules.

### version 0.1.8: LSTM Pipeline + Multi-Model Backtest ✓
**Objective**: 让 LSTM 成为可训练、可推理、可回测的一等模型，并完善回测与个股页面操作体验

**Implemented Features**:
- Delivered real LSTM training (not registry-only):
  - added PyTorch LSTM retrain task with temporal sequence samples
  - added memory-safe chunked feature extraction and sample caps for long windows
  - added end-to-end command `rebuild_lstm_pipeline` with date-window/horizon controls
  - activated LSTM model version on `2000-01-01..2024-12-31`
- Delivered LSTM inference path parallel to LightGBM:
  - added runtime artifact loading + sequence feature build + probability inference
  - persisted LSTM predictions into `PredictionResult` with trade-decision fields
  - added API route family `/api/v1/lstm-predictions/` (stock, batch, train, recalculate)
- Expanded backtest source system:
  - added `lstm` as valid `prediction_source`
  - added frontend source option `all-models` that fans out one submission into heuristic/lightgbm/lstm runs
  - wired LSTM candidate selection into backtest runtime and serializer validation
- Backtest page UX upgrades:
  - run history: 10-row pagination
  - run history columns: added max drawdown + win rate
  - trade history: 10-row pagination
  - selected run metrics: expanded to full summary set (initial/final capital, returns, drawdown, sharpe, win-rate, trades, benchmark)
- Stock detail page UX upgrade:
  - added searchable stock selector (symbol/name filter)
- Documentation updates:
  - refreshed technical guide for LSTM training/inference and backtest source options

**Current Notes**:
- LSTM now supports both training and inference in production code paths.
- Backtest runner default source is now `all-models`.

**Key Files**:
- `apps/prediction/tasks_lstm.py`
- `apps/prediction/management/commands/rebuild_lstm_pipeline.py`
- `apps/prediction/views_lstm.py`
- `config/urls.py`
- `apps/backtest/tasks.py`
- `apps/backtest/serializers.py`
- `frontend/src/pages/BacktestWorkbenchPage.tsx`
- `frontend/src/pages/StockDetailPage.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/i18n.tsx`
- `TechnicalGuide.md`

### version 0.1.7: LightGBM Monitoring & Model Comparison ✓
_Historical note: git history contains two distinct releases tagged as `0.1.7`; both entries are preserved below instead of being renumbered retroactively._
**Objective**: 完成 LightGBM 的可观测性闭环，并把模型对比结果带到日常使用页面

**Implemented Features**:
- Deepened the LightGBM monitoring stack:
  - added historical `FeatureImportanceSnapshot` storage and admin visibility
  - exposed feature-importance trend API for recent model artifacts
  - registered LightGBM runs into `ModelVersion`
  - refreshed `EnsembleWeightSnapshot` from live LightGBM training output
- Expanded frontend model visibility:
  - added the dedicated model-monitoring page for model versions, LightGBM artifacts, prediction snapshots, feature trends, and ensemble weights
  - added heuristic-vs-LightGBM comparison sections on the dashboard and stock detail page
- Added missing LightGBM API parity:
  - stock-symbol LightGBM prediction endpoint aligned with the heuristic stock endpoint shape
  - feature-importance trends endpoint for historical inspection instead of only artifact JSON summaries
- Hardened live LightGBM training/inference behavior:
  - fixed nullable live feature extraction fallbacks in LightGBM training
  - added safe calibration fallback for raw LightGBM booster models
  - prepared historical factor-score and sentiment rows so live 3-day and 7-day LightGBM models could train successfully
- Live data population completed:
  - trained active 3-day and 7-day LightGBM models
  - generated 600 LightGBM prediction rows for the current date
  - populated feature-importance snapshots and ensemble-weight history in the database

**Current Notes**:
- 30-day LightGBM training still requires a deeper historical factor-score and sentiment backfill window.
- Dashboard probability chart section was removed after the comparison rollout to keep the page focused on candidate tables and model comparison data.

**Key Files**:
- `apps/prediction/tasks_lightgbm.py`
- `apps/prediction/views_lightgbm.py`
- `apps/prediction/serializers_lightgbm.py`
- `apps/prediction/models_lightgbm.py`
- `apps/prediction/tests_lightgbm.py`
- `frontend/src/pages/ModelMonitoringPage.tsx`
- `frontend/src/pages/DashboardPage.tsx`
- `frontend/src/pages/StockDetailPage.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/i18n.tsx`

---

### version 0.1.7: Trade-Decision Engine, LightGBM Parity, and Dashboard Consolidation ✓
**Objective**: 把概率预测升级为可执行的交易决策，并把 Heuristic / LightGBM 的对比收敛到统一的日常操作界面

**Implemented Features**:
- Added a shared odds and trade-decision engine:
  - introduced `apps/prediction/odds.py` to estimate `target_price`, `stop_loss_price`, `risk_reward_ratio`, `trade_score`, and `suggested`
  - derived trade levels from OHLCV history, Bollinger Bands, SMA support, and simple resistance rounding rules
  - stabilized `trade_score` so near-1.0 `p_up` values do not create unusable rankings
- Extended heuristic prediction outputs end to end:
  - added persistent trade-decision fields to `PredictionResult`
  - added migration `apps/prediction/migrations/0004_predictionresult_trade_decision_fields.py`
  - integrated trade-decision generation into heuristic prediction tasks and stock/batch APIs
  - extended bottom-candidate screener responses to expose and sort by `trade_score` and `risk_reward_ratio`
- Extended LightGBM prediction outputs to the same contract:
  - added persistent trade-decision fields to `LightGBMPrediction`
  - added migration `apps/prediction/migrations/0005_lightgbmprediction_trade_decision_fields.py`
  - reused the same odds engine in `tasks_lightgbm.py` so heuristic and LightGBM setup quality remain directly comparable
  - updated LightGBM serializers and stock/batch endpoints with the same additive fields
- Consolidated the frontend stock-selection workflow around the dashboard:
  - stock detail now compares heuristic and LightGBM setup quality by horizon alongside probability comparison
  - dashboard now surfaces top-candidate comparison with model-family and suggested-only filtering
  - added a new all-stocks indicator board backed by a composite `dashboard/stocks` API
  - removed the dedicated screener page from routing and navigation
- Added the new dashboard aggregation API:
  - introduced `DashboardStockViewSet` and `DashboardStockRowSerializer`
  - new endpoint returns one row per asset with factor scores, key indicators, sentiment, and heuristic/LightGBM trade summaries

**Validation**:
- Applied both prediction migrations successfully.
- Backend tests passed:
  - `apps.prediction.tests` and `apps.factors.tests`
  - `apps.prediction.tests_lightgbm` with 12 tests passing
  - `apps.analytics.tests` with 22 tests passing
- Frontend production build passed after the dashboard consolidation.
- Regenerated live heuristic predictions for `2026-04-15` so the new trade fields are populated on current rows.
- Regenerated live LightGBM predictions for `2026-04-15` with 600 asset-horizon rows updated.
- Live smoke checks confirmed:
  - `/api/v1/prediction/600519/?date=2026-04-15` returns target/stop/R:R/trade-score/suggested fields
  - `/api/v1/lightgbm-predictions/600519/?date=2026-04-15` and the LightGBM batch endpoint return the same trade-decision fields
  - `/api/v1/dashboard/stocks/?prediction_horizon=7&ordering=-composite_score` returns 300 mixed-source dashboard rows with factor, indicator, sentiment, and dual-model trade fields

**Current Notes**:
- The dashboard is now the primary stock-selection surface; the dedicated screener page was removed from the frontend.
- 30-day LightGBM training still depends on a deeper historical factor-score and sentiment backfill window.
- Frontend build still warns that `StockDetailPage.tsx` exceeds the default chunk-size warning threshold.

**Key Files**:
- `apps/prediction/odds.py`
- `apps/prediction/models.py`
- `apps/prediction/models_lightgbm.py`
- `apps/prediction/tasks.py`
- `apps/prediction/tasks_lightgbm.py`
- `apps/prediction/views.py`
- `apps/prediction/views_lightgbm.py`
- `apps/factors/views.py`
- `apps/analytics/views.py`
- `apps/analytics/serializers.py`
- `frontend/src/pages/DashboardPage.tsx`
- `frontend/src/pages/StockDetailPage.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/App.tsx`
- `frontend/src/components/layout/AppShell.tsx`
- `frontend/src/i18n.tsx`

### version 0.1.6 Realtime Auth & Sentiment Availability Hardening ✓
**Objective**: 提升实时告警鉴权稳定性并消除个股情绪分缺失

**Implemented Features**:
- WebSocket auth compatibility enhancement:
  - backend alerts consumer now supports JWT query-token authentication fallback for `/ws/alerts/`
  - frontend socket URL now appends JWT token when available
- Sentiment data availability enhancement:
  - `calculate_daily_sentiment` now creates neutral fallback `ASSET_7D` entries for active assets without article aggregation
  - prevents stock detail sentiment from showing persistent `N/A` when source article coverage is sparse
- End-to-end checks completed:
  - frontend production build passed
  - Django system checks passed
  - target APIs returned `200` for OHLCV / sentiment / macro-current smoke checks

**Key Files**:
- `apps/analytics/consumers.py`
- `apps/sentiment/tasks.py`
- `frontend/src/hooks/useAlertsSocket.ts`
- `frontend/src/lib/api.ts`
- `frontend/src/pages/AlertCenterPage.tsx`

---

### version 0.1.5 Dashboard, Stock, Macro, Alerts UX Fixes ✓
**Objective**: 修复前端关键页面的可用性与数据可读性问题

**Implemented Features**:
- Dashboard UX updates:
  - moved `Top N Bottom Candidates` table above charts
  - replaced chart section title with formal title: `Top Candidate Probability Outlook`
- Stock Detail data fixes:
  - expanded OHLCV loading from single-page fetch to paginated aggregation for long-history K-line rendering
  - increased frontend OHLCV request limit for deeper historical chart coverage
- Macro Context display improvements:
  - upgraded list layout to table with explicit headers (`Macro Phase`, `Event Tag`, `Status`)
  - changed ambiguous `N/A` event display to explicit `No event tag configured`
- Alert Center connection-state UX:
  - introduced reconnecting state and exponential backoff reconnect behavior
  - status now distinguishes `Connected` / `Reconnecting...` / `Disconnected`

**Key Files**:
- `frontend/src/pages/DashboardPage.tsx`
- `frontend/src/pages/StockDetailPage.tsx`
- `frontend/src/pages/MacroContextPage.tsx`
- `frontend/src/pages/AlertCenterPage.tsx`
- `frontend/src/hooks/useAlertsSocket.ts`
- `frontend/src/lib/api.ts`
- `frontend/src/i18n.tsx`

### version 0.1.4 Data Sync & Coverage Recovery ✓
**Objective**: 恢复核心行情数据覆盖并完成阶段性全链路数据校准

**Implemented Features**:
- Added and verified TuShare-based incremental/backfill workflow in local Docker runtime
- Re-synced representative core symbols (e.g. `600519`, `000001`, `300750`) and completed broader constituent recovery runs
- Introduced throttled batch resume strategy to handle provider rate limits and improve long-run sync stability
- Re-ran model data dependencies for current date:
  - technical/signal recalculation
  - macro snapshot/context refresh
  - sentiment/concept-heat recalculation
  - factor score regeneration
  - prediction result regeneration
- Performed post-sync consistency checks for OHLCV/assets coverage and key feature tables

**Key Files / Modules**:
- `apps/markets/tasks.py` — market/asset history synchronization pipeline
- `apps/analytics/tasks.py` — technical indicator and signal recalculation tasks
- `apps/macro/tasks.py` — macro snapshot and context refresh tasks
- `apps/sentiment/tasks.py` — sentiment and concept heat recalculation
- `apps/factors/tasks.py` — factor score regeneration
- `apps/prediction/tasks.py` — prediction regeneration

### version 0.1.3: UX & Performance Enhancements ✓
  - Route-level code splitting via lazy-loaded page modules + `Suspense` fallback
  - Lightweight Auth Settings panel in sidebar:
    - JWT token input
    - API key input
    - Persistence mode selector (`local`, `session`, `none`)
    - Save/Clear controls wired to storage
  - Additional live dashboard aggregates:
    - completed backtest runs count
    - average bottom-candidate probability from screener endpoint
- Mobile-responsive layout and custom visual theme
- HGFS-compatible npm scripts (no `.bin` symlink dependency)

### version 0.1.2: Dynamic Series Completion ✓
  - Dashboard probability chart now uses live prediction series (top screener symbol fallback)
  - Stock Detail removed static fallback chart/probability data; now displays API-driven series only
  - Chart components now show explicit empty-state messages when API data is unavailable

### version 0.1.1: Real Backend Data Wiring ✓
  - Dashboard metrics now fetched from live endpoints (`macro`, `concept heat`, `signals`, `alert-events`)
  - Stock Detail page now fetches live asset lookup, OHLCV candles, prediction probabilities, and sentiment scores
  - Bottom Screener now renders live `/screener/bottom-candidates/` results
  - Macro Context page now renders live `/macro/contexts/` entries
  - Backtest Workbench now renders live `/backtest/` runs
  - Alert Center now combines live WebSocket stream + `/alert-events/` API history

### version 0.1.0: Frontend Dashboard ✓
**Objective**: 面向用户的可视化操作界面

**Implemented Features**:
- New `frontend/` app bootstrapped with **Vite + React + TypeScript**
- Dashboard routing and application shell with 6 pages:
  - Dashboard
  - Stock Detail
  - Bottom Screener
  - Macro Context
  - Backtest Workbench
  - Alert Center
- Charting integration:
  - **TradingView Lightweight Charts** for candlestick rendering
  - **Recharts** for multi-horizon probability visualization
- Real-time alert stream hook using **WebSocket** (`/ws/alerts/` configurable by env)
- API utility layer with JWT + API Key header support (`Authorization`, `X-API-Key`)
**Key Files**:
- `frontend/src/App.tsx` — route definitions and page composition
- `frontend/src/components/layout/AppShell.tsx` — nav shell
- `frontend/src/components/charts/CandlestickChart.tsx` — K-line chart
- `frontend/src/components/charts/ProbabilityChart.tsx` — prediction probability chart
- `frontend/src/pages/*.tsx` — page implementations
- `frontend/src/hooks/useAlertsSocket.ts` — real-time alert stream
- `frontend/src/lib/api.ts` — API client with auth headers
- `frontend/src/index.css` — responsive dashboard styling
- `frontend/package.json` — dependencies and HGFS-safe scripts

**Run Frontend**:
```bash
cd frontend
npm install --no-bin-links
npm run dev
```

Open: `http://localhost:5173`

### version 0.0.16: API Documentation & Developer Portal ✓
**Objective**: Complete API documentation ecosystem and developer portal

**Implemented Features**:
- **OpenAPI 3.0 schema auto-generation** via `drf-spectacular`:
  - Machine-readable schema: `GET /api/v1/schema/`
  - Interactive Swagger UI: `GET /api/v1/schema/swagger-ui/`
  - ReDoc interface: `GET /api/v1/schema/redoc/`
- **API Key management** — new `developer` app:
  - `DeveloperAPIKey` model with SHA-256-hashed keys (plain text never stored)
  - Key format: `fa-<40 hex chars>` with `key_prefix` stored for display
  - Sandbox mode flag for read-only / synthetic-data workflows
  - Configurable expiry (`expires_at`)
  - `POST /api/v1/developer/keys/` — mint new key (raw key returned once)
  - `GET /api/v1/developer/keys/` — list own keys
  - `DELETE /api/v1/developer/keys/{id}/` — soft-revoke (sets `is_active=False`)
  - `POST /api/v1/developer/keys/{id}/rotate/` — revoke + mint replacement
- **`X-API-Key` authentication** — `APIKeyAuthentication` class added to `DEFAULT_AUTHENTICATION_CLASSES`; works alongside JWT
- **API Changelog** — `ChangelogEntry` model:
  - `GET /api/v1/developer/changelog/` — public endpoint, no auth required
  - Filterable by `?version=`, `?change_type=`, `?is_breaking=true`
  - Change types: ADDED / CHANGED / DEPRECATED / REMOVED / FIXED / SECURITY
- **`SPECTACULAR_SETTINGS`** configured with title, description, version, Swagger UI persistence, and rate-limit documentation table

**Key Files**:
- `apps/developer/models.py` — `DeveloperAPIKey`, `ChangelogEntry`
- `apps/developer/authentication.py` — `APIKeyAuthentication`
- `apps/developer/views.py` — `DeveloperAPIKeyViewSet`, `ChangelogEntryViewSet`
- `apps/developer/serializers.py` — key create/read serializers
- `apps/developer/admin.py` — admin registrations
- `apps/developer/tests.py` — version 0.0.16 test coverage
- `apps/developer/migrations/0001_initial.py` — initial migration
- `config/settings/base.py` — `SPECTACULAR_SETTINGS`, `APIKeyAuthentication` in auth classes
- `config/urls.py` — schema + developer portal routes
- `requirements/base.txt` — `drf-spectacular==0.27.2`

**Test Coverage**: 16/16 tests passing (100%)

### version 0.0.15: Backtesting & Strategy Validation ✓
**Objective**: Validate strategy effectiveness and prediction quality with historical simulation

**Implemented Features**:
- New `backtest` app with execution and trade log models:
  - `BacktestRun` for strategy configuration, async lifecycle, and performance metrics
  - `BacktestTrade` for per-trade records (BUY/SELL, fee, slippage, realized PnL)
- Async backtest engine task:
  - `run_backtest(backtest_run_id)` processes historical OHLCV data by date range
  - Supports strategy modes: `PREDICTION_THRESHOLD`, `BOTTOM_CANDIDATE`, `MACRO_ROTATION`
  - Simulates fee/slippage and computes `total_return`, `annualized_return`, `max_drawdown`, `sharpe_ratio`, `win_rate`
- Backtest API endpoints:
  - `GET /api/v1/backtest/`
  - `POST /api/v1/backtest/` (create run and queue async execution)
  - `GET /api/v1/backtest/{id}/`
  - `POST /api/v1/backtest/{id}/rerun/`
  - `GET /api/v1/backtest/{id}/trades/`
  - `GET /api/v1/backtest-trades/?backtest_run={id}`
- Admin support for run monitoring and trade inspection

**Key Files**:
- `apps/backtest/models.py` — `BacktestRun`, `BacktestTrade`
- `apps/backtest/tasks.py` — async simulation and performance calculation pipeline
- `apps/backtest/views.py` — backtest run/trade APIs with rerun and trade-list actions
- `apps/backtest/serializers.py` — run/trade serializers and validations
- `apps/backtest/admin.py` — admin registrations
- `apps/backtest/tests.py` — version 0.0.15 test coverage
- `apps/backtest/migrations/0001_initial.py` — initial migration

**Test Coverage**: 4/4 tests passing (100%)

---

### version 0.0.14: ML Prediction Engine ✓
**Objective**: Build the core prediction engine to estimate direction probabilities for each stock



**Implemented Features**:

**Tier 1: Heuristic Baseline** (original version 0.0.14):
  - New `prediction` app with core models:
    - `ModelVersion` for prediction model registry and version lifecycle
    - `PredictionResult` for daily stock-level probability snapshots by horizon
  - Heuristic Prediction API endpoints:
    - `GET /api/v1/prediction/{stock_code}/`
    - `POST /api/v1/prediction/batch/`
    - `POST /api/v1/prediction/recalculate/`
    - `GET /api/v1/prediction-model-versions/`
  - Multi-horizon outputs for 3/7/30-day direction probabilities:
    - `up`, `flat`, `down`, `confidence`, and `predicted_label`
  - Feature fusion from prior phases (10-13):
    - technical momentum/relative-strength signals
    - multi-factor bottom-probability signals
    - macro context tags
    - sentiment scores
  - Baseline ensemble training workflow:
    - weekly model version refresh task (Saturday 04:00 UTC)
    - daily prediction generation task (18:00 UTC)
    - macro context/event tag override support

**Tier 2: LightGBM Parallel ML Engine** (version 0.0.14 Extension):
  - Production-ready parallel prediction pipeline alongside heuristic baseline
  - Dual-model architecture for side-by-side accuracy comparison and gradual adoption
  - Core Models:
    - `LightGBMModelArtifact` — model persistence registry with version tracking, metrics, and feature importance
    - `LightGBMPrediction` — daily predictions with raw and calibrated probability scores
    - `EnsembleWeightSnapshot` — historical weight tracking for accuracy-weighted ensemble
  - LightGBM Training Pipeline:
    - Automatic feature extraction from Phases 10–13 infrastructure (technical, factors, macro, sentiment)
    - StandardScaler normalization + CalibratedClassifierCV (Platt scaling) for probability calibration
    - Weekly retraining task (Sunday 05:00 UTC, offset from heuristic)
    - Disk-based model persistence (pickle + JSON) under `/models/lightgbm/`
  - LightGBM Inference & API:
    - Per-asset async inference (`generate_lightgbm_prediction_for_asset`)
    - Batch daily predictions (`generate_lightgbm_predictions_for_date`)
    - Three new endpoints:
      - `POST /api/v1/lightgbm-predictions/train/` — Queue model retraining
      - `POST /api/v1/lightgbm-predictions/recalculate/` — Queue daily prediction generation
      - `POST /api/v1/lightgbm-predictions/batch/` — Batch predictions for multiple stocks
      - `GET /api/v1/lightgbm-predictions/{stock_code}/` — Single-stock LightGBM predictions
      - `GET /api/v1/lightgbm-models/` — Model artifact registry (read-only)
      - `GET /api/v1/ensemble-weights/` — Ensemble weight history
  - Independent from heuristic: both systems run in parallel with own DB tables and schedules
  - Enables production risk-mitigation: fallback to heuristic if LightGBM underperforms

**Key Files**:
  - `apps/prediction/models.py` — `ModelVersion`, `PredictionResult` (heuristic baseline)
  - `apps/prediction/models_lightgbm.py` — `LightGBMModelArtifact`, `LightGBMPrediction`, `EnsembleWeightSnapshot`
  - `apps/prediction/tasks.py` — heuristic training and prediction generation
  - `apps/prediction/tasks_lightgbm.py` — LightGBM training, inference, and model persistence
  - `apps/prediction/views.py` — heuristic prediction endpoints
  - `apps/prediction/views_lightgbm.py` — LightGBM prediction endpoints
  - `apps/prediction/serializers.py` — heuristic serializers
  - `apps/prediction/serializers_lightgbm.py` — LightGBM serializers
  - `apps/prediction/tests.py` — version 0.0.14 heuristic tests (5 tests, all passing)
  - `apps/prediction/tests_lightgbm.py` — version 0.0.14 LightGBM tests (7 tests, all passing)
  - `apps/prediction/migrations/0001_initial.py` — initial heuristic schema
  - `apps/prediction/migrations/0002_ensembleweightsnapshot_lightgbmmodelartifact_and_more.py` — LightGBM schema (applied)

**Test Coverage**: 12/12 tests passing (100%)
  - Heuristic: 5/5 tests ✓
  - LightGBM: 7/7 tests ✓ (including routing fix for train endpoint)

### version 0.0.13: NLP Sentiment & News Intelligence ✓
**Objective**: Add sentiment intelligence from news and concept heat signals, and feed sentiment into multi-factor ranking

**Implemented Features**:
- New `sentiment` app with core models:
  - `NewsArticle` for finance news ingestion
  - `SentimentScore` for article-level and aggregated sentiment
  - `ConceptHeat` for concept/theme popularity tracking
- Sentiment API endpoints:
  - `GET /api/v1/sentiment/`
  - `GET /api/v1/sentiment/latest/`
  - `POST /api/v1/sentiment/recalculate/`
  - `GET /api/v1/sentiment/news/`
  - `POST /api/v1/sentiment/news/ingest/`
  - `GET /api/v1/sentiment/concepts/`
  - `GET /api/v1/sentiment/concepts/top/`
- Daily sentiment pipeline tasks:
  - News ingest task (`ingest_latest_news`)
  - Daily article/asset/market sentiment scoring (`calculate_daily_sentiment`)
  - Concept heat computation (`calculate_concept_heat`)
  - Unified daily dispatcher (`run_daily_sentiment_pipeline`)
- Sentiment factor integration into version 0.0.11:
  - `FactorScore` now stores `sentiment_score` and `sentiment_weight`
  - Factor scoring task supports `sentiment_weight`
  - Asset 7-day sentiment aggregate participates in composite score
- Daily Celery Beat schedule for sentiment pipeline

**Key Files**:
- `apps/sentiment/models.py` — NewsArticle, SentimentScore, ConceptHeat
- `apps/sentiment/tasks.py` — sentiment scoring and concept heat tasks
- `apps/sentiment/views.py` — sentiment/news/concept API viewsets
- `apps/sentiment/serializers.py` — sentiment serializers
- `apps/sentiment/tests.py` — version 0.0.13 test coverage
- `apps/sentiment/migrations/0001_initial.py` — initial migration
- `apps/factors/tasks.py` — sentiment factor integration in composite scoring
- `apps/factors/models.py` — sentiment fields on FactorScore

---

### version 0.0.12: Macro & Event-Driven Context Engine ✓
**Objective**: Introduce macro context and event-driven overlays as a global adjustment layer for model scoring

**Implemented Features**:
- New `macro` app with three core models:
  - `MacroSnapshot` for macro time-series snapshots
  - `MarketContext` for active environment labels
  - `EventImpactStat` for historical tagged-event return statistics
- Macro API endpoints:
  - `GET/POST /api/v1/macro/snapshots/`
  - `POST /api/v1/macro/snapshots/sync/`
  - `GET/POST /api/v1/macro/contexts/`
  - `GET /api/v1/macro/contexts/current/`
  - `POST /api/v1/macro/contexts/refresh/`
  - `GET/POST /api/v1/macro/event-impacts/`
- Macro phase inference task using PMI + yield-curve logic:
  - `RECOVERY`, `OVERHEAT`, `STAGFLATION`, `RECESSION`
- Context-aware weight service for downstream ranking models
- version 0.0.11 integration:
  - Bottom-candidates endpoint now accepts `macro_context` and `event_tag`
  - Recalculate endpoint applies context-adjusted weights before queuing scoring
  - List endpoint returns `adjusted_bottom_probability_score` and `context_applied`
- Monthly macro sync scheduled via Celery Beat

**Key Files**:
- `apps/macro/models.py` — MacroSnapshot, MarketContext, EventImpactStat
- `apps/macro/services.py` — macro/event weight adjustment logic
- `apps/macro/tasks.py` — monthly sync and context refresh tasks
- `apps/macro/views.py` — macro APIs and custom actions
- `apps/macro/serializers.py` — macro serializers
- `apps/macro/tests.py` — version 0.0.12 test coverage
- `apps/macro/migrations/0001_initial.py` — initial migration

---

### version 0.0.11: Multi-Factor Alpha Model ✓
**Objective**: Build a configurable multi-factor stock ranking engine for bottom-candidate screening

**Implemented Features**:
- New `factors` app with dedicated data models for factor ingestion and scoring
- Fundamental snapshot model (`PE`, `PB`, `ROE`, `ROE QoQ`)
- Capital flow snapshot model (northbound net flow, main-force net flow, margin-balance change)
- Composite `FactorScore` model storing normalized component scores and bottom-probability output
- Bottom-candidate screener endpoint:
  - `GET /api/v1/screener/bottom-candidates/`
  - `POST /api/v1/screener/bottom-candidates/recalculate/`
- Parameterized weighted scoring (`financial_weight`, `flow_weight`, `technical_weight`)
- Admin support for all version 0.0.11 models

**Scoring Engine**:
- Financial factors:
  - PE percentile score (lower PE -> higher score)
  - PB percentile score (lower PB -> higher score)
  - ROE trend score from `roe_qoq`
- Capital-flow factors:
  - northbound net flow rank
  - main-force net flow rank
  - margin-balance change rank
- Technical factors:
  - RSI oversold signal
  - close near Bollinger lower band
  - version 0.0.10 oversold signal (`OVERSOLD_COMBINATION`)
- Weighted aggregation into `composite_score` and `bottom_probability_score`

**Data / API Models**:
- `FundamentalFactorSnapshot`
- `CapitalFlowSnapshot`
- `FactorScore`

**Key Files**:
- `apps/factors/models.py` — factor data and scoring models
- `apps/factors/tasks.py` — daily factor scoring task
- `apps/factors/views.py` — factor ingestion and bottom-candidate APIs
- `apps/factors/serializers.py` — factor serializers
- `apps/factors/tests.py` — version 0.0.11 test coverage
- `apps/factors/migrations/0001_initial.py` — initial migration

---

### version 0.0.10: Advanced Technical Indicators Expansion ✓
**Objective**: Extend the analytics engine with signal detection across moving averages, Bollinger Bands, volume-price relationships, and momentum factors

**Implemented Signals** (`SignalEvent` model):

| Category | Signal Type | Trigger Condition |
|---|---|---|
| Moving Averages | `GOLDEN_CROSS` | MA5 crosses above MA20 |
| Moving Averages | `DEATH_CROSS` | MA5 crosses below MA20 |
| Moving Averages | `MA_BULL_ALIGN` | MA5 > MA10 > MA20 > MA60 |
| Moving Averages | `MA_BEAR_ALIGN` | MA5 < MA10 < MA20 < MA60 |
| Bollinger Bands | `BB_SQUEEZE` | Bandwidth < 5% (volatility compression) |
| Bollinger Bands | `BB_BREAKOUT_UP` | Close above upper band |
| Bollinger Bands | `BB_BREAKOUT_DOWN` | Close below lower band |
| Bollinger Bands | `BB_RSI_OVERBOUGHT` | Close ≥ upper band×0.98 AND RSI > 70 |
| Bollinger Bands | `BB_RSI_OVERSOLD` | Close ≤ lower band×1.02 AND RSI < 30 |
| Volume | `VOLUME_SPIKE` | Volume > 2× 20-day average |
| Volume | `VOLUME_PRICE_DIVERGENCE` | ≥3% price move with opposing OBV trend |
| Momentum | `MOMENTUM_UP_5D` | 5-day return > +5% |
| Momentum | `MOMENTUM_DOWN_5D` | 5-day return < -5% |
| Momentum | `HIGH_RS_SCORE` | Top 20% by 20-day return (cross-asset) |
| Reversal | `OVERSOLD_COMBINATION` | RSI < 30 + near lower BB + volume contraction |

**New Indicator Values** (stored as `TechnicalIndicator`):
- `MOM_5D`, `MOM_10D`, `MOM_20D` — period return as a decimal fraction
- `RS_SCORE` — normalized relative strength rank (0–1) vs. all assets

**API Endpoints**:
- `GET /api/v1/signals/` — paginated list, filterable by `asset` and `signal_type`
- `GET /api/v1/signals/recent/?days=7` — signals from the last N days
- `POST /api/v1/signals/recalculate/` — queue full signal recalculation (HTTP 202)

**Technical Implementation**:
- `SignalEvent` model with 15 `SignalType` choices, `unique_together` on `(asset, timestamp, signal_type)`
- 6 new Celery tasks: `calculate_ma_signals_for_asset`, `calculate_bollinger_signals_for_asset`, `calculate_volume_signals_for_asset`, `calculate_momentum_signals_for_asset`, `calculate_reversal_signals_for_asset`, `calculate_rs_scores_for_all_assets`
- Batch dispatcher: `calculate_signals_for_all_assets`
- Celery Beat: daily at 16:00 UTC (after A-share market close at 15:00 CST)

**Key Files**:
- `apps/analytics/models.py` — `SignalEvent` model
- `apps/analytics/tasks.py` — all version 0.0.10 signal tasks
- `apps/analytics/views.py` — `SignalEventViewSet` with `recent` and `recalculate` actions
- `apps/analytics/serializers.py` — `SignalEventSerializer`
- `apps/analytics/migrations/0004_phase10_signal_events.py` — migration
- `config/settings/base.py` — `calculate-signals-daily` Celery Beat schedule

---

### version 0.0.9: Stock Screeners & Alerts ✓
**Objective**: Automated screening and notification system

**Implemented Features**:
- Pre-built screener endpoints:
  - Overbought/Oversold stocks
  - Breakout candidates
  - High volume stocks
  - Trend reversal signals
- Saved screener templates API
- Alert rule management API (price + indicator conditions)
- Alert event history API
- Multi-channel notifications:
  - Email
  - SMS via webhook provider integration hook
  - WebSocket push notifications
- Celery periodic alert checks with configurable cooldown

**Technical Implementation**:
- `AlertRule`, `AlertEvent`, `ScreenerTemplate` models
- Celery tasks: `check_alert_rules`, `send_alert_notifications`
- WebSocket endpoint: `/ws/alerts/` (Channels + Redis channel layer)
- Router endpoints:
  - `/api/v1/screeners/`
  - `/api/v1/screener-templates/`
  - `/api/v1/alerts/`
  - `/api/v1/alert-events/`

**Key Files**:
- `apps/analytics/models.py` - version 0.0.9 data models
- `apps/analytics/tasks.py` - Alert evaluation and dispatch tasks
- `apps/analytics/views.py` - Screener and alert APIs
- `apps/analytics/consumers.py` - WebSocket alert consumer
- `config/asgi.py` - ASGI protocol routing for HTTP + WebSocket
- `config/settings/base.py` - Channels and periodic schedule config

---

### version 0.0.8: Advanced Technical Indicators ✓
**Objective**: Expand analytical capabilities

**Implemented Indicators**:
- **Bollinger Bands** (`BBANDS`) with upper/middle/lower levels
- **Moving Averages**: `SMA` and `EMA` (multi-period)
- **Stochastic Oscillator** (`STOCH`)
- **ADX** (trend strength with `plus_di` / `minus_di`)
- **OBV** (On-Balance Volume)
- **Fibonacci Retracement** (`FIB_RET`) levels

**Features**:
- Configurable indicator parameters via `/api/v1/indicators/recalculate/`
- Historical indicator values query support via date filters
- Indicator comparison API: `/api/v1/indicators/compare/`
- Specialized indicator endpoints for trend/overbought/oversold and Fibonacci levels

**Key Files**:
- `apps/analytics/tasks.py` - Indicator calculation tasks (including Fibonacci)
- `apps/analytics/views.py` - Indicator compare/recalculate/Fibonacci endpoints
- `apps/analytics/models.py` - Unified `TechnicalIndicator` storage model
---

### version 0.0.7: User Management & Subscriptions ✓
**Objective**: Multi-tenant user system with subscription tiers

**Implemented Features**:
- User registration with email verification flow
- Password reset via token-based email link
- User profile management (`phone_number`, `company`, subscription info)
- Subscription tier model: `FREE`, `PRO`, `PREMIUM` with Stripe-ready fields (`stripe_subscription_id`, `stripe_customer_id`)
- Subscription lifecycle: `is_active`, `start_date`, `end_date`, `auto_renew`, `cancel()` method
- API usage tracking via `APIUsageMiddleware` — records endpoint, method, status, IP for every `/api/v1/` call
- Usage stats dashboard — daily/monthly counts, top endpoints, tier-based daily limit
- Admin panel with `UserAdmin` inlines, `SubscriptionAdmin` with activate/deactivate bulk actions

**Technical Implementation**:
- `UserProfile` (OneToOne), `Subscription` (FK), `APIUsage` (FK, nullable for anonymous) models
- `UserProfile` auto-created on user save via Django signal
- `subscription_tier`, `is_pro`, `is_premium` computed properties on `UserProfile`
- Tier-aware daily limits (`FREE: 100`, `PRO: 1000`, `PREMIUM: 10000`)
- Router endpoints:
  - `POST /api/v1/users/register/`
  - `POST /api/v1/users/verify-email/`
  - `POST /api/v1/users/password-reset/`
  - `POST /api/v1/users/password-reset-confirm/`
  - `GET/PATCH /api/v1/users/profile/me/`
  - `GET /api/v1/users/subscriptions/current/`
  - `GET /api/v1/users/usage/stats/`

**Key Files**:
- `apps/users/models.py` — UserProfile, Subscription, APIUsage, SubscriptionTier
- `apps/users/views.py` — Registration, email/password, profile, subscription, usage viewsets
- `apps/users/serializers.py` — All serializers with validation
- `apps/users/middleware.py` — APIUsageMiddleware
- `apps/users/signals.py` — Auto-create UserProfile
- `apps/users/admin.py` — UserAdmin with inlines, SubscriptionAdmin with bulk actions

---

### version 0.0.6: Production Readiness & SaaS Features ✓
**Objective**: Authentication, authorization, and rate limiting

**Achievements**:
- **JWT Authentication**:
  - SimpleJWT integration
  - Access tokens (60-minute lifetime)
  - Refresh tokens (7-day lifetime with rotation)
  - Token blacklist for security
- **Authentication Endpoints**:
  - `POST /api/v1/auth/token/` - Obtain tokens
  - `POST /api/v1/auth/token/refresh/` - Refresh access token
  - `POST /api/v1/auth/token/verify/` - Verify token validity
- **Rate Limiting (SaaS Tiers)**:
  - Anonymous users: 100 requests/day
  - Authenticated users: 1,000 requests/day
  - Premium users: 10,000 requests/day (extensible)
- **Security**:
  - Redis-backed caching
  - Stateless authentication
  - Token rotation and blacklisting
  - Custom throttle classes for tier management

**API Security Model**:
- Read-only access for unauthenticated users
- Full access for authenticated users
- Tier-based rate limiting
- JWT bearer token authentication

**Key Files**:
- `config/settings/base.py` - JWT and throttling configuration
- `apps/core/throttling.py` - Custom throttle classes
- `config/urls.py` - Authentication endpoints


---

### version 0.0.5: REST API with Caching ✓
**Objective**: Expose data through secure, performant API

**Achievements**:
- **API Endpoints**:
  - `/api/v1/markets/` - List markets
  - `/api/v1/assets/` - Search and filter stocks
  - `/api/v1/ohlcv/` - Historical price data
  - `/api/v1/indicators/` - Technical indicators
  - `/api/v1/indicators/top_rsi/` - Top 20 overbought stocks
  - `/api/v1/indicators/bottom_rsi/` - Top 20 oversold stocks
- **Features**:
  - Pagination (50 items per page)
  - Advanced filtering with `django-filter`
  - Search functionality
  - Redis caching (2 hours for data, 5 minutes for dynamic endpoints)
  - Optimized queries with `select_related()`
  - Separate list/detail serializers for performance
- **Browsable API** for easy testing and documentation

**API Capabilities**:
- Filter assets by market: `?market__code=SSE`
- Search by name/symbol: `?search=平安`
- Date range filtering for OHLCV and indicators
- Custom aggregations (top/bottom RSI)

**Key Files**:
- `apps/markets/serializers.py` - Market/Asset/OHLCV serializers
- `apps/markets/views.py` - Market API ViewSets
- `apps/analytics/serializers.py` - Indicator serializers
- `apps/analytics/views.py` - Analytics API ViewSets
- `config/urls.py` - API routing

---

### version 0.0.4: Financial Analysis & Indicators ✓
**Objective**: Calculate technical indicators using TA-Lib

**Achievements**:
- TA-Lib C library compiled and installed in Docker container
- **TechnicalIndicator Model**: Stores calculated indicators with timestamps
- **Calculation Tasks**:
  - `calculate_rsi_for_asset()` - 14-period RSI calculation
  - `calculate_macd_for_asset()` - MACD calculation (12, 26, 9)
  - `calculate_indicators_for_all_assets()` - Batch processing dispatcher
- Pandas-based data pipeline for efficient computation
- Successfully calculated 668 indicators (334 RSI + 334 MACD)

**Indicators Calculated**:
- **RSI (Relative Strength Index)**: 14-period, identifies overbought/oversold conditions
- **MACD (Moving Average Convergence Divergence)**: 12/26/9 periods, trend indicator

**Key Files**:
- `apps/analytics/models.py` - TechnicalIndicator model
- `apps/analytics/tasks.py` - Indicator calculation tasks
- `compose/local/django/Dockerfile` - TA-Lib installation

---

### version 0.0.3: Data Ingestion Engine ✓
**Objective**: Automated data synchronization with AkShare

**Achievements**:
- Celery worker and beat services configured
- Distributed task architecture (dispatcher + workers)
- **Main Tasks**:
  - `sync_daily_a_shares()` - Dispatcher task for CSI 300 stocks
  - `sync_asset_history()` - Individual stock data processor
- Successfully imported 334 CSI 300 stocks with historical data
- Idempotent data handling (no duplicates on re-runs)
- Error handling and logging

**Data Coverage**:
- **Markets**: Shanghai (SSE), Shenzhen (SZSE), Beijing (BSE)
- **Stocks**: 334 CSI 300 constituents
- **Historical Data**: ~1 year of daily OHLCV data per stock

**Key Files**:
- `apps/markets/tasks.py` - Data ingestion tasks
- `config/celery.py` - Celery configuration
- `compose/local/django/start-celeryworker` - Worker startup script
- `compose/local/django/start-celerybeat` - Scheduler startup script

---

### version 0.0.2: Bilingual Data Modeling ✓
**Objective**: Create core financial data models with translation support

**Achievements**:
- **Market Model**: Stock exchanges (SSE, SZSE, BSE)
- **Asset Model**: Individual stocks with bilingual names
- **OHLCV Model**: Daily price data (Open, High, Low, Close, Volume)
- Integrated `django-modeltranslation` for field-level translations
- Django Admin interface with translation support
- Database migrations applied successfully

**Data Model**:
```
Market (3 exchanges)
  ├── Asset (334 CSI 300 stocks)
      └── OHLCV (Historical daily data)
```

**Key Files**:
- `apps/markets/models.py` - Core financial models
- `apps/markets/translation.py` - Translation configuration
- `apps/markets/admin.py` - Admin interface

---

### version 0.0.1: Foundation & Docker Setup ✓
**Objective**: Production-ready project structure with containerization

**Achievements**:
- Split settings architecture (`base.py`, `local.py`, `production.py`)
- Django project initialized with custom apps structure
- Docker Compose configuration:
  - Django application service
  - PostgreSQL 15 database
  - Redis for caching and Celery broker
  - Celery worker and beat services
- Environment variable management with `django-environ`
- Static files handling

**Key Files**:
- `config/settings/base.py` - Core Django settings
- `docker-compose.yml` - Service orchestration
- `compose/local/django/Dockerfile` - Django container build
- `compose/local/django/start*.sh` - Service startup scripts

---
