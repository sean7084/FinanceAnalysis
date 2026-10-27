# Sensitivity grid `7c7bb491bffb`

Panel starts: 2016-01-29. End: 2023-12-31. Blocks: 6. Baseline arm: `rule-rolling`.
Pre-registration: `docs/reports/preregistration/fold-shape-icir-2026-10-27.md` (sha256 7c7bb491bffb).

| arm | panel start | median Sharpe | positive blocks | maxDD median | legs won | passed |
| :--- | :--- | ---: | ---: | ---: | :--- | :--- |
| rule-rolling | 2016-01-29 | 0.0791 | 66.67% | 19.24% | - | no |
| rule-expanding | 2016-01-29 | 0.0791 | 66.67% | 19.24% | lose/win/win | no |
| lgb-select-rolling | 2016-01-29 | -0.0970 | 33.33% | 15.48% | lose/lose/win | no |
| lgb-select-expanding | 2016-01-29 | -0.5497 | 33.33% | 18.56% | lose/lose/win | no |
| lstm-select-rolling | 2016-01-29 | -0.4626 | 50.00% | 15.63% | lose/lose/win | no |
| lstm-select-expanding | 2016-01-29 | -0.4626 | 50.00% | 17.35% | lose/lose/win | no |

## Replication

- `rule-expanding`: passed in **0/1** panel start(s). Not reproduced across contexts.
- `lgb-select-rolling`: passed in **0/1** panel start(s). Not reproduced across contexts.
- `lgb-select-expanding`: passed in **0/1** panel start(s). Not reproduced across contexts.
- `lstm-select-rolling`: passed in **0/1** panel start(s). Not reproduced across contexts.
- `lstm-select-expanding`: passed in **0/1** panel start(s). Not reproduced across contexts.
