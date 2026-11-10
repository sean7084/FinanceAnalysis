# Sentiment data corrections, preserved after the code that made them

_Extracted 2026-11-10 from `apps/sentiment/migrations/0003_*`/`_tz_shift_logic.py` and
`0004_*`/`_upsert_key_logic.py`, ahead of the module decommissioning (#52). Both repairs
were applied to the production database. When the app is deleted, these files are the only
place left that explains why the stored history looks the way it does — which matters most
for the rows that were *not* touched, because "no code references this" is not the same as
"this data is wrong"._

## 1. `NewsArticle.published_at` held Beijing wall clock stored as UTC

`apps/sentiment/providers.py` parsed the wall-clock strings the Chinese providers publish
(Eastmoney, Sina, Tonghuashun and TuShare all publish Beijing time) into a naive datetime and
localised it with `make_aware()`, which follows `settings.TIME_ZONE`. While that setting was
`'UTC'`, an article published at 09:30 Beijing was stored as `09:30+00:00` — an instant eight
hours ahead of the truth.

The timezone unification flipped `TIME_ZONE` to `'Asia/Shanghai'` on 2026-11-02, which makes
`make_aware()` correct for everything ingested afterwards. Migration `0003` corrected the rows
stored before the flip, so the two eras agree.

**The affected rows were identified by a predicate, not a timestamp cutoff.** A legacy
mis-stamped row satisfies exactly one property: `published_at` equals the provider's own
wall-clock string read as UTC. That raw string survives in `metadata['raw']`. Three other
kinds of row exist and each excludes itself:

- A row written after the flip stores the same string read as Asia/Shanghai, whose offset is
  non-zero, so it can never equal the UTC reading.
- A row that fell through to the `timezone.now()` fallback carries a true instant and has no
  comparable raw string.
- A cutoff on `created_at` would have been strictly worse: it would "fix" the fallback rows and
  miss rows an old-code worker inserts after `migrate` runs.

**No `SentimentScore` recompute was needed, and that was checked rather than assumed.** The
daily bucket was the UTC date of the mis-stamped value, which is the article's Beijing *wall*
date `W`. The corrected value is `W` interpreted as Asia/Shanghai, whose Beijing date is still
`W`. The day bucket is invariant under this shift, so all three `score_type` values and
`ConceptHeat` kept pointing at the right day.

Residual: articles ingested through the `timezone.now()` fallback were bucketed by UTC date,
which can differ from their Beijing date. They cannot be identified after the fact, and the
daily score they feed is a QA surface rather than a model feature.

The migration was reversible and idempotent: forward marked each row it touched in `metadata`,
reverse keyed off that mark, and a shifted row no longer matches the forward predicate so it can
never move twice.

**The live guard survives the deletion.** `TIME_ZONE` and the session zone must agree;
`apps/core/checks.py` (`core.E001`) fails loudly at startup and in CI if they diverge, and
`apps/core/date_floor.session_key_timestamp` takes its zone explicitly instead of following the
setting. That check exists because a setting flip silently doubled `TechnicalIndicator`'s key
space — the same class of bug as this one, and it is not a sentiment concern.

## 2. The `SentimentScore` upsert key was never actually unique (#49)

`unique_together` on `(article, asset, date, score_type)` cannot constrain a row whose
`article` or `asset` is NULL, because Postgres treats NULLs as distinct — and the two score
types written by the million are exactly the NULL-keyed ones.

This was found in production data, not in a report: five sessions of the 2026-11-09 recompute
walk (2026-05-19, 2026-10-08, 2026-10-13, 2026-10-20, 2026-10-21) died with
`MultipleObjectsReturned`. Behind them stood **2,047 duplicate upsert-key groups**, every one
with NULL in `article` or `asset`. No group disagreed on the score, which is what made
collapsing them a cleanup rather than a data decision.

Repairs, in order: #104 declared the constraint `NULLS NOT DISTINCT` and batched the writer;
#105 made the collapse actually delete rows.

Two traps are worth keeping, because they generalise past this table:

- **The first version of the collapse deleted nothing.** It filtered
  `WHERE (score_type, article_id, asset_id, date) IN (SELECT ...)`, and SQL `IN` uses plain
  equality, so NULL never equals NULL — the same NULL-distinctness that created the duplicates
  repeated itself inside the repair. `PARTITION BY` groups NULLs together, so ranking alone
  identifies the groups and no `IN` filter is needed.
- **`GROUP BY` and `IN` disagree about NULL.** A helper that counted the production table while
  being pointed at a test table looked like it passed. The table argument was made required, not
  defaulted, for exactly that reason.

The collapse kept the newest row per key (ties broken on `id`, so retries keep the same rows)
and refused to run if any group held differing `sentiment_score` values — choosing between two
published values is a data decision. After the five sessions were re-run, duplicate key groups
stood at 0.

## 3. What the deletion removes, and what cannot be rebuilt

Deleted by the decommissioning migration, after a verified cold backup:

| Table | Rows | Volume |
| :--- | ---: | ---: |
| `sentiment_sentimentscore` | 6,708,181 | 2,692 MB |
| `sentiment_newsarticle` | 176,098 | 356 MB |
| `sentiment_conceptheat` | 654 | 240 kB |

Plus `FactorScore.sentiment_score` and `FactorScore.sentiment_weight` — the first non-null on
all 2,043,000 rows with exactly one distinct value, and the second a constant 0.0.

Cold backup: `finance_analysis_pre_decommission_2026-11-10.dump` (181,636,503 bytes,
md5 `50958f8059964821fb0a1663d03485d3`), restored into a scratch database and compared before
anything was dropped: all four row counts and the `sentiment_score` non-null count matched the
pre-deletion baseline. Two `pg_restore` statements failed and were deliberately tolerated — both
foreign keys pointing at `markets_asset`, a table the `-t`-filtered dump does not contain. Data
recoverability was never in question; the FK definitions are re-derivable from the models.

**Why this corpus cannot be regenerated.** The `major_news` entitlement was not purchased, and
the free per-stock source (`ak.stock_news_em`) accepts no date range — one call returns the 10
most-relevant items and nothing older. History is not obtainable at any price we intend to pay.

**What that costs.** The 2026-11-09 retrain on the repaired features reported:

```
3d:  test_accuracy 0.4782, train_accuracy 0.5547, rank_ic 0.0080, icir 0.0465, rank_ic_tstat 0.7351
30d: test_accuracy 0.3896, train_accuracy 0.5780, rank_ic 0.0495, icir 0.2265, rank_ic_tstat 3.5813
```

Those rows stay in the registry, annotated as not reproducible. 30d was the only horizon whose
holdout `rank_ic` cleared significance, and the sentiment-free retrain cannot be compared against
it in either direction — the feature set differs and the inputs no longer exist. That is a
documented consequence of this decision, not a regression to be explained away.

Also worth recording: the article corpus was never as deep as the backfill floor suggested.
`NEWS_BACKFILL_FLOOR` was 2021-04-15, but all 176,098 surviving rows start on **2025-06-09**,
which is why the 2026-11-09 recompute walked 2025-06-09..2026-11-07 and left the ~5.8M pre-era
`ASSET_7D` rows at a legitimate 0 rather than treating them as still broken.
