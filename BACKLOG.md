# Backlog

Working notes, open defects, and deferred ideas. This file absorbs content that
used to live in `README.md` as unfiled scratch, plus findings from documentation
and code audits.

It is deliberately **not** release notes — see `CHANGELOG.md` for what shipped.
Items here have no commitment attached. When something is done, move it to
`CHANGELOG.md` and delete it from here.

---

## Resolved

- **App-label test discovery failure — and a silent no-op test run.**
  `python manage.py test apps.sentiment` failed with `ImportError: attempted
  relative import with no known parent package` while the fully qualified
  `apps.sentiment.tests` worked. Root cause: `apps/__init__.py` did not exist,
  making `apps` a PEP 420 namespace package. Fixed by adding the marker file.

  The worse half, found while verifying the fix: bare `python manage.py test` —
  the invocation the old README documented — discovered **zero** tests and exited
  0. Confirmed against a pristine worktree at the pre-fix commit: `Ran 0 tests in
  0.000s`, success. The suite had been reporting green while running nothing, so
  every failure listed below was invisible until now. With the marker in place the
  same command discovers all 339. Documented in `docs/how-to/testing.md` §5.
- **README was globally gitignored.** `.gitignore` line 1 was a bare `README.md`,
  which matches at any depth. `frontend/README.md` was untracked as a result and
  no subdirectory README could be committed. Removed the rule.
- **Windows backtest parallelism.** The old note asked for multiple worker
  processes plus native-thread caps. Delivered by the four-queue Celery split
  (`ops` / `backtest` / `train-lightgbm` / `train-lstm`), per-queue worker node
  naming, and the `OMP_NUM_THREADS` / `MKL_NUM_THREADS` / `OPENBLAS_NUM_THREADS` /
  `NUMEXPR_NUM_THREADS` guidance in `docs/how-to/local-setup.md` §11.
- **Hand-transcribed reference data.** All row counts, coverage ranges, registry
  snapshots, command inventories, Celery routes, and env vars are now generated
  by `export_documentation_facts` into `docs/reference/`.
- **Dead root-level scripts.** `audit_script.py` (broken import — pulled `Asset`
  from `apps.prediction.models` where it does not exist — plus a hardcoded
  retired path), `check_indicators.py` (superseded by the generated metrics
  sheet), `query_check_v2.py` (ad-hoc query profiler with no `django.setup()`),
  and `test_register.json` (sample payload, now an inline example in
  `docs/reference/api.md`) were removed. Recoverable from git history. The empty
  `apps/analysis/` package was removed with them.
- **Scratch notes in README.** Roughly 155 lines of unfiled TODOs, pasted Celery
  logs, first-person fragments, and a stray `>>` were moved here. `README.md` is
  now orientation-only.
- **Redis credential rejected — was blocking 93 tests and all caching.** `.env`
  carried a stale password. Corrected in `REDIS_URL`, `CELERY_BROKER_URL`, and
  `CELERY_RESULT_BACKEND`; all three now authenticate, the Django cache
  round-trips, and the Channels layer completes a group add/send/discard/flush
  cycle. Recovered **92 tests** (109 failing → 17). Probing also settled the URL
  shape: this server uses a real ACL user, so `redis://:<pw>@` and
  `redis://default:<pw>@` both fail while `redis://finance_analysis:<pw>@`
  succeeds. Recorded in `docs/how-to/local-setup.md` §3 so nobody "simplifies" a
  working URL into a broken one.
- **`verify_local_stack.ps1` never actually verified anything.** It shelled out to
  `pg_isready` and `redis-cli`, neither of which ships with Windows, then downgraded
  their absence to a warning and still `exit 0`. On the primary documented platform
  the verifier introspected Django settings and printed URLs back without ever
  opening a connection — which is exactly how the stale Redis password survived.
  Both launchers now probe through `psycopg2` and `redis-py` and fail the exit code on
  any unreachable service.

  *Corrected afterwards.* That commit also claimed both launchers redact credentials
  "from every message including failures". They did not, and the claim was wrong in two
  different ways. `verify_local_stack.sh` redacted its probe messages but its settings
  summary at the end of the file printed `CELERY_BROKER_URL` and the cache `LOCATION`
  raw — `redact()` was defined inside a heredoc and was not in scope twenty lines later.
  `verify_local_stack.ps1` had a second, weaker `redact_url()` in its settings probe,
  with no `try/except` and no malformed-path guard, so it leaked whenever a mistyped URL
  pushed credentials into the path component.

  Three copies of the redaction logic existed and two were defective differently. The
  duplication was the root cause, not an incidental detail — it is how one copy got
  missed while the commit message asserted otherwise. Fixed in `04faf85` by consolidating
  into a single `scripts/_stack_probe.py` that both verifiers call, which also removed
  135 lines of Python embedded in shell strings.

  Found by running the `.sh` on Linux for the first time — it had never been executed on
  any platform, because Windows users run the `.ps1`. The weaker `.ps1` `redact_url()`
  likewise looked correct on Windows, because a well-formed URL puts credentials in
  `urlparse`'s `username`/`password` fields, which it drops; the malformed-path branch
  that it failed to guard only triggers on a mistyped URL. Both defects were invisible
  until something exercised the untested path. **That is the reason this file records it:
  a fix applied to two platform variants must be verified on both, and a claim in a commit
  message is not evidence.**
