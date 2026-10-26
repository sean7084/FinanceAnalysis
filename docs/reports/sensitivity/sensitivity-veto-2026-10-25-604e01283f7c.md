# Sensitivity grid `604e01283f7c`

Panel starts: 2016-01-29, 2017-01-03, 2018-01-02. End: 2023-12-31. Blocks: 6. Baseline arm: `rule`.
Pre-registration: `docs/reports/preregistration/sensitivity-veto-2026-10-25.md` (sha256 604e01283f7c).

| arm | panel start | median Sharpe | positive blocks | maxDD median | legs won | passed |
| :--- | :--- | ---: | ---: | ---: | :--- | :--- |
| rule | 2016-01-29 | 0.1881 | 83.33% | 17.97% | - | no |
| lgb-veto | 2016-01-29 | 0.5400 | 83.33% | 16.78% | win/win/win | yes |
| lstm-veto | 2016-01-29 | 0.0841 | 66.67% | 19.09% | lose/lose/win | no |
| rule | 2017-01-03 | 0.1061 | 66.67% | 13.73% | - | no |
| lgb-veto | 2017-01-03 | 0.4573 | 66.67% | 12.63% | win/win/win | yes |
| lstm-veto | 2017-01-03 | 0.2576 | 83.33% | 13.71% | win/win/win | yes |
| rule | 2018-01-02 | 0.2306 | 66.67% | 19.24% | - | no |
| lgb-veto | 2018-01-02 | -0.4900 | 50.00% | 16.77% | lose/lose/win | no |
| lstm-veto | 2018-01-02 | 0.0191 | 66.67% | 21.15% | lose/win/win | no |

## Replication

- `lgb-veto`: passed in **2/3** panel start(s). Not reproduced across contexts.
- `lstm-veto`: passed in **1/3** panel start(s). Not reproduced across contexts.
