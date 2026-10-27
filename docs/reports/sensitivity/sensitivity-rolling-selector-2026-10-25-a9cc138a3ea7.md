# Sensitivity grid `a9cc138a3ea7`

Panel starts: 2016-01-29, 2017-01-03, 2018-01-02. End: 2023-12-31. Blocks: 6. Baseline arm: `rule-rolling`.
Pre-registration: `docs/reports/preregistration/sensitivity-rolling-selector-2026-10-25.md` (sha256 a9cc138a3ea7).

| arm | panel start | median Sharpe | positive blocks | maxDD median | legs won | passed |
| :--- | :--- | ---: | ---: | ---: | :--- | :--- |
| rule-rolling | 2016-01-29 | 0.0791 | 66.67% | 19.24% | - | no |
| lgb-select-rolling | 2016-01-29 | 0.4945 | 66.67% | 14.24% | win/win/win | yes |
| rule-rolling | 2017-01-03 | 0.4460 | 83.33% | 13.26% | - | no |
| lgb-select-rolling | 2017-01-03 | 0.0301 | 66.67% | 14.27% | lose/lose/win | no |
| rule-rolling | 2018-01-02 | -0.5284 | 16.67% | 11.58% | - | no |
| lgb-select-rolling | 2018-01-02 | 0.2736 | 66.67% | 16.58% | win/win/lose | no |

## Replication

- `lgb-select-rolling`: passed in **1/3** panel start(s). Not reproduced across contexts.