- **Analytics test fixtures never seeded the trading calendar.**
  `_make_ohlcv_sequence` created OHLCV on consecutive *calendar* days and
  `ExchangeTradingCalendar` appeared nowhere in `apps/analytics/tests.py`. Since the
  freshness guards resolve trading dates from the official calendar, an empty
  calendar made `latest_official_trade_date` return `None`, so no indicator or
  signal row was written and 8 tests failed. The helper now generates business days,
  seeds a matching open calendar row per date, derives the exchange code through the
  production `asset_exchange_code` helper so fixture and guard cannot diverge, and
  returns the dates so callers can bound windows by trading day instead of assuming
  one row per calendar day.
- **RS_SCORE fixtures had the same defect in a second location.** The three
  `test_calculate_rs_scores_for_all_assets_*` tests built OHLCV inline over 21
  **consecutive calendar days** from 2024-02-01 with no calendar rows. That range
  contains only 15 weekdays, so it could never satisfy the exact 20-trading-day
  anchor `RS_SCORE` requires — the window was unsatisfiable regardless of seeding.
  Routed all three through `_make_ohlcv_sequence`, which generates 21 business days
  ending at the patched `as_of` (2024-02-21, so the window starts 2024-01-24) and
  seeds the calendar. `Phase10SignalTests` is now 17/17.
- **Backtest PIT LightGBM candidate test had two stacked defects.** First
  `ValueError: No active LightGBM artifact available for horizon 7` — `_pick_candidates`
  resolves the runtime for provenance even when the prediction call is mocked, so it
  needs both an active artifact row and a stubbed `_load_model_artifacts`. Fixing that
  exposed the second: `mock_predict.call_count` was 0 because the default `auto`
  backend resolves to `cpu_batched`, which routes through
  `_predict_lightgbm_for_assets_batched` and bypasses the mocked per-asset function
  entirely. Pinned `lightgbm_inference_backend: cpu_serial` so the test exercises the
  seam it mocks.

  *Commit provenance.* The fix landed in two pieces: the artifact fixture and stubbed
  `_load_model_artifacts` in `6ec691d`, the backend pin in `ffe2347`. The first half
  was written while PostgreSQL was unreachable and left uncommitted pending
  verification, then committed separately. `ffe2347`'s message nonetheless describes
  **both** halves at length while its diff contains only the pin, so it overclaims and
  reads as though it duplicated `6ec691d`. It did not — the tree is correct and nothing
  is duplicated. Left uncorrected because both commits are published on `origin/main`;
  rewording would rewrite three SHAs and force-push `main`, which is not worth it for a
  prose defect, and would leave the WSL2 clone diverged. The durable fix is the
  "describe the staged diff, not the session" rule in `CONTRIBUTING.md`.
- **`apps.analytics.tests` is fully green (39/39, was 13 failing).** Beyond the two
  above: extracted a reusable `_seed_trading_calendar(asset, dates)` helper for
  fixtures that build OHLCV inline; seeded it in `Phase8IndicatorTests.setUp`, which
  is why `test_calculate_fibonacci_retracement_creates_indicator` got `None` back;
  corrected two RSI date-set assertions that still expected five consecutive
  *calendar* days after the window became five *trading* days spanning a weekend; and
  added `model_version=None` to the LSTM prediction stub, whose signature predated
  the model-version-selection work.
