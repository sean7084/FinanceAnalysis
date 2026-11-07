<!--
  GENERATED FILE - DO NOT EDIT BY HAND.
  Regenerate: python manage.py export_documentation_facts
  Source of truth: the prediction registry tables plus models/*/metadata.json on disk.
-->

# Model Registry and Artifacts

_Generated: 2026-11-06T12:43:13Z_

Read this sheet before trusting any accuracy or feature count quoted in prose. `LightGBMModelArtifact` is the authority for per-horizon LightGBM deployment; `ModelVersion` is a higher-level registry that does **not** keep one simultaneously active row per horizon. The rationale for that split, and the version-tag naming convention, are explained in `TECHNICAL_GUIDE.md`; the promote/rollback procedure is in `docs/how-to/retrain.md`.

## Active LightGBM artifacts

| Horizon | Version | Status | Trained at | Window | Samples | Accuracy | Features | Pruning rule | Missing-value strategy | Present in store? |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 3 | `lgb-3d-2026-11-05` | READY | 2026-11-06 11:06:40 | 2016-06-01 → 2026-11-05 | 1,247,276 | 0.549247 | 39 | none | native_nan | yes |
| 7 | `lgb-7d-2026-11-05` | READY | 2026-11-06 11:13:25 | 2016-06-01 → 2026-11-05 | 1,246,255 | 0.487352 | 39 | none | native_nan | yes |
| 30 | `lgb-30d-2026-11-05` | READY | 2026-11-06 11:14:05 | 2016-06-01 → 2026-11-05 | 1,237,658 | 0.57442 | 39 | none | native_nan | yes |

Registry totals: **35** `LightGBMModelArtifact` rows, **3** active.


## ModelVersion registry

| ID | Type | Version | Active | Status | Trained at | Accuracy | Artifact path |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 113 | ENSEMBLE | `ensemble-2026-11-05` | ACTIVE | READY | 2026-11-06 11:14:06 | &mdash; | models/ensemble/latest.json |
| 110 | ETF_ENSEMBLE | `etf-ensemble-2026-11-06-adj-fix` | ACTIVE | READY | 2026-11-06 03:54:37 | &mdash; | &mdash; |
| 111 | ETF_LIGHTGBM | `etf-lgb-20d-lambdarank-2026-10-08` | ACTIVE | READY | 2026-11-06 09:43:09 | &mdash; | models/etf_rotation/lightgbm/etf-lgb-20d-lambdarank-2026-10-08 |
| 109 | ETF_LSTM | `etf-lstm-2026-09-29-adj-fix` | ACTIVE | READY | 2026-11-06 03:54:37 | &mdash; | models/etf_rotation/lstm/etf-lstm-2026-09-29-adj-fix |
| 115 | LIGHTGBM | `lgb-30d-2026-11-05` | ACTIVE | READY | 2026-11-06 11:14:05 | 0.57442 | models/lightgbm/30d_lgb-30d-2026-11-05 |
| 114 | LIGHTGBM | `lgb-7d-2026-11-05` | ACTIVE | READY | 2026-11-06 11:13:25 | 0.487352 | models/lightgbm/7d_lgb-7d-2026-11-05 |
| 112 | LIGHTGBM | `lgb-3d-2026-11-05` | ACTIVE | READY | 2026-11-06 11:06:40 | 0.549247 | models/lightgbm/3d_lgb-3d-2026-11-05 |
| 107 | LSTM | `lstm-2026-11-03-adj-fix` | ACTIVE | READY | 2026-11-05 17:50:18 | 0.452167 | models/lstm/lstm-2026-11-03-adj-fix |
| 106 | ENSEMBLE | `ensemble-2026-11-03` |  | READY | 2026-11-05 17:50:19 | &mdash; | models/ensemble/latest.json |
| 58 | ENSEMBLE | `ensemble-2020-01-01` |  | ARCHIVED | 2026-09-25 12:17:32 | &mdash; | models/ensemble/latest.json |
| 53 | ENSEMBLE | `ensemble-2026-09-21` |  | ARCHIVED | 2026-09-22 06:30:02 | &mdash; | models/ensemble/latest.json |
| 42 | ENSEMBLE | `ensemble-2026-05-23` |  | ARCHIVED | 2026-05-24 05:17:21 | &mdash; | models/ensemble/latest.json |
| 33 | ENSEMBLE | `ensemble-2026-05-16` |  | ARCHIVED | 2026-05-16 04:00:00 | 0.5 | models/ensemble/2026-05-16.bin |
| 30 | ENSEMBLE | `ensemble-2026-05-09` |  | ARCHIVED | 2026-05-09 09:08:44 | 0.5 | models/ensemble/2026-05-09.bin |
| 27 | ENSEMBLE | `ensemble-2026-05-02` |  | ARCHIVED | 2026-05-02 04:00:00 | 0.5 | models/ensemble/2026-05-02.bin |
| 18 | ENSEMBLE | `ensemble-2026-04-25` |  | ARCHIVED | 2026-04-25 04:00:00 | 0.5 | models/ensemble/2026-04-25.bin |
| 14 | ENSEMBLE | `ensemble-2024-12-31` |  | ARCHIVED | 2026-05-23 16:41:47 | &mdash; | models/ensemble/latest.json |
| 9 | ENSEMBLE | `ensemble-2026-04-18` |  | ARCHIVED | 2026-04-18 04:00:00 | 0.5 | models/ensemble/2026-04-18.bin |
| 1 | ENSEMBLE | `ensemble-2026-04-15` |  | ARCHIVED | 2026-04-15 01:45:38 | &mdash; | models/ensemble/latest.json |
| 65 | ETF_ENSEMBLE | `etf-ensemble-2026-10-20-oos23` |  | ARCHIVED | 2026-10-20 18:35:45 | &mdash; | &mdash; |
| 62 | ETF_ENSEMBLE | `etf-ensemble-2026-10-20` |  | ARCHIVED | 2026-10-20 18:22:16 | &mdash; | &mdash; |
| 108 | ETF_LIGHTGBM | `etf-lgb-20d-lambdarank-2026-09-29-adj-fix` |  | READY | 2026-11-06 03:51:41 | &mdash; | models/etf_rotation/lightgbm/etf-lgb-20d-lambdarank-2026-09-29-adj-fix |
| 101 | ETF_LIGHTGBM | `etf-lgb-20d-lambdarank-2026-09-17-post-release` |  | ARCHIVED | 2026-10-25 06:58:26 | &mdash; | models/etf_rotation/lightgbm/etf-lgb-20d-lambdarank-2026-09-17-post-release |
| 63 | ETF_LIGHTGBM | `etf-lgb-20d-lambdarank-2023-11-30-oos23` |  | ARCHIVED | 2026-10-20 18:33:58 | &mdash; | models/etf_rotation/lightgbm/etf-lgb-20d-lambdarank-2023-11-30-oos23 |
| 60 | ETF_LIGHTGBM | `etf-lgb-20d-lambdarank-2026-09-14` |  | ARCHIVED | 2026-10-20 18:07:01 | &mdash; | models/etf_rotation/lightgbm/etf-lgb-20d-lambdarank-2026-09-14 |
| 102 | ETF_LSTM | `etf-lstm-2026-09-17-post-release` |  | ARCHIVED | 2026-10-25 07:03:14 | &mdash; | models/etf_rotation/lstm/etf-lstm-2026-09-17-post-release |
| 100 | ETF_LSTM | `etf-lstm-2023-12-29-oos-lstm-v2` |  | ARCHIVED | 2026-10-22 20:48:05 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-oos-lstm-v2 |
| 99 | ETF_LSTM | `etf-lstm-2023-12-29-t-4da7ba13` |  | ARCHIVED | 2026-10-22 20:40:56 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-4da7ba13 |
| 98 | ETF_LSTM | `etf-lstm-2023-12-29-t-3764f738` |  | ARCHIVED | 2026-10-22 20:39:21 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-3764f738 |
| 97 | ETF_LSTM | `etf-lstm-2023-12-29-t-894273e9` |  | ARCHIVED | 2026-10-22 20:37:26 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-894273e9 |
| 96 | ETF_LSTM | `etf-lstm-2023-12-29-t-d0086700` |  | ARCHIVED | 2026-10-22 20:36:01 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-d0086700 |
| 95 | ETF_LSTM | `etf-lstm-2023-12-29-t-3ddb961e` |  | ARCHIVED | 2026-10-22 20:34:35 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-3ddb961e |
| 94 | ETF_LSTM | `etf-lstm-2023-12-29-t-3b39a23e` |  | ARCHIVED | 2026-10-22 20:32:58 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-3b39a23e |
| 93 | ETF_LSTM | `etf-lstm-2023-12-29-t-43a584d6` |  | ARCHIVED | 2026-10-22 20:31:22 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-43a584d6 |
| 92 | ETF_LSTM | `etf-lstm-2023-12-29-t-2fd85c66` |  | ARCHIVED | 2026-10-22 20:29:55 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-2fd85c66 |
| 91 | ETF_LSTM | `etf-lstm-2023-12-29-t-cb1ae1d7` |  | ARCHIVED | 2026-10-22 20:28:29 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-cb1ae1d7 |
| 90 | ETF_LSTM | `etf-lstm-2023-12-29-t-ea816a41` |  | ARCHIVED | 2026-10-22 20:26:58 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-ea816a41 |
| 89 | ETF_LSTM | `etf-lstm-2023-12-29-t-9ac4dfba` |  | ARCHIVED | 2026-10-22 20:25:27 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-9ac4dfba |
| 88 | ETF_LSTM | `etf-lstm-2023-12-29-t-15bc3f78` |  | ARCHIVED | 2026-10-22 20:24:06 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-15bc3f78 |
| 87 | ETF_LSTM | `etf-lstm-2023-12-29-t-48a3a44f` |  | ARCHIVED | 2026-10-22 20:22:46 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-48a3a44f |
| 86 | ETF_LSTM | `etf-lstm-2023-12-29-t-d4c0f43f` |  | ARCHIVED | 2026-10-22 20:21:15 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-d4c0f43f |
| 85 | ETF_LSTM | `etf-lstm-2023-12-29-t-526e79aa` |  | ARCHIVED | 2026-10-22 20:19:32 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-526e79aa |
| 84 | ETF_LSTM | `etf-lstm-2023-12-29-t-465b242a` |  | ARCHIVED | 2026-10-22 20:18:11 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-465b242a |
| 83 | ETF_LSTM | `etf-lstm-2023-12-29-t-9a8eee8c` |  | ARCHIVED | 2026-10-22 20:16:50 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-9a8eee8c |
| 82 | ETF_LSTM | `etf-lstm-2023-12-29-t-9d712399` |  | ARCHIVED | 2026-10-22 20:15:15 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-9d712399 |
| 81 | ETF_LSTM | `etf-lstm-2023-12-29-t-c6514865` |  | ARCHIVED | 2026-10-22 20:13:44 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-c6514865 |
| 80 | ETF_LSTM | `etf-lstm-2023-12-29-t-6ef67633` |  | ARCHIVED | 2026-10-22 20:12:17 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-6ef67633 |
| 79 | ETF_LSTM | `etf-lstm-2023-12-29-t-9fa1140b` |  | ARCHIVED | 2026-10-22 20:10:51 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-9fa1140b |
| 78 | ETF_LSTM | `etf-lstm-2023-12-29-t-ac65802d` |  | ARCHIVED | 2026-10-22 20:09:16 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-ac65802d |
| 77 | ETF_LSTM | `etf-lstm-2023-12-29-t-3ca96e66` |  | ARCHIVED | 2026-10-22 20:07:39 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-3ca96e66 |
| 76 | ETF_LSTM | `etf-lstm-2023-12-29-t-84dc6607` |  | ARCHIVED | 2026-10-22 20:06:13 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-84dc6607 |
| 75 | ETF_LSTM | `etf-lstm-2023-12-29-t-d9d06d3d` |  | ARCHIVED | 2026-10-22 20:04:48 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-d9d06d3d |
| 74 | ETF_LSTM | `etf-lstm-2023-12-29-t-27c2548a` |  | ARCHIVED | 2026-10-22 20:03:13 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-27c2548a |
| 73 | ETF_LSTM | `etf-lstm-2023-12-29-t-ca3d925e` |  | ARCHIVED | 2026-10-22 20:01:28 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-ca3d925e |
| 72 | ETF_LSTM | `etf-lstm-2023-12-29-t-95ab0141` |  | ARCHIVED | 2026-10-22 20:00:02 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-95ab0141 |
| 71 | ETF_LSTM | `etf-lstm-2023-12-29-t-3cd95031` |  | ARCHIVED | 2026-10-22 19:58:39 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-3cd95031 |
| 70 | ETF_LSTM | `etf-lstm-2023-12-29-t-1becb8db` |  | ARCHIVED | 2026-10-22 19:56:52 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-1becb8db |
| 69 | ETF_LSTM | `etf-lstm-2023-12-29-t-625c11fd` |  | ARCHIVED | 2026-10-22 19:55:09 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-625c11fd |
| 68 | ETF_LSTM | `etf-lstm-2023-12-29-t-cf1276f1` |  | ARCHIVED | 2026-10-22 19:53:46 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-cf1276f1 |
| 67 | ETF_LSTM | `etf-lstm-2023-12-29-t-50cbd9f4` |  | ARCHIVED | 2026-10-22 19:50:27 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-50cbd9f4 |
| 66 | ETF_LSTM | `etf-lstm-2023-12-29-t-fd4deb21` |  | ARCHIVED | 2026-10-22 19:49:39 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-12-29-t-fd4deb21 |
| 64 | ETF_LSTM | `etf-lstm-2023-11-30-oos23` |  | ARCHIVED | 2026-10-20 18:35:40 | &mdash; | models/etf_rotation/lstm/etf-lstm-2023-11-30-oos23 |
| 61 | ETF_LSTM | `etf-lstm-2026-09-14` |  | ARCHIVED | 2026-10-20 18:13:05 | &mdash; | models/etf_rotation/lstm/etf-lstm-2026-09-14 |
| 105 | LIGHTGBM | `lgb-30d-2026-11-03-adj-fix` |  | READY | 2026-11-05 17:43:37 | 0.59844 | models/lightgbm/30d_lgb-30d-2026-11-03-adj-fix |
| 104 | LIGHTGBM | `lgb-7d-2026-11-03-adj-fix` |  | READY | 2026-11-05 17:42:50 | 0.483502 | models/lightgbm/7d_lgb-7d-2026-11-03-adj-fix |
| 103 | LIGHTGBM | `lgb-3d-2026-11-03-adj-fix` |  | READY | 2026-11-05 17:42:00 | 0.522605 | models/lightgbm/3d_lgb-3d-2026-11-03-adj-fix |
| 57 | LIGHTGBM | `lgb-30d-2020-01-01` |  | ARCHIVED | 2026-09-25 12:16:10 | 0.630868 | models/lightgbm/30d_lgb-30d-2020-01-01 |
| 56 | LIGHTGBM | `lgb-7d-2020-01-01` |  | ARCHIVED | 2026-09-25 12:15:43 | 0.501229 | models/lightgbm/7d_lgb-7d-2020-01-01 |
| 55 | LIGHTGBM | `lgb-3d-2020-01-01` |  | ARCHIVED | 2026-09-25 12:15:20 | 0.521064 | models/lightgbm/3d_lgb-3d-2020-01-01 |
| 52 | LIGHTGBM | `lgb-30d-2026-09-21` |  | ARCHIVED | 2026-09-22 06:28:49 | 0.563817 | models/lightgbm/30d_lgb-30d-2026-09-21 |
| 51 | LIGHTGBM | `lgb-7d-2026-09-21` |  | ARCHIVED | 2026-09-22 06:28:21 | 0.480272 | models/lightgbm/7d_lgb-7d-2026-09-21 |
| 50 | LIGHTGBM | `lgb-3d-2026-09-21` |  | ARCHIVED | 2026-09-22 06:27:51 | 0.54648 | models/lightgbm/3d_lgb-3d-2026-09-21 |
| 45 | LIGHTGBM | `lgb-30d-2024-12-31-stored-ti-v2` |  | ARCHIVED | 2026-05-23 16:39:18 | 0.577996 | C:\Users\sean_\Documents\FinanceAnalysis\models\lightgbm\30d_lgb-30d-2024-12-31-stored-ti-v2 |
| 44 | LIGHTGBM | `lgb-7d-2024-12-31-stored-ti-v2` |  | ARCHIVED | 2026-05-23 16:39:03 | 0.49292 | C:\Users\sean_\Documents\FinanceAnalysis\models\lightgbm\7d_lgb-7d-2024-12-31-stored-ti-v2 |
| 43 | LIGHTGBM | `lgb-3d-2024-12-31-stored-ti-v2` |  | ARCHIVED | 2026-05-23 16:38:49 | 0.566561 | C:\Users\sean_\Documents\FinanceAnalysis\models\lightgbm\3d_lgb-3d-2024-12-31-stored-ti-v2 |
| 40 | LIGHTGBM | `lgb-30d-2024-12-31-stored-ti-v1` |  | ARCHIVED | 2026-05-18 13:59:35 | 0.577996 | /home/chang-liu/Documents/FinanceAnalysis/models/lightgbm/30d_lgb-30d-2024-12-31-stored-ti-v1 |
| 39 | LIGHTGBM | `lgb-7d-2024-12-31-stored-ti-v1` |  | ARCHIVED | 2026-05-18 13:59:19 | 0.49292 | /home/chang-liu/Documents/FinanceAnalysis/models/lightgbm/7d_lgb-7d-2024-12-31-stored-ti-v1 |
| 38 | LIGHTGBM | `lgb-3d-2024-12-31-stored-ti-v1` |  | ARCHIVED | 2026-05-18 13:59:02 | 0.566561 | /home/chang-liu/Documents/FinanceAnalysis/models/lightgbm/3d_lgb-3d-2024-12-31-stored-ti-v1 |
| 36 | LIGHTGBM | `lgb-30d-2024-12-31-sec5b-v1` |  | ARCHIVED | 2026-05-17 05:44:30 | 0.58009 | /home/chang-liu/Documents/FinanceAnalysis/models/lightgbm/30d_lgb-30d-2024-12-31-sec5b-v1 |
| 35 | LIGHTGBM | `lgb-7d-2024-12-31-sec5b-v1` |  | ARCHIVED | 2026-05-17 05:44:13 | 0.498743 | /home/chang-liu/Documents/FinanceAnalysis/models/lightgbm/7d_lgb-7d-2024-12-31-sec5b-v1 |
| 34 | LIGHTGBM | `lgb-3d-2024-12-31-sec5b-v1` |  | ARCHIVED | 2026-05-17 05:43:57 | 0.569895 | /home/chang-liu/Documents/FinanceAnalysis/models/lightgbm/3d_lgb-3d-2024-12-31-sec5b-v1 |
| 31 | LIGHTGBM | `lightgbm-2026-05-16` |  | ARCHIVED | 2026-05-16 04:00:00 | 0.5 | models/lightgbm/2026-05-16.bin |
| 24 | LIGHTGBM | `lgb-30d-2024-12-31-core80-v1` |  | ARCHIVED | 2026-04-26 09:43:51 | 0.571041 | /app/models/lightgbm/30d_lgb-30d-2024-12-31-core80-v1 |
| 23 | LIGHTGBM | `lgb-7d-2024-12-31-core80-v1` |  | ARCHIVED | 2026-04-26 09:43:40 | 0.490223 | /app/models/lightgbm/7d_lgb-7d-2024-12-31-core80-v1 |
| 22 | LIGHTGBM | `lgb-3d-2024-12-31-core80-v1` |  | ARCHIVED | 2026-04-26 09:43:27 | 0.557191 | /app/models/lightgbm/3d_lgb-3d-2024-12-31-core80-v1 |
| 21 | LIGHTGBM | `lgb-30d-2024-12-31-regstrong-v1` |  | ARCHIVED | 2026-04-26 08:38:57 | 0.57023 | /app/models/lightgbm/30d_lgb-30d-2024-12-31-regstrong-v1 |
| 20 | LIGHTGBM | `lgb-7d-2024-12-31-regstrong-v1` |  | ARCHIVED | 2026-04-26 08:38:39 | 0.49182 | /app/models/lightgbm/7d_lgb-7d-2024-12-31-regstrong-v1 |
| 19 | LIGHTGBM | `lgb-3d-2024-12-31-regstrong-v1` |  | ARCHIVED | 2026-04-26 08:38:25 | 0.557241 | /app/models/lightgbm/3d_lgb-3d-2024-12-31-regstrong-v1 |
| 12 | LIGHTGBM | `lgb-30d-2024-12-31` |  | ARCHIVED | 2026-05-02 00:59:35 | 0.57203 | /app/models/lightgbm/30d_lgb-30d-2024-12-31 |
| 11 | LIGHTGBM | `lgb-7d-2024-12-31` |  | ARCHIVED | 2026-05-02 00:59:11 | 0.482195 | /app/models/lightgbm/7d_lgb-7d-2024-12-31 |
| 10 | LIGHTGBM | `lgb-3d-2024-12-31` |  | ARCHIVED | 2026-05-02 00:58:45 | 0.534723 | /app/models/lightgbm/3d_lgb-3d-2024-12-31 |
| 6 | LIGHTGBM | `lgb-30d-2026-04-15` |  | ARCHIVED | 2026-04-16 08:57:22 | 0.481488 | /app/models/lightgbm/30d_lgb-30d-2026-04-15 |
| 5 | LIGHTGBM | `lgb-7d-2026-04-15` |  | ARCHIVED | 2026-04-16 08:57:02 | 0.468084 | /app/models/lightgbm/7d_lgb-7d-2026-04-15 |
| 4 | LIGHTGBM | `lgb-3d-2026-04-15` |  | ARCHIVED | 2026-04-16 08:56:36 | 0.585669 | /app/models/lightgbm/3d_lgb-3d-2026-04-15 |
| 3 | LIGHTGBM | `lgb-7d-2026-04-11` |  | ARCHIVED | 2026-04-15 14:24:09 | 0.882889 | /app/models/lightgbm/7d_lgb-7d-2026-04-11 |
| 2 | LIGHTGBM | `lgb-3d-2026-04-11` |  | ARCHIVED | 2026-04-15 14:24:06 | 0.949667 | /app/models/lightgbm/3d_lgb-3d-2026-04-11 |
| 59 | LSTM | `lstm-2020-01-01` |  | ARCHIVED | 2026-09-25 12:17:32 | 0.447333 | models/lstm/lstm-2020-01-01 |
| 54 | LSTM | `lstm-2026-09-21` |  | ARCHIVED | 2026-09-22 06:30:02 | 0.476111 | models/lstm/lstm-2026-09-21 |
| 46 | LSTM | `lstm-2024-12-31-stored-ti-v2` |  | ARCHIVED | 2026-05-23 16:41:47 | 0.476722 | C:\Users\sean_\Documents\FinanceAnalysis\models\lstm\lstm-2024-12-31-stored-ti-v2 |
| 41 | LSTM | `lstm-2024-12-31-stored-ti-v1` |  | ARCHIVED | 2026-05-18 14:01:40 | 0.472 | /home/chang-liu/Documents/FinanceAnalysis/models/lstm/lstm-2024-12-31-stored-ti-v1 |
| 37 | LSTM | `lstm-2024-12-31-sec5b-v1` |  | ARCHIVED | 2026-05-17 05:49:25 | 0.480556 | /home/chang-liu/Documents/FinanceAnalysis/models/lstm/lstm-2024-12-31-sec5b-v1 |
| 32 | LSTM | `lstm-2026-05-16` |  | ARCHIVED | 2026-05-16 04:00:00 | 0.5 | models/lstm/2026-05-16.bin |
| 15 | LSTM | `lstm-2024-12-31` |  | ARCHIVED | 2026-05-02 01:00:45 | 0.460611 | /app/models/lstm/lstm-2024-12-31 |

## Ensemble and diagnostics

| ID | Date | LightGBM weight | LSTM weight | Heuristic weight | Lookback (d) | basis: LightGBM acc | basis: LSTM acc | basis: heuristic acc |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 8 | 2026-11-05 | 0.3580 | 0.3049 | 0.3371 | 60 | 0.530886 | 0.452167 | 0.5 |
| 7 | 2026-11-03 | 0.3597 | 0.3041 | 0.3362 | 60 | 0.534849 | 0.452167 | 0.5 |
| 5 | 2026-09-21 | 0.3520 | 0.3161 | 0.3319 | 60 | 0.53019 | 0.476111 | 0.5 |
| 4 | 2026-05-23 | 0.3560 | 0.3143 | 0.3297 | 60 | 0.53999 | 0.476722 | 0.5 |
| 2 | 2026-04-15 | 0.5058 | 0.0000 | 0.4942 | 60 | 0.511747 | 0 | 0.5 |

`FeatureImportanceSnapshot`: **1,188** rows, latest id `1900`.


## On-disk LightGBM artifacts

| Directory | Version | Horizon | Trained at | Window | Features | Pruning rule | Missing-value strategy |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `30d_lgb-30d-2020-01-01` | lgb-30d-2020-01-01 | 30 | 2026-09-25 12:16:09 | 2010-01-01 → 2020-01-01 | 39 | none | native_nan |
| `30d_lgb-30d-2024-12-31` | lgb-30d-2024-12-31 | 30 | 2026-05-02 00:59:35 | 2016-06-01 → 2024-12-31 | 39 | none | ABSENT |
| `30d_lgb-30d-2024-12-31-core80-v1` | lgb-30d-2024-12-31-core80-v1 | 30 | 2026-04-26 09:43:51 | 2016-06-01 → 2024-12-31 | 20 | latest_snapshot_cumulative_80_core20_25 | ABSENT |
| `30d_lgb-30d-2024-12-31-regstrong-v1` | lgb-30d-2024-12-31-regstrong-v1 | 30 | 2026-04-26 08:38:57 | 2016-06-01 → 2024-12-31 | 37 | &mdash; | ABSENT |
| `30d_lgb-30d-2024-12-31-sec5b-v1` | lgb-30d-2024-12-31-sec5b-v1 | 30 | 2026-05-17 05:44:30 | 2016-06-01 → 2024-12-31 | 39 | none | native_nan |
| `30d_lgb-30d-2024-12-31-stored-ti-v1` | lgb-30d-2024-12-31-stored-ti-v1 | 30 | 2026-05-18 13:59:35 | 2016-06-01 → 2024-12-31 | 20 | latest_snapshot_cumulative_80_core20_25 | native_nan |
| `30d_lgb-30d-2024-12-31-stored-ti-v2` | lgb-30d-2024-12-31-stored-ti-v2 | 30 | 2026-05-23 16:39:18 | 2016-06-01 → 2024-12-31 | 20 | latest_snapshot_cumulative_80_core20_25 | native_nan |
| `30d_lgb-30d-2026-04-15` |  | 30 | 2026-04-16 08:57:22 |  →  | 39 | &mdash; | ABSENT |
| `30d_lgb-30d-2026-05-23` | lgb-30d-2026-05-23 | 30 | 2026-05-24 05:17:21 | 2010-01-01 → 2026-05-23 | 39 | none | native_nan |
| `30d_lgb-30d-2026-09-21` | lgb-30d-2026-09-21 | 30 | 2026-09-22 06:28:49 | 2016-06-01 → 2026-09-21 | 39 | none | native_nan |
| `30d_lgb-30d-2026-11-03-adj-fix` | lgb-30d-2026-11-03-adj-fix | 30 | 2026-11-05 17:43:36 | 2010-01-01 → 2026-11-03 | 39 | none | native_nan |
| `30d_lgb-30d-2026-11-05` | lgb-30d-2026-11-05 | 30 | 2026-11-06 11:14:05 | 2016-06-01 → 2026-11-05 | 39 | none | native_nan |
| `3d_lgb-3d-2020-01-01` | lgb-3d-2020-01-01 | 3 | 2026-09-25 12:15:18 | 2010-01-01 → 2020-01-01 | 39 | none | native_nan |
| `3d_lgb-3d-2024-12-31` | lgb-3d-2024-12-31 | 3 | 2026-05-02 00:58:45 | 2016-06-01 → 2024-12-31 | 39 | none | ABSENT |
| `3d_lgb-3d-2024-12-31-core80-v1` | lgb-3d-2024-12-31-core80-v1 | 3 | 2026-04-26 09:43:27 | 2016-06-01 → 2024-12-31 | 20 | latest_snapshot_cumulative_80_core20_25 | ABSENT |
| `3d_lgb-3d-2024-12-31-regstrong-v1` | lgb-3d-2024-12-31-regstrong-v1 | 3 | 2026-04-26 08:38:25 | 2016-06-01 → 2024-12-31 | 37 | &mdash; | ABSENT |
| `3d_lgb-3d-2024-12-31-sec5b-v1` | lgb-3d-2024-12-31-sec5b-v1 | 3 | 2026-05-17 05:43:56 | 2016-06-01 → 2024-12-31 | 39 | none | native_nan |
| `3d_lgb-3d-2024-12-31-stored-ti-v1` | lgb-3d-2024-12-31-stored-ti-v1 | 3 | 2026-05-18 13:59:02 | 2016-06-01 → 2024-12-31 | 20 | latest_snapshot_cumulative_80_core20_25 | native_nan |
| `3d_lgb-3d-2024-12-31-stored-ti-v2` | lgb-3d-2024-12-31-stored-ti-v2 | 3 | 2026-05-23 16:38:49 | 2016-06-01 → 2024-12-31 | 20 | latest_snapshot_cumulative_80_core20_25 | native_nan |
| `3d_lgb-3d-2026-04-11` |  | 3 | 2026-04-15 14:24:06 |  →  | 39 | &mdash; | ABSENT |
| `3d_lgb-3d-2026-04-15` |  | 3 | 2026-04-16 08:56:36 |  →  | 39 | &mdash; | ABSENT |
| `3d_lgb-3d-2026-05-23` | lgb-3d-2026-05-23 | 3 | 2026-05-24 05:15:24 | 2010-01-01 → 2026-05-23 | 39 | none | native_nan |
| `3d_lgb-3d-2026-09-21` | lgb-3d-2026-09-21 | 3 | 2026-09-22 06:27:50 | 2016-06-01 → 2026-09-21 | 39 | none | native_nan |
| `3d_lgb-3d-2026-11-03-adj-fix` | lgb-3d-2026-11-03-adj-fix | 3 | 2026-11-05 17:41:58 | 2010-01-01 → 2026-11-03 | 39 | none | native_nan |
| `3d_lgb-3d-2026-11-05` | lgb-3d-2026-11-05 | 3 | 2026-11-06 11:06:39 | 2016-06-01 → 2026-11-05 | 39 | none | native_nan |
| `7d_lgb-7d-2020-01-01` | lgb-7d-2020-01-01 | 7 | 2026-09-25 12:15:43 | 2010-01-01 → 2020-01-01 | 39 | none | native_nan |
| `7d_lgb-7d-2024-12-31` | lgb-7d-2024-12-31 | 7 | 2026-05-02 00:59:11 | 2016-06-01 → 2024-12-31 | 39 | none | ABSENT |
| `7d_lgb-7d-2024-12-31-core80-v1` | lgb-7d-2024-12-31-core80-v1 | 7 | 2026-04-26 09:43:40 | 2016-06-01 → 2024-12-31 | 20 | latest_snapshot_cumulative_80_core20_25 | ABSENT |
| `7d_lgb-7d-2024-12-31-regstrong-v1` | lgb-7d-2024-12-31-regstrong-v1 | 7 | 2026-04-26 08:38:39 | 2016-06-01 → 2024-12-31 | 37 | &mdash; | ABSENT |
| `7d_lgb-7d-2024-12-31-sec5b-v1` | lgb-7d-2024-12-31-sec5b-v1 | 7 | 2026-05-17 05:44:13 | 2016-06-01 → 2024-12-31 | 39 | none | native_nan |
| `7d_lgb-7d-2024-12-31-stored-ti-v1` | lgb-7d-2024-12-31-stored-ti-v1 | 7 | 2026-05-18 13:59:19 | 2016-06-01 → 2024-12-31 | 20 | latest_snapshot_cumulative_80_core20_25 | native_nan |
| `7d_lgb-7d-2024-12-31-stored-ti-v2` | lgb-7d-2024-12-31-stored-ti-v2 | 7 | 2026-05-23 16:39:03 | 2016-06-01 → 2024-12-31 | 20 | latest_snapshot_cumulative_80_core20_25 | native_nan |
| `7d_lgb-7d-2026-04-11` |  | 7 | 2026-04-15 14:24:09 |  →  | 39 | &mdash; | ABSENT |
| `7d_lgb-7d-2026-04-15` |  | 7 | 2026-04-16 08:57:02 |  →  | 39 | &mdash; | ABSENT |
| `7d_lgb-7d-2026-05-23` | lgb-7d-2026-05-23 | 7 | 2026-05-24 05:16:22 | 2010-01-01 → 2026-05-23 | 39 | none | native_nan |
| `7d_lgb-7d-2026-09-21` | lgb-7d-2026-09-21 | 7 | 2026-09-22 06:28:21 | 2016-06-01 → 2026-09-21 | 39 | none | native_nan |
| `7d_lgb-7d-2026-11-03-adj-fix` | lgb-7d-2026-11-03-adj-fix | 7 | 2026-11-05 17:42:49 | 2010-01-01 → 2026-11-03 | 39 | none | native_nan |
| `7d_lgb-7d-2026-11-05` | lgb-7d-2026-11-05 | 7 | 2026-11-06 11:13:24 | 2016-06-01 → 2026-11-05 | 39 | none | native_nan |

## On-disk LSTM artifacts

| Directory | Version | Window | Aggregate accuracy | Features | Missing-value strategy | Present in store? |
| --- | --- | --- | --- | --- | --- | --- |
| `lstm-2020-01-01` | lstm-2020-01-01 | 2010-01-01 → 2020-01-01 | 0.447333 | 78 | mask_and_zero_impute | yes |
| `lstm-2024-12-31` | lstm-2024-12-31 | 2016-06-01 → 2024-12-31 | 0.460611 | 39 | ABSENT | NO |
| `lstm-2024-12-31-sec5b-v1` | lstm-2024-12-31-sec5b-v1 | 2016-06-01 → 2024-12-31 | 0.480556 | 78 | mask_and_zero_impute | NO |
| `lstm-2024-12-31-stored-ti-v1` | lstm-2024-12-31-stored-ti-v1 | 2016-06-01 → 2024-12-31 | 0.472 | 78 | mask_and_zero_impute | NO |
| `lstm-2024-12-31-stored-ti-v2` | lstm-2024-12-31-stored-ti-v2 | 2016-06-01 → 2024-12-31 | 0.476722 | 78 | mask_and_zero_impute | NO |
| `lstm-2026-09-21` | lstm-2026-09-21 | 2016-06-01 → 2026-09-21 | 0.476111 | 78 | mask_and_zero_impute | yes |
| `lstm-2026-11-03-adj-fix` | lstm-2026-11-03-adj-fix | 2010-01-01 → 2026-11-03 | 0.452167 | 78 | mask_and_zero_impute | yes |

> **Portability warning.** Stored `artifact_path` values are absolute and were written on the host that trained them. 4 of 7 do not resolve on this machine. Retraining rewrites them; see `docs/how-to/retrain.md`.

