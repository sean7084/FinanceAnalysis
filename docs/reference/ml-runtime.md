# ML runtime pins

The ETF rotation artifacts are only comparable when they were produced by the same
modelling stack. `requirements/base.txt` pins the stack; this sheet records what those pins
resolve to and where the accelerator reality differs from the hope.

## Pinned set (verified 2026-10-27 against the dev venv)

| package | pin |
| :--- | :--- |
| lightgbm | 4.6.0 |
| torch | 2.12.0 |
| scikit-learn | 1.8.0 |
| numpy | 2.4.6 |
| mlflow | 3.12.0 |
| akshare | 1.18.63 |
| tushare | 1.4.29 |
| TA-Lib | 0.6.8 |
| django-celery-beat | 2.9.0 |

The source of truth is `requirements/base.txt`; `PinnedRuntimeTests` refuses a venv whose
installed version of lightgbm / torch / scikit-learn / numpy disagrees with its pin, so a
rebuild cannot silently drift away from the recorded set.

## What the artifacts record

`train_lightgbm.train_walk_forward` and the LSTM artifact metadata both write
`library_versions` (`lightgbm`, `torch`, `numpy`, `scikit-learn`), and every measurement
ledger row carries the same block in its context. A reading whose stack is unknown cannot be
compared with one whose stack is pinned; the field makes that visible instead of implied.

## Accelerators, as measured

- **LightGBM stays on CPU.** The installed wheel has no OpenCL device, so `device=gpu`
  errors; `ETF_LGB_PARAMS` can still select it on a machine that has one. Thread count is
  `ETF_LGB_NUM_THREADS` (default `min(16, cpu_count - 2)`).
- **PyTorch sees no CUDA device in this WSL2 venv** (`torch.cuda.is_available()` is false;
  the wheel is CPU-only). The LSTM trains on CPU here. The earlier "runs on cuda" note
  referred to a different host; record the accelerator next to any timing claim.