- **Migration drift cleared — `makemigrations --check` exits 0**, so the
  `CONTRIBUTING.md` definition of done is satisfiable at last. Two migrations were
  pending, and neither touches data. `backtest.0003_alter_backtestrun_status` is a
  proven no-op: `sqlmigrate` emits literally `-- (no-op)`, because `default` is a
  Python-level attribute and `0002` had simply omitted it. `markets.0012` is five
  `RenameIndex` operations, which PostgreSQL executes as `ALTER INDEX ... RENAME TO
  ...` — a catalog-only rename with no table rewrite and no meaningful lock. Before
  applying, verified that all five *old* index names existed in the dev database and
  none of the new ones did; that is the precondition for the rename to succeed, and a
  database built fresh under Django 6 would already carry the new names and fail. The
  drift came from auto-generated `models.Index` names (no explicit `name=`) being
  rehashed by a Django upgrade.
- **Compose worker now consumes all four declared queues.** `start-celeryworker` had no
  `-Q`, so Celery fell back to `CELERY_TASK_DEFAULT_QUEUE` (`ops`) and the single
  Compose worker would never run a backtest or a retrain — published to queues nothing
  reads, sitting there silently rather than failing. Now defaults to all four and is
  overridable via `CELERY_WORKER_QUEUES`, matching the native launcher's variable. The
  native script keeps its `ops` default because it is started once per queue group with
  a distinct node name; that model does not apply to a single container. Also switched
  to `exec` so celery becomes PID 1 and receives SIGTERM for a warm shutdown.
  `start-celerybeat` was reviewed and needs no `-Q` — beat publishes, it does not
  consume.
- **`FRONTEND_URL` default corrected to `http://localhost:5173`.** It was `3000`, a
  port nothing in this project listens on, while Vite pins 5173 with `strictPort: true`
  and so can never fall back to it. The setting builds the verification and
  password-reset links in `apps/users/views.py`, so both were dead out of the box.
- **`.env.example` is now complete: 33 of 33 settings-read variables.** Previously 9
  declared keys against ~30 reads, so `HISTORICAL_DATA_FLOOR` — the governance control
  both main docs treat as canonical — was undiscoverable from the example file, along
  with every `MACRO_*` and `NEWS_BACKFILL_*` tuning key and the whole `EMAIL_*` group.
  Each is listed at the default the code actually uses, with the non-obvious ones
  annotated. `WSL2_UBUNTU_ACCOUNT` / `WSL2_UBUNTU_PASSWORD` were removed: no consumer
  anywhere, and a `*_PASSWORD` key in a committed template invites a plaintext secret.
  `docs/reference/env.md` now reports zero missing variables and zero inert keys.
- **`export_documentation_facts` scans Compose entrypoints too.** Its consumer scan only
  looked in `scripts/`, so `CELERY_WORKER_QUEUES` — read by a container on every boot —
  would have been reported as "no consumer found — inert". The generated sheet is only
  trustworthy if its detection covers every place a key can be consumed.

---

## Open — correctness

### Test suite green — 339/339

Resolved. The reduction ran 109 (Redis) → 17 → 10 → 7 → 6 → 3 → **0**. Every step was
verified against the full suite. No production behaviour changed except the one genuine
validator bug below, which was itself surfaced by a failing test that was right.

**Cluster 2 — data-quality continuity (3 tests, `core`). Resolved.** The three shared an
area but not a cause, which is why the earlier "probably the calendar again" note was
wrong and had to be replaced rather than acted on:

- `TechnicalIndicatorValidationRegressionTests.…flags_out_of_range_rsi` exposed a **real
  production bug** — see the next entry. Fixed in the validator; the test was not
  modified.
- `DataQualityValidationCommandTests.test_validate_data_quality_writes_actionable_reports`
  compared `metadata['technical_indicators']` against `REQUIRED_TECHNICAL_INDICATORS`, a
  hand-copied 13-entry fixture constant, while the command defaults to
  `DEFAULT_TECHNICAL_INDICATORS` — 19 entries since `RETURN_3D/5D/10D`,
  `RELATIVE_VOLUME_5D/20D` and `REALIZED_VOLATILITY_5D` were added. Now asserts against
  the imported production constant, so it cannot drift again. This is the
  `RETURN_3D/5D/10D` discrepancy flagged in the original documentation validation.
- `…test_technical_indicator_continuity_warnings_appear_in_summary_and_metadata` listed
  its asset on the window's first date, so RSI(14) had roughly 15 bars of warmup to find
  in a 4-day calendar and every date was legitimately excused. The validator was right;
  the fixture was asking for a finding the rules correctly refuse to produce. Relisted at
  `d1 - 1 day`, which is the branch where the guard *does* expect continuity, because
  warmup cannot be assessed from a calendar that starts after the listing.

