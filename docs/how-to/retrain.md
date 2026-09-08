# How to retrain, promote, and roll back models

Covers the full lifecycle: decide → retrain → validate → promote → roll back if
needed. The live registry state is generated in
[`../reference/models.md`](../reference/models.md); the reasoning behind the
registry split is in `TECHNICAL_GUIDE.md`.

---

## 1. When to retrain

Retrain when any of these is true:

| Trigger | Why |
| --- | --- |
| A Stage 3 backfill materially changed stored features | The deployed artifacts were fitted against the old feature distribution |
| The feature contract changed (new column, renamed column, new missing-value strategy) | Old artifacts silently fall back to legacy neutral fills |
| The effective universe expanded (e.g. CSI A500 onboarding) | Training samples must come from the same universe inference will see |
| Validation backtests show drift versus the benchmark | The usual symptom that prompts the others |
| `validate_data_quality` reports repaired critical findings | Repaired history invalidates the previous fit |

Do **not** retrain on a fixed calendar. There is no scheduled retrain in
`CELERY_BEAT_SCHEDULE` — training is always deliberate.

---

## 2. Which registry table is authoritative

This is the single most misunderstood part of the system.

| Table | Authority over | Granularity |
| --- | --- | --- |
| `LightGBMModelArtifact` | **LightGBM deployment** — which artifact inference and backtests load | One row per `(horizon_days, version)`; `is_active` is per horizon |
| `ModelVersion` | Registry metadata and labelling for LSTM, ensemble, and historical LightGBM | One row per `(model_type, version)` |

Consequences:

- To know which LightGBM model is deployed, read `LightGBMModelArtifact`
  filtered on `is_active=True` — **not** `ModelVersion(model_type=LIGHTGBM)`.
- `ModelVersion(model_type=LIGHTGBM)` is refreshed during training but does not
  maintain one simultaneously active row per horizon. It can show a different
  generation than what inference actually loads.
- LSTM deployment resolves through `ModelVersion(model_type=LSTM, is_active=True)`,
  whose `artifact_path` points at a directory holding `3d_model.pt`,
  `7d_model.pt`, `30d_model.pt`, and `summary.json`.

Both views are generated in [`../reference/models.md`](../reference/models.md).
Read that file before quoting any version string.

---

## 3. Version tags

`--version-tag` is appended to the generated version string:

```
lgb-{horizon}d-{training_end_date}[-{version_tag}]
lstm-{training_end_date}[-{version_tag}]
```

`(horizon_days, version)` is unique, so **retraining against the same cutoff
date requires a distinct tag** or the run collides with the existing family.
Tags are free-form labels — nothing in the code enforces a vocabulary. What each
existing family actually contains is recorded in
[`../reference/models.md`](../reference/models.md); summarising the observed
convention:

| Tag shape | Denotes |
| --- | --- |
| _no tag_ | Baseline run for that cutoff date |
| `regstrong-v1` | Relative-strength feature family; also used as the importance **source** for later pruning runs |
| `core80-v1` | Pruned with the cumulative-80%-importance rule and a 20-feature floor |
| `sec5b-v1` | Remediation of the missing-value contract audit (introduced `native_nan`) |
| `stored-ti-v1`, `stored-ti-v2` | Stored-technical-indicator parity iterations |

When you create a new family, pick a tag that says **what changed**, not the
date — the date is already in the version string. Record the reason in
`CHANGELOG.md`.

---

## 4. Retrain LightGBM

```bash
python manage.py rebuild_lightgbm_pipeline \
  --start-date 2016-06-01 --end-date 2024-12-31 \
  --horizons 3,7,30 \
  --version-tag <what-changed>
```

| Flag | Effect |
| --- | --- |
| `--horizons` | Comma-separated subset of `3,7,30` |
| `--skip-backfill` | Do not call `backfill_model_data` first |
| `--skip-sentiment` | Exclude sentiment from the backfill stage |
| `--version-tag` | Suffix on the generated version string |
| `--use-snapshot-pruning` | Prune features using the latest active `FeatureImportanceSnapshot` per horizon |

