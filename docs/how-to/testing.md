# How to run the tests

Two independent suites: Django's built-in runner for the backend, Vitest for the
frontend. Neither is wired into CI — there is no CI configuration in the
repository.

---

## 1. Suite inventory

### Backend — 339 tests across 11 modules

| Module | Tests |
| --- | --- |
| `apps/backtest/tests.py` | 71 |
| `apps/markets/tests.py` | 41 |
| `apps/prediction/tests.py` | 41 |
| `apps/analytics/tests.py` | 39 |
| `apps/users/tests.py` | 28 |
| `apps/prediction/tests_lightgbm.py` | 28 |
| `apps/core/tests.py` | 23 |
| `apps/factors/tests.py` | 20 |
| `apps/macro/tests.py` | 20 |
| `apps/developer/tests.py` | 16 |
| `apps/sentiment/tests.py` | 12 |

`apps/prediction/tests_lstm.py` does not exist. LSTM coverage lives inside
`apps/prediction/tests.py` (sequence building, ensemble refresh, registry
resolution) and `apps/backtest/tests.py` (LSTM as a prediction source).

### Frontend — 31 tests across 4 files

| File | Tests |
| --- | --- |
| `src/pages/BacktestWorkbenchPage.test.tsx` | 22 |
| `src/pages/DashboardPage.test.tsx` | 4 |
| `src/lib/api.test.ts` | 4 |
| `src/pages/IndicatorBoardPage.test.tsx` | 1 |

Run with `npm test`, which invokes `vitest run` (single pass, no watch).
Environment is `jsdom`; the setup file is `frontend/src/test/setup.ts`,
registered by `vite.config.ts`.

---

## 2. Prerequisites

The backend suite needs:

- A reachable PostgreSQL with `CREATE` privilege for the configured role —
  Django builds and tears down `test_finance_analysis`.
- A `.env` present, or the equivalent variables exported.
- No Redis or network access for most tests, but see §6.

The frontend suite needs only `npm install`. It does not touch the backend.

---

## 3. Running backend tests

Always run from the activated `.venv` at the repository root. Docker is not part
of the test path.

```bash
# Everything
python manage.py test

# One app
python manage.py test apps.backtest

# One module
python manage.py test apps.backtest.tests

# One class
python manage.py test apps.backtest.tests.BacktestManagementCommandTests

# One test
python manage.py test apps.backtest.tests.BacktestManagementCommandTests.test_run_core_backtest_matrix_inline_scheduler_round_robins_continuations

# Several specific tests in one invocation
python manage.py test apps.factors.tests apps.prediction.tests_lightgbm
```

### Use `--keepdb`

```bash
python manage.py test --keepdb apps.backtest
```

Without it, Django drops and recreates the test database on every run. Two
consequences matter here:

1. **It is slow.** This schema has many migrations across ten apps, and the
   backtest and analytics tables carry large indexes.
2. **It prompts interactively.** If a stale `test_finance_analysis` exists —
   common after an interrupted run — Django asks
   `Type 'yes' if you would like to try deleting the test database…`. In a
   non-interactive shell that read hits EOF and the run aborts:

   ```
   django.db.backends.base.creation: confirm = input(...)
   EOFError: EOF when reading a line
   ```

   `--keepdb` reuses the existing database and runs pending migrations instead
   of prompting. This is the correct default for repeated local runs and for any
   scripted or agent-driven invocation.

If you genuinely need a clean database, drop it deliberately rather than
answering a prompt:

```bash
psql "$DATABASE_URL" -c 'DROP DATABASE IF EXISTS test_finance_analysis;'
```

### Useful flags

| Flag | Effect |
| --- | --- |
| `--keepdb` | Reuse the test database; migrate instead of recreate |
| `-v 2` | Print each test name as it runs |
| `--failfast` | Stop on the first failure |
| `--parallel N` | Run across `N` processes (each gets its own `test_…_N` clone) |
| `--debug-sql` | Print SQL for failing tests |
| `--tag` / `--exclude-tag` | Filter by `@tag(...)` |

---

## 4. Running frontend tests

```bash
cd frontend
npm test                      # single pass
npm run lint                  # eslint
npm run build                 # tsc -b && vite build
```

To watch during development, call Vitest directly without the `run` argument:

```bash
node ./node_modules/vitest/vitest.mjs
```

The `package.json` scripts invoke binaries through Node rather than through
npm's shim. Keep that form when adding scripts; `npx vitest` reintroduces the
Windows path-resolution problems the current form avoids.

---

## 5. App-label discovery used to fail — and why it no longer does

Historically `python manage.py test apps.sentiment` failed while
`python manage.py test apps.sentiment.tests` worked, with:

```
ImportError: attempted relative import with no known parent package
```

**Root cause.** `apps/__init__.py` did not exist, so `apps` was a PEP 420
implicit namespace package. Django's app-label discovery hands unittest a module
path whose parent package is never resolved, and every `apps/*/tests.py` opens
with relative imports (`from .models import …`), which then fail.

**Fix.** `apps/__init__.py` now exists as a regular-package marker carrying a
docstring that explains why it must not be deleted. Both invocation forms now
behave identically. Do not remove that file to "clean up an empty module".

**The worse half of this bug.** Without that file, bare `python manage.py test`
— the command previously documented as the way to run the suite — discovered
**zero** tests and exited successfully:

```
Ran 0 tests in 0.000s
```

Verified against a pristine checkout of the commit before the fix: `Ran 0 tests`,
exit code 0. With the marker in place the same command discovers all 339. So the
suite was not merely awkward to invoke by app label; the default invocation was a
silent no-op that reported success, and every failure documented in §6 had been
invisible.