Two of the three were fixtures asserting against hand-copied or unstated preconditions
rather than against production constants. That is the same failure mode the generated
reference sheets were built to eliminate, appearing in tests instead of docs.

### Validator skipped value-range checks on dates excused from continuity

**Production bug, fixed in `1d018d3`.** `_write_technical_indicator_continuity_gaps`
gated the bounded-value check behind the continuity expectation:

```python
if not variant_expected_dates:
    continue          # skipped the out-of-range loop further down as well
```

A date is legitimately excused from continuity when warmup is insufficient, the asset is
suspended, or it is newly listed — but an excused date can still carry a stored row, and
a stored `RSI` of 120 or an `RS_SCORE` of 4.0 is a defect wherever it sits. The old
ordering silently suppressed range validation on exactly the partial-window dates where
malformed rows are most likely: warmup edges, post-suspension resumes, backfill
boundaries.

The range check now runs before the continuity guard and does not depend on it.
`variant_rows` is already restricted to OHLCV-backed baseline dates by the caller, so
this validates what exists without asserting anything about what ought to exist.

**Expect new findings on the next production run.** `validate_data_quality` will now
report `value_out_of_range` rows it previously suppressed. They were always present and
invisible. Budget triage time for that run rather than reading the new warnings as a
regression.

Two related notes. The `issue_type` is spelled `technical_indicator_value_out_of_range`
in the counters and `value_out_of_range` in the CSV detail row — confusing but
intentional, and worth unifying if the report schema is ever versioned. And the
`critical_issues=4` seen while instrumenting that fixture were its genuinely absent
related rows (FactorScore, MacroSnapshot, MarketContext, IndexMembership), not a
symptom.

**Instrumentation found this; reading did not.** Dumping every row the validator emitted
for the fixture, and re-running without `--only-report`, showed the target CSV had 0 rows
while 15 were written elsewhere — and `factor_score_gaps` had already confirmed OHLCV
existed for the date. That eliminated the empty-baseline hypothesis in a single run and
pointed straight at the `continue`. Several hundred lines of static reading had narrowed
it to three candidates but not to the cause. The temporary debug block was reverted with
`git checkout` before committing.

Baseline evidence: the original 109 failures were confirmed **pre-existing** by
running the identical selection against a pristine worktree at the pre-change
commit -- same test counts and same failing test names for both `apps.backtest` +
`apps.core` (94 tests) and the remaining nine modules (245 tests). No regression was
introduced by the documentation restructure. Every reduction up to 3 (109 → 17 → 10 →
7 → 6 → 3) came from an environment credential fix or a test-fixture fix, with no
change to production code. The final step (3 → 0) is the exception: it included one
genuine production fix, the validator bug above, found because a failing test turned out
to be correct and the code wrong. That is the outcome the whole exercise was for — a
suite that actually runs is what makes such a bug visible at all.

### Production code inspects whether it is being mocked

`apps/backtest/tasks.py` around line 757 calls `inspect.signature` on
`_predict_lightgbm_for_asset.side_effect` to decide whether to pass `run=`. Production
code branching on the shape of a test double is a compatibility shim for older stub
signatures, and it hides drift instead of surfacing it.

The LSTM path has no such shim, which is why
`test_dashboard_stocks_overlays_runtime_lstm_candidate_payload` failed loudly with
`TypeError: … unexpected keyword argument 'model_version'` -- the better failure mode,
though it surfaces from inside the view and reads like a view bug.

Removing the LightGBM shim means updating every LightGBM stub in
`apps/backtest/tests.py` to accept `run=`. Worth doing; not urgent.

### `apps/macro/tests.py` depends on a gitignored local fixture

`test_backfill_macro_snapshots_uses_yahoo_cny_usd_before_tushare_start` overrides
`MACRO_CNYUSD_CSV_PATH` with a temp file, but `backfill_macro_snapshots` also loads
the ChinaBond yield CSV from `source_data/CGBYieldCurve_2010to2016.csv`. That
directory is **gitignored**, so on a fresh clone the test fails with:

```
CommandError: Historical yield CSV not found: .../source_data/CGBYieldCurve_2010to2016.csv
```