Without `--skip-backfill` the command runs `backfill_model_data` internally, so
a retrain can silently take hours on a cold feature store. If you have just
completed Stage 3 of [`backfill.md`](backfill.md), pass `--skip-backfill`.

### Pruning behaviour

| Mode | `pruning.rule` in metadata | Features retained |
| --- | --- | --- |
| default | `none` | All engineered features (currently 39) |
| `--use-snapshot-pruning` | `latest_snapshot_cumulative_80_core20_25` | The prefix reaching 80% cumulative importance, floored at 20 and capped at 25 |

Pruning reads the **latest active `FeatureImportanceSnapshot`** per horizon. If
no snapshot exists, pruning is skipped and the full feature set is retained.
That means the first training run of a new feature family cannot be pruned
meaningfully — it produces the snapshots the next run consumes.

### Training pipeline stages

1. Build the feature matrix for the training window.
2. Build direction labels per horizon.
3. Align labels to feature rows.
4. Prune to the core feature set (when enabled).
5. Standardise with `StandardScaler`.
6. Train the multiclass booster.
7. Calibrate probabilities.
8. Write artifact files, registry rows, and feature-importance snapshots.
9. Refresh ensemble weights from the latest metrics.

Training runs on the `train-lightgbm` Celery queue when invoked asynchronously.

---

## 5. Retrain LSTM

```bash
python manage.py rebuild_lstm_pipeline \
  --start-date 2016-06-01 --end-date 2024-12-31 \
  --horizons 3,7,30 \
  --sequence-length 20 \
  --asset-chunk-size 60 \
  --max-samples-per-horizon 30000
```

| Flag | Effect |
| --- | --- |
| `--sequence-length` | Rolling window per sample (default 20) |
| `--asset-chunk-size` | Assets processed per feature-extraction chunk — controls peak memory |
| `--max-samples-per-horizon` | Cap on sampled sequences per horizon — controls peak memory |
| `--skip-backfill` / `--skip-sentiment` | As for LightGBM |

Artifacts land in `models/lstm/<version>/` as `3d_model.pt`, `7d_model.pt`,
`30d_model.pt`, and `summary.json`. The active `ModelVersion` row is updated via
`update_or_create`, and each successful retrain refreshes ensemble weights
against the currently active LightGBM artifacts.

LSTM consumes the same shared feature extraction as LightGBM, converted into
sequences, and expands each base feature with an `__is_missing` mask column —
which is why the LSTM `feature_count` in `summary.json` is roughly double the
LightGBM count.

Training runs on the `train-lstm` queue.

---

## 6. The missing-value contract

Artifacts record how they expect gaps to be handled at inference time:

| `missing_value_strategy` | Meaning | Where |
| --- | --- | --- |
| `native_nan` | LightGBM consumes real `NaN`; no neutral fill | LightGBM `metadata.json` |
| `mask_and_zero_impute` | Build `__is_missing` masks, then zero-fill after scaling | LSTM `summary.json` |
| `ABSENT` (key missing) | Inference falls back to the legacy neutral-fill path | Older artifacts |

**An artifact without the key does not fail — it silently degrades** to legacy
neutral fills (`0`/`1`/`50`/`0.5` depending on the feature). That is a correctness
problem disguised as a working model: training and inference disagree about what
a gap means, and nothing logs it.

After any retrain, confirm the key is present:

```bash
python manage.py export_documentation_facts --only models
grep -c ABSENT docs/reference/models.md
```

The generated sheet prints `ABSENT` explicitly for artifacts missing the key.
Any active artifact showing `ABSENT` should be retrained before it is trusted.

---

## 7. Validate before promoting

Never promote on training accuracy alone.

```bash
# 1. Rolling validation runs across all three sources
python manage.py run_validation_backtests \
  --start-date 2024-01-01 --end-date <today> \
  --sources heuristic,lightgbm,lstm

# 2. Exported comparison bundle
python manage.py run_reference_benchmark_suite \
  --start-date 2024-01-01 --end-date <today> \
  --output-dir reports/reference_suite_<label>

# 3. Regenerate the registry sheet and read the diff
python manage.py export_documentation_facts --only models
git diff docs/reference/models.md
```