**Always read the `Ran N tests` line, never just the exit code.** A green run that
executed nothing is the most dangerous possible outcome, and this repository
produced one by default.

---

## 6. Known failures and how to read them

A full run currently reports **10 failing of 339**, in four clusters — all
pre-existing fixture debt, catalogued with their diagnoses in `BACKLOG.md`.

That number was **109** until recently. The reduction is instructive, because almost
none of it came from fixing product code:

| Stage | Failing | What changed |
| --- | --- | --- |
| As discovered | 109 | Bare `manage.py test` had been finding **0** tests, so none of this was visible |
| After the Redis credential fix | 17 | One environment value; 92 tests recovered |
| After the analytics calendar fixture fix | 10 | One test helper; 7 more recovered |

The lesson: when a suite reports a large failure count, **categorise by exception
type before investigating any individual test.** Here 93 of 109 shared one cause, and
the remaining 16 shared another. Two fixes accounted for 91% of the total.

### 6.1 Case study: the Redis failure (93 tests, resolved)

Every test that made an API request failed, because DRF runs `check_throttles`
during `initial()` and throttle counters live in the Redis cache. The traceback ended:

```
rest_framework/views.py, in check_throttles
  -> redis/client.py, in execute_command
  -> redis.exceptions.AuthenticationError: invalid username-password pair or user is disabled.
```

**Diagnosis:** the password in `.env` had gone stale and the server rejected it.
Tested directly, both with and without a username in the URL:

```python
redis.Redis.from_url(REDIS_URL).ping()                 # AuthenticationError
redis.Redis(host=..., password=<same>, ...).ping()     # AuthenticationError
```

So it is **not** an ACL-username-versus-`requirepass` URL-shape problem; the
credential itself does not match the server. Confirm before changing anything:

```bash
./scripts/verify_local_stack.sh    # pings both Redis databases
```

The blast radius is far wider than the test suite. The same credential backs the
Django cache, `cache_page` responses, and the Channels layer, so while it is wrong:

- every authenticated API request fails at the throttle check;
- server-side response caching does not work;
- the WebSocket alert stream has no functioning channel layer.

Resolved by correcting the credential in `REDIS_URL`, `CELERY_BROKER_URL`, and
`CELERY_RESULT_BACKEND`; 92 tests recovered at once. Probing settled the URL shape
definitively — this server uses a real ACL user, so the empty-username form fails
here. See `local-setup.md` §3 before changing a working `REDIS_URL`.

The general point stands: tests that avoid the API client — management-command,
task-level, and model tests — keep passing while the cache is unreachable, so a
partially green suite can hide a completely broken one.

### 6.2 Non-hermetic sentiment tests

Independently of the credential problem, `apps/sentiment/tests.py` emits real
provider traffic (`Total fetched: 1 items from providers: eastmoney`) rather than
mocking it, so those tests vary run to run. Tracked in `BACKLOG.md`.

### 6.3 Tests needing a gitignored local fixture

`apps/macro/tests.py` exercises `backfill_macro_snapshots`, which also loads the
ChinaBond yield CSV from `source_data/CGBYieldCurve_2010to2016.csv`. `source_data/`
is **gitignored**, so on a fresh clone that test fails with:

```
CommandError: Historical yield CSV not found: .../source_data/CGBYieldCurve_2010to2016.csv
```

and passes only on a machine that happens to have the file. A test whose outcome
depends on an untracked local file is not reproducible; it should build the fixture
in a temp directory, as the CNY/USD case in the same module already does. Tracked in
`BACKLOG.md`.

---

## 7. Coverage

No coverage tooling is configured. There is no `coverage` dependency in
`requirements/`, no `.coveragerc`, and no `--coverage` flag anywhere. `.gitignore`
anticipates `.coverage`, `htmlcov/`, and `.pytest_cache/`, but nothing generates
them.

To measure ad hoc:

```bash
pip install coverage
coverage run --source=apps manage.py test --keepdb
coverage report -m
```

`coverage` is not in `requirements/local.txt`, so this is a throwaway
environment change rather than a supported workflow.

---

## 8. Conventions the suite follows

Worth matching when adding tests:

- **`TestCase`, not `pytest`.** Everything is `django.test.TestCase` with
  `unittest.mock.patch`. There is no pytest configuration and no fixtures
  framework.
- **Class names encode the delivery phase** — `Phase13SentimentTests`,
  `Phase15BacktestTests`, `BacktestManagementCommandTests`. New tests go into
  the class matching their area.
- **Settings assertions read `django.conf.settings`** rather than re-declaring
  expected values, so configuration tests track the real module. See
  `test_celery_queue_split_routes_backtest_and_training_tasks`.
- **Expensive collaborators are patched at the import site**, e.g.
  `@patch('apps.backtest.management.commands.run_core_backtest_matrix.run_backtest')`
  — not at the defining module. Patching the definition site does not affect an
  already-imported reference.
- **Rows are created through the ORM** with explicit `Decimal(...)` values for
  money and probability fields, never floats.
- **Test databases are seeded per test**, not per class, so ordering is not
  significant.

---

## 9. Quick reference

```bash
# Backend, fast iteration
python manage.py test --keepdb -v 2 apps.backtest

# Backend, single test
python manage.py test --keepdb apps.core.tests.<Class>.<test_name>

# Frontend
cd frontend && npm test

# Both, from the repo root
python manage.py test --keepdb; (cd frontend; npm test)
```