Confirmed: this is the one test whose result differed between the working tree and
a pristine HEAD worktree, and the difference was the missing untracked file, not
code. Fix by building the yield fixture in a temp directory the way the CNY/USD
case in the same module already does.

### `makemigrations --check` — resolved

Both drifts are gone; the check exits 0 and is enforced in CI. See the Resolved entry
for what the generated migrations actually contained. Two corrections to the earlier
diagnosis, recorded so the reasoning is not reused blindly:

- `backtest.0003` was correctly predicted to be harmless, and `sqlmigrate` confirms it
  emits literally `-- (no-op)`.
- `markets.0012` was described as touching "real indexes", which overstated it. Django
  generated `RenameIndex`, and PostgreSQL executes that as `ALTER INDEX ... RENAME TO
  ...` — a catalog-only rename with no rebuild, no table rewrite and no meaningful
  lock. The distinction mattered: it is what made applying this to the dev database
  routine rather than something to schedule.

The `CHANGELOG.md` note that the check was clean at v0.1.9 still stands, and the
likely cause of the index half is a Django upgrade rehashing auto-generated
`models.Index` names.

### Non-hermetic sentiment tests

Independent of the Redis credential problem above, two tests in
`apps.sentiment.tests.Phase13SentimentTests` make real network calls rather than
using test doubles:

- `test_sentiment_latest_endpoint`
- `test_recalculate_endpoint_queues_pipeline`

Observed provider traffic during a test run:
`Total fetched: 1 items from providers: eastmoney`.

They currently fail on Redis first, which masks this defect. Once the credential
is fixed they will still vary run to run with provider availability and quota.
They need a `locmem` cache override and mocked providers.

### Implausible model accuracies in the registry

`ModelVersion` rows for `lgb-3d-2026-04-11` and `lgb-7d-2026-04-11` record
accuracies of **0.949667** and **0.882889**. Directional accuracy for this
problem sits around 0.42–0.58 across every other family. These are almost
certainly look-ahead leakage, not good models.

Worse, `EnsembleWeightSnapshot` id 1 carries a basis `lightgbm_accuracy` of
**0.916278**, so the leaked artifacts propagated into ensemble weights. That
snapshot is inactive now, but the mechanism is not guarded.

- Investigate what the 2026-04-11 feature pipeline did differently.
- Consider retiring those rows to `ARCHIVED` so they cannot be selected.
- Add a sanity bound that refuses to activate an artifact whose accuracy exceeds
  a plausible ceiling, or at least warns loudly.

### Stuck `RUNNING` backtest runs

The coverage sheet shows runs in `RUNNING` whose `created_at` is weeks old. They
will not self-heal. `apps/backtest/task_health.py` can identify them; see
`docs/how-to/runbook-sync-failure.md` §6. Needs a sweep and a decision on whether
to restart or fail them.

### Stored artifact paths are not portable

`ModelVersion.artifact_path` and LSTM `summary.json` record **absolute** paths
from whatever host trained them. The registry currently holds paths from a Docker
container (`/app/...`), a Linux home directory, and a Windows OneDrive checkout —
none of which resolve on the current host. Only the most recent LightGBM family
resolves.

Options: store repo-relative paths and resolve against `BASE_DIR` at load time, or
add a re-registration step for moved clones. A model that loads by version but
cannot find its file fails at **inference** time, not at promotion time, which is
the worst place to discover it.

### `TECHNICAL_GUIDE.md` disagrees with the staleness code

The guide states that `relative_volume_5d` and `relative_volume_20d` inherit the
5-day and 20-day moving-average gap rules (2 and 5 trading days).
`apps/analytics/technical_staleness.py` sets both to **0** in
`BASE_MAX_GAP_TRADING_DAYS`. The code is authoritative; the guide needs
correcting.

### Empty tables behind live features

`analytics_alertrule`, `analytics_alertevent`,
`analytics_screenertemplate`, and `macro_eventimpactstat` all have **0 rows**.

- `check_alert_rules` runs every 5 minutes against no rules, so the WebSocket
  alert stream connects and stays permanently silent.
- The Alert Center page combines a live socket with `/alert-events/` history and
  has nothing to show.
- Screener templates are a documented API surface with no templates.

Either seed reference data, or document these as user-provisioned and empty by
design. Right now the features look broken rather than unconfigured.

---

## Open — frontend

### The frontend does not type-check or lint clean

