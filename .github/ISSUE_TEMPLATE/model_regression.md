---
name: Model Regression
about: Report LightGBM or LSTM model quality degradation or training failures
title: "[MODEL] Brief description"
labels: ["bug", "triage"]
assignees: []
---

## Model Type
- [ ] LightGBM
- [ ] LSTM

## Horizon
- [ ] 3-day
- [ ] 7-day
- [ ] 30-day

## Symptom
- [ ] Metric regression (accuracy/IC/Sharpe dropped after retrain)
- [ ] Training failure (task error or OOM)
- [ ] Prediction output anomaly (all NaN, constant values, extreme distribution)
- [ ] Feature importance shift
- [ ] Calibration / probability output miscalibrated

## Metrics Comparison
| Metric | Previous | Current | Delta |
|--------|----------|---------|-------|
| [e.g., IC] | ... | ... | ... |
| [e.g., Directional Accuracy] | ... | ... | ... |

## Training Configuration
- Universe: [e.g., CSI 300, A500]
- Feature set version: [e.g., v2.1]
- Training window: [e.g., 2020-01-01 to 2024-12-31]
- Celery queue: [train-lightgbm / train-lstm]

## Error Details (if training failed)
Paste the Celery task traceback or log excerpt:

```
[paste error here]
```

## Model Artifacts
- Path: `models/lightgbm/...` or `models/lstm/...`
- Retrained on: [date]
- Trained by commit: [commit hash]

## Steps to Reproduce
1. Run command: `python manage.py ...` or trigger Celery task ...
2. Observe ...

## Additional Context
Any recent changes to features, universe selection, label definitions, or
hyperparameters that might explain the regression.
