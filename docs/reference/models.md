<!--
  GENERATED FILE - DO NOT EDIT BY HAND.
  Regenerate: python manage.py export_documentation_facts
  Source of truth: the prediction registry tables plus models/*/metadata.json on disk.
-->

# Model Registry and Artifacts

_Generated: 2026-09-07T15:17:02Z_

Read this sheet before trusting any accuracy or feature count quoted in prose. `LightGBMModelArtifact` is the authority for per-horizon LightGBM deployment; `ModelVersion` is a higher-level registry that does **not** keep one simultaneously active row per horizon. The rationale for that split, and the version-tag naming convention, are explained in `TECHNICAL_GUIDE.md`; the promote/rollback procedure is in `docs/how-to/retrain.md`.

## Active LightGBM artifacts

| Horizon | Version | Status | Trained at | Window | Samples | Accuracy | Features | Pruning rule | Missing-value strategy | Path resolves here? |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 3 | `lgb-3d-2024-12-31-stored-ti-v2` | READY | 2026-05-23 16:38:49 | 2016-06-01 → 2024-12-31 | 635,185 | 0.566561 | 20 | latest_snapshot_cumulative_80_core20_25 | native_nan | yes |
| 7 | `lgb-7d-2024-12-31-stored-ti-v2` | READY | 2026-05-23 16:39:03 | 2016-06-01 → 2024-12-31 | 635,139 | 0.49292 | 20 | latest_snapshot_cumulative_80_core20_25 | native_nan | yes |
| 30 | `lgb-30d-2024-12-31-stored-ti-v2` | READY | 2026-05-23 16:39:18 | 2016-06-01 → 2024-12-31 | 634,850 | 0.577996 | 20 | latest_snapshot_cumulative_80_core20_25 | native_nan | yes |

Registry totals: **23** `LightGBMModelArtifact` rows, **3** active.


## ModelVersion registry

| ID | Type | Version | Active | Status | Trained at | Accuracy | Artifact path |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 42 | ENSEMBLE | `ensemble-2026-05-23` | ACTIVE | READY | 2026-05-24 05:17:21 | &mdash; | models/ensemble/latest.json |
| 45 | LIGHTGBM | `lgb-30d-2024-12-31-stored-ti-v2` | ACTIVE | READY | 2026-05-23 16:39:18 | 0.577996 | C:\Users\sean_\Documents\FinanceAnalysis\models\lightgbm\30d_lgb-30d-2024-12-31-stored-ti-v2 |
| 44 | LIGHTGBM | `lgb-7d-2024-12-31-stored-ti-v2` | ACTIVE | READY | 2026-05-23 16:39:03 | 0.49292 | C:\Users\sean_\Documents\FinanceAnalysis\models\lightgbm\7d_lgb-7d-2024-12-31-stored-ti-v2 |
| 43 | LIGHTGBM | `lgb-3d-2024-12-31-stored-ti-v2` | ACTIVE | READY | 2026-05-23 16:38:49 | 0.566561 | C:\Users\sean_\Documents\FinanceAnalysis\models\lightgbm\3d_lgb-3d-2024-12-31-stored-ti-v2 |
| 46 | LSTM | `lstm-2024-12-31-stored-ti-v2` | ACTIVE | READY | 2026-05-23 16:41:47 | 0.476722 | C:\Users\sean_\Documents\FinanceAnalysis\models\lstm\lstm-2024-12-31-stored-ti-v2 |
| 33 | ENSEMBLE | `ensemble-2026-05-16` |  | READY | 2026-05-16 04:00:00 | 0.5 | models/ensemble/2026-05-16.bin |
| 30 | ENSEMBLE | `ensemble-2026-05-09` |  | READY | 2026-05-09 09:08:44 | 0.5 | models/ensemble/2026-05-09.bin |
| 27 | ENSEMBLE | `ensemble-2026-05-02` |  | READY | 2026-05-02 04:00:00 | 0.5 | models/ensemble/2026-05-02.bin |
| 18 | ENSEMBLE | `ensemble-2026-04-25` |  | READY | 2026-04-25 04:00:00 | 0.5 | models/ensemble/2026-04-25.bin |
| 14 | ENSEMBLE | `ensemble-2024-12-31` |  | READY | 2026-05-23 16:41:47 | &mdash; | models/ensemble/latest.json |
| 9 | ENSEMBLE | `ensemble-2026-04-18` |  | READY | 2026-04-18 04:00:00 | 0.5 | models/ensemble/2026-04-18.bin |
| 1 | ENSEMBLE | `ensemble-2026-04-15` |  | READY | 2026-04-15 01:45:38 | &mdash; | models/ensemble/latest.json |
| 40 | LIGHTGBM | `lgb-30d-2024-12-31-stored-ti-v1` |  | READY | 2026-05-18 13:59:35 | 0.577996 | /home/chang-liu/Documents/FinanceAnalysis/models/lightgbm/30d_lgb-30d-2024-12-31-stored-ti-v1 |
| 39 | LIGHTGBM | `lgb-7d-2024-12-31-stored-ti-v1` |  | READY | 2026-05-18 13:59:19 | 0.49292 | /home/chang-liu/Documents/FinanceAnalysis/models/lightgbm/7d_lgb-7d-2024-12-31-stored-ti-v1 |
| 38 | LIGHTGBM | `lgb-3d-2024-12-31-stored-ti-v1` |  | READY | 2026-05-18 13:59:02 | 0.566561 | /home/chang-liu/Documents/FinanceAnalysis/models/lightgbm/3d_lgb-3d-2024-12-31-stored-ti-v1 |
| 36 | LIGHTGBM | `lgb-30d-2024-12-31-sec5b-v1` |  | READY | 2026-05-17 05:44:30 | 0.58009 | /home/chang-liu/Documents/FinanceAnalysis/models/lightgbm/30d_lgb-30d-2024-12-31-sec5b-v1 |
| 35 | LIGHTGBM | `lgb-7d-2024-12-31-sec5b-v1` |  | READY | 2026-05-17 05:44:13 | 0.498743 | /home/chang-liu/Documents/FinanceAnalysis/models/lightgbm/7d_lgb-7d-2024-12-31-sec5b-v1 |
| 34 | LIGHTGBM | `lgb-3d-2024-12-31-sec5b-v1` |  | READY | 2026-05-17 05:43:57 | 0.569895 | /home/chang-liu/Documents/FinanceAnalysis/models/lightgbm/3d_lgb-3d-2024-12-31-sec5b-v1 |
| 31 | LIGHTGBM | `lightgbm-2026-05-16` |  | READY | 2026-05-16 04:00:00 | 0.5 | models/lightgbm/2026-05-16.bin |
| 24 | LIGHTGBM | `lgb-30d-2024-12-31-core80-v1` |  | READY | 2026-04-26 09:43:51 | 0.571041 | /app/models/lightgbm/30d_lgb-30d-2024-12-31-core80-v1 |
| 23 | LIGHTGBM | `lgb-7d-2024-12-31-core80-v1` |  | READY | 2026-04-26 09:43:40 | 0.490223 | /app/models/lightgbm/7d_lgb-7d-2024-12-31-core80-v1 |
| 22 | LIGHTGBM | `lgb-3d-2024-12-31-core80-v1` |  | READY | 2026-04-26 09:43:27 | 0.557191 | /app/models/lightgbm/3d_lgb-3d-2024-12-31-core80-v1 |
| 21 | LIGHTGBM | `lgb-30d-2024-12-31-regstrong-v1` |  | READY | 2026-04-26 08:38:57 | 0.57023 | /app/models/lightgbm/30d_lgb-30d-2024-12-31-regstrong-v1 |
| 20 | LIGHTGBM | `lgb-7d-2024-12-31-regstrong-v1` |  | READY | 2026-04-26 08:38:39 | 0.49182 | /app/models/lightgbm/7d_lgb-7d-2024-12-31-regstrong-v1 |
| 19 | LIGHTGBM | `lgb-3d-2024-12-31-regstrong-v1` |  | READY | 2026-04-26 08:38:25 | 0.557241 | /app/models/lightgbm/3d_lgb-3d-2024-12-31-regstrong-v1 |
| 12 | LIGHTGBM | `lgb-30d-2024-12-31` |  | READY | 2026-05-02 00:59:35 | 0.57203 | /app/models/lightgbm/30d_lgb-30d-2024-12-31 |
| 11 | LIGHTGBM | `lgb-7d-2024-12-31` |  | READY | 2026-05-02 00:59:11 | 0.482195 | /app/models/lightgbm/7d_lgb-7d-2024-12-31 |
| 10 | LIGHTGBM | `lgb-3d-2024-12-31` |  | READY | 2026-05-02 00:58:45 | 0.534723 | /app/models/lightgbm/3d_lgb-3d-2024-12-31 |
| 6 | LIGHTGBM | `lgb-30d-2026-04-15` |  | READY | 2026-04-16 08:57:22 | 0.481488 | /app/models/lightgbm/30d_lgb-30d-2026-04-15 |
| 5 | LIGHTGBM | `lgb-7d-2026-04-15` |  | READY | 2026-04-16 08:57:02 | 0.468084 | /app/models/lightgbm/7d_lgb-7d-2026-04-15 |
| 4 | LIGHTGBM | `lgb-3d-2026-04-15` |  | READY | 2026-04-16 08:56:36 | 0.585669 | /app/models/lightgbm/3d_lgb-3d-2026-04-15 |
| 3 | LIGHTGBM | `lgb-7d-2026-04-11` |  | READY | 2026-04-15 14:24:09 | 0.882889 | /app/models/lightgbm/7d_lgb-7d-2026-04-11 |
| 2 | LIGHTGBM | `lgb-3d-2026-04-11` |  | READY | 2026-04-15 14:24:06 | 0.949667 | /app/models/lightgbm/3d_lgb-3d-2026-04-11 |
| 41 | LSTM | `lstm-2024-12-31-stored-ti-v1` |  | READY | 2026-05-18 14:01:40 | 0.472 | /home/chang-liu/Documents/FinanceAnalysis/models/lstm/lstm-2024-12-31-stored-ti-v1 |
| 37 | LSTM | `lstm-2024-12-31-sec5b-v1` |  | READY | 2026-05-17 05:49:25 | 0.480556 | /home/chang-liu/Documents/FinanceAnalysis/models/lstm/lstm-2024-12-31-sec5b-v1 |
| 32 | LSTM | `lstm-2026-05-16` |  | READY | 2026-05-16 04:00:00 | 0.5 | models/lstm/2026-05-16.bin |
| 15 | LSTM | `lstm-2024-12-31` |  | READY | 2026-05-02 01:00:45 | 0.460611 | /app/models/lstm/lstm-2024-12-31 |

## Ensemble and diagnostics

| ID | Date | LightGBM weight | LSTM weight | Heuristic weight | Lookback (d) | basis: LightGBM acc | basis: LSTM acc | basis: heuristic acc |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 4 | 2026-05-23 | 0.3560 | 0.3143 | 0.3297 | 60 | 0.53999 | 0.476722 | 0.5 |
| 2 | 2026-04-15 | 0.5058 | 0.0000 | 0.4942 | 60 | 0.511747 | 0 | 0.5 |
| 1 | 2026-04-11 | 0.6470 | 0.0000 | 0.3530 | 60 | 0.916278 | 0 | 0.5 |
| 3 | 2024-12-31 | 0.3585 | 0.3131 | 0.3284 | 60 | 0.545826 | 0.476722 | 0.5 |

`FeatureImportanceSnapshot`: **720** rows, latest id `1315`.


## On-disk LightGBM artifacts

| Directory | Version | Horizon | Trained at | Window | Features | Pruning rule | Missing-value strategy |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `30d_lgb-30d-2024-12-31` | lgb-30d-2024-12-31 | 30 | 2026-05-02 00:59:35 | 2016-06-01 → 2024-12-31 | 39 | none | ABSENT |
| `30d_lgb-30d-2024-12-31-core80-v1` | lgb-30d-2024-12-31-core80-v1 | 30 | 2026-04-26 09:43:51 | 2016-06-01 → 2024-12-31 | 20 | latest_snapshot_cumulative_80_core20_25 | ABSENT |
| `30d_lgb-30d-2024-12-31-regstrong-v1` | lgb-30d-2024-12-31-regstrong-v1 | 30 | 2026-04-26 08:38:57 | 2016-06-01 → 2024-12-31 | 37 | &mdash; | ABSENT |
| `30d_lgb-30d-2024-12-31-sec5b-v1` | lgb-30d-2024-12-31-sec5b-v1 | 30 | 2026-05-17 05:44:30 | 2016-06-01 → 2024-12-31 | 39 | none | native_nan |
| `30d_lgb-30d-2024-12-31-stored-ti-v1` | lgb-30d-2024-12-31-stored-ti-v1 | 30 | 2026-05-18 13:59:35 | 2016-06-01 → 2024-12-31 | 20 | latest_snapshot_cumulative_80_core20_25 | native_nan |
| `30d_lgb-30d-2024-12-31-stored-ti-v2` | lgb-30d-2024-12-31-stored-ti-v2 | 30 | 2026-05-23 16:39:18 | 2016-06-01 → 2024-12-31 | 20 | latest_snapshot_cumulative_80_core20_25 | native_nan |
| `30d_lgb-30d-2026-04-15` |  | 30 | 2026-04-16 08:57:22 |  →  | 39 | &mdash; | ABSENT |
| `30d_lgb-30d-2026-05-23` | lgb-30d-2026-05-23 | 30 | 2026-05-24 05:17:21 | 2010-01-01 → 2026-05-23 | 39 | none | native_nan |
| `3d_lgb-3d-2024-12-31` | lgb-3d-2024-12-31 | 3 | 2026-05-02 00:58:45 | 2016-06-01 → 2024-12-31 | 39 | none | ABSENT |
| `3d_lgb-3d-2024-12-31-core80-v1` | lgb-3d-2024-12-31-core80-v1 | 3 | 2026-04-26 09:43:27 | 2016-06-01 → 2024-12-31 | 20 | latest_snapshot_cumulative_80_core20_25 | ABSENT |
| `3d_lgb-3d-2024-12-31-regstrong-v1` | lgb-3d-2024-12-31-regstrong-v1 | 3 | 2026-04-26 08:38:25 | 2016-06-01 → 2024-12-31 | 37 | &mdash; | ABSENT |
| `3d_lgb-3d-2024-12-31-sec5b-v1` | lgb-3d-2024-12-31-sec5b-v1 | 3 | 2026-05-17 05:43:56 | 2016-06-01 → 2024-12-31 | 39 | none | native_nan |
| `3d_lgb-3d-2024-12-31-stored-ti-v1` | lgb-3d-2024-12-31-stored-ti-v1 | 3 | 2026-05-18 13:59:02 | 2016-06-01 → 2024-12-31 | 20 | latest_snapshot_cumulative_80_core20_25 | native_nan |
| `3d_lgb-3d-2024-12-31-stored-ti-v2` | lgb-3d-2024-12-31-stored-ti-v2 | 3 | 2026-05-23 16:38:49 | 2016-06-01 → 2024-12-31 | 20 | latest_snapshot_cumulative_80_core20_25 | native_nan |
| `3d_lgb-3d-2026-04-11` |  | 3 | 2026-04-15 14:24:06 |  →  | 39 | &mdash; | ABSENT |
| `3d_lgb-3d-2026-04-15` |  | 3 | 2026-04-16 08:56:36 |  →  | 39 | &mdash; | ABSENT |
| `3d_lgb-3d-2026-05-23` | lgb-3d-2026-05-23 | 3 | 2026-05-24 05:15:24 | 2010-01-01 → 2026-05-23 | 39 | none | native_nan |
| `7d_lgb-7d-2024-12-31` | lgb-7d-2024-12-31 | 7 | 2026-05-02 00:59:11 | 2016-06-01 → 2024-12-31 | 39 | none | ABSENT |
| `7d_lgb-7d-2024-12-31-core80-v1` | lgb-7d-2024-12-31-core80-v1 | 7 | 2026-04-26 09:43:40 | 2016-06-01 → 2024-12-31 | 20 | latest_snapshot_cumulative_80_core20_25 | ABSENT |
| `7d_lgb-7d-2024-12-31-regstrong-v1` | lgb-7d-2024-12-31-regstrong-v1 | 7 | 2026-04-26 08:38:39 | 2016-06-01 → 2024-12-31 | 37 | &mdash; | ABSENT |
| `7d_lgb-7d-2024-12-31-sec5b-v1` | lgb-7d-2024-12-31-sec5b-v1 | 7 | 2026-05-17 05:44:13 | 2016-06-01 → 2024-12-31 | 39 | none | native_nan |
| `7d_lgb-7d-2024-12-31-stored-ti-v1` | lgb-7d-2024-12-31-stored-ti-v1 | 7 | 2026-05-18 13:59:19 | 2016-06-01 → 2024-12-31 | 20 | latest_snapshot_cumulative_80_core20_25 | native_nan |
| `7d_lgb-7d-2024-12-31-stored-ti-v2` | lgb-7d-2024-12-31-stored-ti-v2 | 7 | 2026-05-23 16:39:03 | 2016-06-01 → 2024-12-31 | 20 | latest_snapshot_cumulative_80_core20_25 | native_nan |
| `7d_lgb-7d-2026-04-11` |  | 7 | 2026-04-15 14:24:09 |  →  | 39 | &mdash; | ABSENT |
| `7d_lgb-7d-2026-04-15` |  | 7 | 2026-04-16 08:57:02 |  →  | 39 | &mdash; | ABSENT |
| `7d_lgb-7d-2026-05-23` | lgb-7d-2026-05-23 | 7 | 2026-05-24 05:16:22 | 2010-01-01 → 2026-05-23 | 39 | none | native_nan |

## On-disk LSTM artifacts

| Directory | Version | Window | Aggregate accuracy | Features | Missing-value strategy | Stored path resolves here? |
| --- | --- | --- | --- | --- | --- | --- |
| `lstm-2024-12-31` | lstm-2024-12-31 | 2016-06-01 → 2024-12-31 | 0.460611 | 39 | ABSENT | NO |
| `lstm-2024-12-31-sec5b-v1` | lstm-2024-12-31-sec5b-v1 | 2016-06-01 → 2024-12-31 | 0.480556 | 78 | mask_and_zero_impute | NO |
| `lstm-2024-12-31-stored-ti-v1` | lstm-2024-12-31-stored-ti-v1 | 2016-06-01 → 2024-12-31 | 0.472 | 78 | mask_and_zero_impute | NO |
| `lstm-2024-12-31-stored-ti-v2` | lstm-2024-12-31-stored-ti-v2 | 2016-06-01 → 2024-12-31 | 0.476722 | 78 | mask_and_zero_impute | NO |

> **Portability warning.** Stored `artifact_path` values are absolute and were written on the host that trained them. 4 of 4 do not resolve on this machine. Retraining rewrites them; see `docs/how-to/retrain.md`.