`npm run build` exits 2 with six TypeScript errors; `npm run lint` exits 1 with one
error and nine warnings. `npm test` passes all 31 — because vitest transpiles without
type-checking, so a green test run says nothing about whether the project compiles.

That is the same blind-spot class as `manage.py test` once discovering zero tests and
exiting 0. It is also why `frontend/dist` cannot currently be rebuilt from a clean
checkout.

Itemised, with the diagnosis that makes each one small:

| Where | Error | Assessment |
| --- | --- | --- |
| `BacktestWorkbenchPage.test.tsx` 350, 351, 353 | TS2783 — `id`, `name`, `status` specified more than once | Fixture object literals with duplicate keys. Last wins, so behaviour is unchanged; delete the shadowed ones |
| `BacktestWorkbenchPage.tsx` 192, 193 | TS2339 — `processed_trading_days`, `total_trading_days` do not exist on `{}` | `BacktestRunDto['report']['progress']` is typed `{}`. The runtime code is already defensive, routing both through `parseFiniteNumber`, so widen the DTO rather than change the call sites |
| `BacktestWorkbenchPage.tsx` 432 | TS2741 — `entry_weekdays` missing but required | **The code is right and the type is wrong.** `BacktestWorkbenchPage.test.tsx:518` asserts `expect(payload?.parameters).not.toHaveProperty('entry_weekdays')`, so omitting it from the create payload is deliberate and tested. Make the field optional in `BacktestCreatePayload` |
| `629:17` | ESLint `react-refresh/only-export-components` | A page module exports a non-component beside its components, which breaks Fast Refresh. Move the shared constant or helper to its own module |
| 9 warnings | `react-hooks/exhaustive-deps` — missing `t` | All the same shape: a translation function used inside an effect but absent from the dependency array |

The `entry_weekdays` row is the one worth reading carefully. It looks like a dropped
user selection — a required parameter silently missing from a create payload is exactly
what a real bug looks like — and the test asserting its absence is the only thing that
distinguishes it. Checking that before "fixing" it avoids breaking intended behaviour.

### CI runs these two gates non-blocking

`.github/workflows/ci.yml` has a `frontend-static` job with `continue-on-error: true`
that runs `npm run build` and `npm run lint`. It is non-blocking because both fail on
the current tree, and a workflow that is red on arrival gets ignored rather than fixed.

Fix the seven errors above, then delete `continue-on-error` and fold the two steps into
the blocking `frontend` job. The job name states the situation so the non-blocking
status is not mistaken for an oversight.

### CI exists, and asserts a discovery floor

`.github/workflows/ci.yml` runs on push to `main` and on pull requests, with three jobs:

- **`backend`** (blocking) — PostgreSQL 15 and Redis 7 service containers, then
  `manage.py check`, `makemigrations --check --dry-run`,
  `export_documentation_facts --check`, and the full suite.
- **`frontend`** (blocking) — `npm ci` and the vitest suite.
- **`frontend-static`** (non-blocking, see above).

Both test jobs assert a **minimum test count** (`MIN_TEST_COUNT`, currently 339 and 31)
as well as a green result. This is the load-bearing part. A workflow that only checked
the exit code would have reported success through the entire period when
`manage.py test` was discovering zero tests, which is how 109 failures stayed hidden.
The floor converts that class of silent success into a hard failure.

Raise the floors when tests are added. Lowering one should require a stated reason, since
the count can only fall if discovery breaks or tests are deleted.

Two notes on the service configuration. The Redis service runs unauthenticated: the
development server requires an ACL user, but that is a property of that server's
configuration, not of the code, so CI does not need to reproduce it. And Python is pinned
to 3.14 to match the verified local interpreter — TA-Lib 0.6.8 ships manylinux wheels
bundling the C library, so no apt build step is needed, but `torch` is unpinned and large
enough to dominate the job runtime.

## Open — configuration and infrastructure

### `BACKTEST_STALE_TASK_MAX_AGE_SECONDS` is not configurable

`apps/backtest/task_health.py` reads it via `getattr(settings, ..., 2400)`, but
the setting is never defined and never read from the environment. Changing the
stale-run threshold currently requires a code edit. Wire it through `env.int()`.

### OpenAPI schema metadata is stale

- `SPECTACULAR_SETTINGS['VERSION'] = '1.2.0'` — a third version scheme alongside
  the project version and the README footer, cross-referenced by nothing.
- The schema description says "Chinese Markets **(CSI 300)**", omitting CSI A500.
- The published rate-limit table omits the `auth` and legacy `user` scopes.

