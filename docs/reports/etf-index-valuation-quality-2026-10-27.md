# IndexValuationDaily quality (#66)

Panel: 2015-01-05..2026-10-23. Staleness reference: 2026-10-27. Rows: 17168.

## Four rules

| rule | reading | verdict |
| :--- | :--- | :--- |
| PE null rate | 0/17168 (0.00%) | pass |
| PB null rate | 0/17168 (0.00%) | pass |
| date gaps | 2 index(es) with gaps | fail |
| index coverage | 6/299 (2.01%) | fail |
| staleness | latest 2026-10-23, 4 day(s) | pass |

## Issues

- **warning** `etf_index_valuation_date_gaps`: 1 expected trading date(s) missing (2026-03-16..2026-03-16).
- **warning** `etf_index_valuation_date_gaps`: 33 expected trading date(s) missing (2023-10-31..2026-05-20).
- **critical** `etf_index_valuation_index_coverage`: 6/299 mapped indices have valuation rows (2.01%) < 70%.

## Spot check

The reference column is a published plausibility range, not a second feed: TuShare publishes no second valuation quote on this entitlement.

| index | date | PE TTM | PB | turnover | plausible | reference |
| :--- | :--- | ---: | ---: | ---: | :--- | :--- |
| 000001.SH | 2026-10-12 | 15.4913 | 1.4194 | 0.9407 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 000001.SH | 2026-10-13 | 15.5060 | 1.4209 | 0.9858 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 000001.SH | 2026-10-14 | 15.4032 | 1.4116 | 0.9106 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 000001.SH | 2026-10-15 | 15.1962 | 1.3927 | 1.0410 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 000001.SH | 2026-10-16 | 15.2234 | 1.3966 | 0.9634 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 000001.SH | 2026-10-19 | 15.2919 | 1.4033 | 0.8466 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 000001.SH | 2026-10-20 | 15.2878 | 1.4039 | 0.8296 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 000001.SH | 2026-10-21 | 15.1836 | 1.3955 | 0.7688 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 000001.SH | 2026-10-22 | 14.9829 | 1.3778 | 0.8871 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 000001.SH | 2026-10-23 | 15.0801 | 1.3867 | 1.1201 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 000016.SH | 2026-10-12 | 10.8754 | 1.2426 | 0.2717 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 000016.SH | 2026-10-13 | 10.8890 | 1.2442 | 0.2818 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 000016.SH | 2026-10-14 | 10.8308 | 1.2376 | 0.2555 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 000016.SH | 2026-10-15 | 10.8308 | 1.2376 | 0.3423 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 000016.SH | 2026-10-16 | 10.8291 | 1.2379 | 0.3101 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 000016.SH | 2026-10-19 | 10.9061 | 1.2472 | 0.2431 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 000016.SH | 2026-10-20 | 10.8857 | 1.2450 | 0.2969 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 000016.SH | 2026-10-21 | 10.7739 | 1.2375 | 0.2441 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 000016.SH | 2026-10-22 | 10.7664 | 1.2366 | 0.3220 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 000016.SH | 2026-10-23 | 10.7669 | 1.2369 | 0.4140 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 399001.SZ | 2026-10-12 | 27.7340 | 2.5238 | 1.4232 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 399001.SZ | 2026-10-13 | 27.6440 | 2.5159 | 1.4435 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 399001.SZ | 2026-10-14 | 27.1784 | 2.4745 | 1.2717 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 399001.SZ | 2026-10-15 | 26.5456 | 2.4177 | 1.5119 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 399001.SZ | 2026-10-16 | 26.5248 | 2.4167 | 1.4244 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 399001.SZ | 2026-10-19 | 26.4449 | 2.4188 | 1.2453 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 399001.SZ | 2026-10-20 | 26.3767 | 2.4152 | 1.2613 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 399001.SZ | 2026-10-21 | 26.0682 | 2.3900 | 1.2467 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 399001.SZ | 2026-10-22 | 25.3989 | 2.3244 | 1.3804 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |
| 399001.SZ | 2026-10-23 | 26.1194 | 2.3841 | 1.9007 | yes | tushare.index_dailybasic; public range pe 0-200, pb 0-50 |

## Why the family was terminated

Index coverage against the mapped universe is 6/299 (2.01%); the feature therefore cannot rank most of the pool, and a cross-sectional model would treat "no valuation" as a systematic characteristic of the uncovered names. That is a coverage failure, not a tuning problem.