Compare the new family against the incumbent on **out-of-sample** return,
Sharpe, win rate, and the in-sample/out-of-sample alpha gap. A large gap means
overfitting regardless of how good the training accuracy looks.

### Accuracy sanity bounds

Directional accuracy for this problem sits in a narrow band. Across the
registered families, plausible 3/7/30-day accuracies are roughly **0.42–0.58**.

Treat anything above ~0.65 as **leakage until proven otherwise**. The registry
contains historical rows with 3-day accuracy near 0.95 and 7-day near 0.88;
those are not good models, they are look-ahead bugs, and they also poisoned the
ensemble-weight snapshot that consumed them. Never promote a family whose
accuracy is implausibly high — investigate the feature pipeline first.

---

## 8. Promote

Promotion means setting `is_active=True` on the new artifacts and clearing it on
the incumbents, per horizon.

**Preferred:** retrain with the pipeline commands, which activate the artifacts
they produce.

**Manual promotion** (e.g. promoting an already-trained family) uses the Django
admin at `/admin/prediction/lightgbmmodelartifact/`:

1. Filter by `horizon_days`.
2. Clear `is_active` on the current active row.
3. Set `is_active=True` on the new row.
4. Repeat for each horizon — **exactly one active row per horizon**.
5. For LSTM, do the equivalent on `/admin/prediction/modelversion/` filtered to
   `model_type=LSTM`.

Verify afterwards:

```bash
python manage.py export_documentation_facts --only models
```

The "Active LightGBM artifacts" table must show exactly three rows, one per
horizon, all `READY`, all with a missing-value strategy that is not `ABSENT`.

---

## 9. Roll back

Rollback is promotion in reverse — there is no separate mechanism, and no
artifact is ever deleted by the pipeline.

1. Identify the last known-good version from
   [`../reference/models.md`](../reference/models.md) or the admin changelog.
2. Clear `is_active` on the bad artifacts and set it on the previous generation,
   per horizon.
3. Re-run `export_documentation_facts --only models` and confirm.
4. Re-run a short validation backtest to confirm the rollback restored the
   expected metrics.
5. Record the rollback and its cause in `CHANGELOG.md`.

Because every generation is retained, rollback is immediate and does not require
retraining. This is the main reason the registry never overwrites: a version tag
produces a new family rather than replacing one.

### Artifact path portability

Stored `artifact_path` values are **absolute paths written on the host that
trained the artifact**. The registry currently contains paths from a Docker
container (`/app/...`), a Linux home directory, and a Windows OneDrive checkout.
The generated models sheet reports whether each path resolves on the current
host.

If you move the repository or switch hosts:

- Artifacts trained on the current host resolve and work.
- Artifacts trained elsewhere will not resolve. Either retrain on this host
  (which rewrites the paths) or copy the artifact directories to the recorded
  locations.
- A model that loads by version lookup but fails to find its file will error at
  inference time, not at promotion time — so verify path resolution as part of
  promotion.

---

## 10. Ensemble weights

Weights are refreshed automatically at the end of a successful LightGBM or LSTM
retrain, proportional to each model's accuracy over a trailing 60-day basis
window, quantised to four decimals. With no usable accuracy the fallback is
approximately equal weights.

Two nuances that cause confusion:

- The **active ensemble `ModelVersion`** is dated to the retrain window end and
  carries the latest retrain metrics.
- The **latest `EnsembleWeightSnapshot`** is a chronological monitoring row dated
  to when it was computed, reflecting the 60-day basis available then.

They are different things and will usually disagree. Both appear in the
generated models sheet. Ensemble weights are monitoring and reporting only —
backtest ranking does not consume them.

A snapshot can inherit a bad basis: if it was computed while a leaked artifact
was active, its weights encode that artifact's inflated accuracy. Check the
`basis` accuracy columns in the generated sheet before trusting a snapshot.