### Unpinned ML dependencies

`lightgbm`, `torch`, `scikit-learn`, `mlflow`, `numpy`, `akshare`, `tushare`,
`TA-Lib`, and `django-celery-beat` are all unpinned in `requirements/base.txt`,
while the guide documents exact hyperparameters and accuracies. A rebuild on a
different day can produce a different model. Pin at least the ML stack, and
record the resolved set used for each artifact family.

Also: `torch` installs as a **CPU-only** build from the default index on Windows,
which conflicts with the `windows_gpu` LightGBM inference backend and the
WSL2-as-NVIDIA-owner framing. Document which runtimes actually have GPU.

### `requirements/production.txt` cannot support the documented deployment

It is a single `-r base.txt` line. No `gunicorn`, `uvicorn`, or `daphne`
anywhere — and the WebSocket surface requires an ASGI server. The README's
production section describes Kubernetes, RDS, CDN, CI/CD, Prometheus, Sentry, and
ELK, none of which exists. Relabelled as a plan; still unbuilt.

### No CI

There is no CI configuration in the repository. `export_documentation_facts --check`
and the test suites are designed to be gate-able but nothing runs them
automatically. A minimal pipeline (lint, backend tests with `--keepdb`, frontend
tests, docs `--check`) would catch most of the drift class documented here.

### No coverage measurement

No `coverage` dependency, no `.coveragerc`. `.gitignore` anticipates `.coverage`
and `htmlcov/` but nothing generates them.

---

## Open — documentation

- Correct the `relative_volume_*` gap values in `TECHNICAL_GUIDE.md` (see above).
- Add docstrings to `apps/backtest/` (19 non-test files, ~4,100 lines, zero
  docstrings — including the 2,061-line engine in `tasks.py` that encodes the fee
  model, TP/SL exit ordering, chunked resume, and candidate generation) and to
  `apps/sentiment/` (13 files, ~1 docstring). Module-level docstrings were added
  in this pass; the deep function-level coverage of `backtest/tasks.py` is still
  outstanding.
- Duplicate `import json` at
  `apps/backtest/management/commands/run_core_backtest_matrix.py` lines 28-29.
- Clarify the relationship between `onboard_csi_a500_universe` and
  `rollout_csi_a500_universe`. Both exist; nothing says when to use which.
- `scripts/run_local_stack.sh`, `scripts/smoke_api_check.sh`, and
  `scripts/run_staged_news_backfill.sh` are referenced by no document.
- `CHANGELOG.md` 0.1.13 is an audit scratch pad, not release notes — mixed
  Chinese/English checklist with unchecked items and no
  `Objective`/`Implemented Features`/`Key Files` structure. Convert before
  tagging.
- `CHANGELOG.md` 0.1.12 states env loading moved to `.envs/.local`; that was
  reversed by the single-`.env` consolidation. Unlike the duplicate-`0.1.7`
  entry, which carries an explicit historical note, this one has no forward
  pointer.
- Decide `LICENSE`. `README.md` declares the project private and proprietary and
  a proprietary notice file now exists, but the choice should be conscious
  rather than incidental.

---

## Deferred ideas

Carried over from the old `README.md` scratch sections. Unprioritised and
unvalidated.

### Data semantics

- **OHLCV adjustment semantics.** `close` is already qfq and `adj_close` holds the
  same value; the unadjusted original close is not retained. Add a `raw_close`
  column so the two stop being "same value, different name".
- **No limit-up / limit-down rules.** Nothing judges 10% / 20% / ST limits against
  the previous close, distinguishes main board / ChiNext / STAR / BSE regimes, or
  flags return-based price anomalies. Limit-up days are not marked.
- **Macro snapshot alignment.** The yield curve uses each month's first-day data
  and CNY/USD uses the monthly open. Switch to the previous month's OHLCV, and
  align snapshots and market context by publication/availability date rather than
  hard-pasting by statistical period.
- **Rename the dollar index field.** `MacroSnapshot.dxy` actually syncs the Dow
  Jones FXCM Dollar Index Basket (`USDOLLAR`), not the ICE DXY. Rename to match
  what is stored.
- **Northbound/southbound flow.** Decide whether `moneyflow_hsgt` 北向资金 /
  南向资金 (百万元) belongs on `MacroSnapshot` — it is a market-level series, not
  a per-stock one, which is why the per-stock northbound fields were removed.

### Model and feature work

- **Provenance on artifacts and backtests.** Record
  `effective_universe_policy` version, `label_definition`, code version / git
  commit, `data_snapshot_version`, and `schema_version` on training artifacts, and
  capture the same during backtests.
- **Feature schema validation.** Persist the feature schema in the artifact and
  validate strictly at inference time, so a schema mismatch fails loudly instead
  of neutral-filling.
- **Overfitting and model quality.** The in-sample / out-of-sample alpha gap is
  the metric to attack.
- **Per-date candidate snapshots.** Persist the full cross-section so unselected
  names also carry rank, pass/fail, and `candidate_selected=False`. Today only
  executed BUY trades are explainable.
- **Prediction audit workflow.** A dedicated job joining stored predictions to
  realised returns by horizon, writing hit rate, layered returns, and calibration
  to a report. Currently only observable indirectly through backtests.
- **Promote macro series into features.** `dxy`, `cny_usd`, `cpi_yoy`, and
  `ppi_yoy` are stored but consumed by nothing.
- **Replace the heuristic in the ensemble.** It is the weakest member and drags
  the blend; a tuned multi-factor linear model would be a better complement to
  LightGBM.
- **Replace LSTM with a Transformer / TFT.** The consensus strongest architecture
  for this problem: handles multiple time scales, digests price + macro +
  sentiment together, and does not forget distant history. Expensive to tune.
- **Add a 14-day horizon.**
- **Stored analytics not feeding `technical_score`.** MACD, ADX, OBV, SMA/EMA, and
  RS score are stored but the `FactorScore` technical component is a
  bottom-fishing reversal scorer that ignores them. Decide whether that is
  intentional (it currently is) and say so where a reader will look.
- **Backfill as a validation-only step.** Consider removing backfill from the
  model rebuild path so it runs only during data validation, making rebuilds
  faster and more predictable.

### Trade execution

- **Entry price depends on the close.** Buys fill at `close + slippage`, which is
  not achievable in practice. Consider intraday minute data to model real entry
  cost.
- **Entry weekday policy.** Currently Tuesday/Thursday by default. Is that
  justified, or an artefact?
- **Position sizing.** Move from binary `suggested` to sized recommendations.
  Half-Kelly as the conservative baseline:
  `position_fraction = (win_rate × odds − loss_rate) / odds`, then halve.
  A tiered mapping on the existing `trade_score` is the cheap first step:
  10–12 → ¥10,000, 12–14 → ¥15,000, 14+ → ¥20,000 per entry.

### Product surface

- **Model version selection.** Let the operator pick a model version so the latest
  LightGBM/LSTM can be validated against previous ones: add selection to the
  backtest page, sync it to the dashboard, expose it on `BacktestRun` in admin
  defaulting to the latest, update exported reports, and wire it through the
  backend.
- **Trade-decision integration and dashboard consolidation.** Invest in the next
  layer rather than adding parallel pages.
- **Sector rotation signals.** Industry and theme rotation context to improve
  selection directionality.
- **Real NLP sentiment.** Replace the rule-based, neutral-heavy fallback with a
  finance-oriented Chinese BERT model.
- **Policy text analysis.** Parse CSRC / NDRC documents for sector-level
  directional impact.
- **Personal holdings tracking.** Let a user enter cost basis and position size
  for portfolio-specific suggestions.

### Quality targets

The bar the model work is aiming at, against the 3.2 baseline:

| Metric | Baseline | Target |
| --- | --- | --- |
| Out-of-sample return | +10.66% | > 20% (approach or beat benchmark) |
| Out-of-sample Sharpe | 0.81 | > 1.0 |
| Out-of-sample win rate | 50.56% | > 55% |
| In/out-of-sample alpha gap | ~44% | < 20% |

### Notes with no owner yet

- A backtest once ran 2061.9s and returned
  `'Backtest failed for run_id=578: SoftTimeLimitExceeded()'` while Celery logged
  it as `succeeded`. The soft limit is now 1800s for `run_backtest`, and
  `docs/how-to/runbook-sync-failure.md` §4 explains why Celery reports success on
  a failed run. Whether 1800s is the right ceiling for long windows is still open.
- Real-time strictness was scoped to backtests only. Prediction APIs, alerts, and
  the rest of the project were deliberately not switched.
- An unexplained arithmetic note survived from the old scratch section:
  `$23.7 / 0.2257 ≈ 105`. Kept here only because deleting it loses the hint that
  something was being sized.
