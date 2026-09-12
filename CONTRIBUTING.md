# Contributing

Conventions actually in use in this repository. They are written down because they
were previously only inferable from history.

This is a private, single-maintainer project. "Contribution" mostly means future
you, or an agent working on your behalf, being able to read the repository six
months from now.

---

## 1. Branching and commits

Work happens on `main`. There is no protected-branch workflow, no PR template, and
no CI. That means **nothing checks your work except you** — the discipline below
is what substitutes for a review gate.

### Conventional Commits

```
<type>(<scope>): <imperative summary>

<why, then what — the body explains the motivation and the mechanism>

- <file or area>: <what changed>
- <file or area>: <what changed>
```

Types in use: `feat`, `fix`, `perf`, `refactor`, `chore`, `docs`, `test`.

Scopes are app or area names: `backtest`, `celery`, `lightgbm`, `lstm`, `auth`,
`pagination`, `scripts`, `docker`, `env`, `readme`.

Examples from history:

```
perf(backtest): group inline matrix runs by horizon and reset process caches
feat(celery): split backtest and model training onto dedicated queues
refactor(env): consolidate environment variable configuration to a single .env file
fix(auth): implement scoped throttling for JWT authentication endpoints
```

Rules that matter:

- **Imperative mood** — "add", not "added" or "adds".
- **Summary under ~72 characters.** History contains a commit whose subject is
  five conventional commits concatenated onto one line. Do not do that; make five
  commits.
- **Body explains why.** A summary says what changed; the body says what was wrong
  before and what mechanism fixes it. Future readers need the second part.

### Commit by category

One logical change per commit. When a working session touches several independent
concerns, split them:

| Instead of | Do |
| --- | --- |
| One commit with a launcher change, a settings change, and an engine change | Three commits, each self-consistent |
| A test file bundled with an unrelated feature | Stage the test hunk that belongs to each feature |

Splitting a single file across commits is legitimate. Stage the relevant hunks,
commit, then restore and stage the rest. Verify the final file is byte-identical to
your working state before finishing.

### Describe the staged diff, not the session

Before writing a message, run `git diff --cached --stat` and read what is actually
there. Do not write the message from memory of what you worked on.

A long session accumulates edits, and some of them may already be committed — by an
earlier pass, by a collaborator, or by you committing a half-finished fix before an
outage interrupted verification. Describing work that landed in a previous commit
produces a message that overclaims and misattributes it in the permanent record, and
`git blame` inherits the error.

This has already happened here: `ffe2347`'s message describes a two-part fix while its
diff contains only the second part, because the first part had been committed as
`6ec691d`. It was left uncorrected rather than rebased, since both were already
published. See the cluster-3 note in `BACKLOG.md`.

The same check catches the inverse error — committing less than the message claims,
which leaves a build or test suite broken at that commit. Verify the commit stands on
its own: if the message says tests pass, the staged tree should pass them.

Never commit:

- `.env` (gitignored — contains `TUSHARE_TOKEN` and database credentials)
- `reports/` (gitignored — generated local output, including checkpoint state)
- `get-pip.py` or any other downloaded bootstrap installer
- Scratch notes, pasted log output, or TODO lists — those belong in `BACKLOG.md`

---

## 2. Documentation ownership

Documentation is split by whether a human or a machine owns the content. **This is
the most important convention in the repository** — violating it is what caused
every stale fact found in past audits.

### Generated — never hand-edit

| File | Source of truth |
| --- | --- |
| `docs/reference/metrics.md` | Database row counts and coverage ranges |
| `docs/reference/models.md` | Prediction registry tables plus `models/*/metadata.json` |
| `docs/reference/commands.md` | `get_commands()` and each command's argument parser |
| `docs/reference/celery.md` | The Celery app registry, routes, and beat schedule |
| `docs/reference/env.md` | Every `env(...)` read in `config/settings/*.py` |

Regenerate after any change that touches them:

```bash
python manage.py export_documentation_facts
git diff docs/reference/
```

**Commit the regenerated output with the change that caused it.** The diff is the
clearest available summary of what your change did to the system.

Verify nothing drifted:

```bash
python manage.py export_documentation_facts --check
```

`--check` exits non-zero on drift. Generated output is deterministic apart from a
timestamp line, which `--check` ignores.

If you find yourself typing a row count, a coverage date, an accuracy, a feature
count, or an artifact version into a hand-written document — **stop**. It belongs
in a generated sheet, and it will be wrong within weeks otherwise.

### Authored — hand-written and reviewed

| File | Owns |
| --- | --- |
| `README.md` | Orientation only: what it is, architecture sketch, quickstart, links. Target under 150 lines |
| `TECHNICAL_GUIDE.md` | Explanation only: contracts, formulas, semantics, rationale. **No counts, dates, or version strings** |
| `docs/reference/api.md` | API contracts the OpenAPI schema cannot express |
| `docs/how-to/*.md` | Procedures, in task order, with the reasoning that makes the order matter |
| `CHANGELOG.md` | What shipped, per release |
| `BACKLOG.md` | What is unfinished, broken, or deferred |
| `frontend/README.md` | Frontend architecture and conventions |

### Where a fact goes

| It is… | Put it in |
| --- | --- |
| A number the database knows | generated sheet |
| A formula or threshold in code | `TECHNICAL_GUIDE.md`, citing the module |
| A procedure with an order | `docs/how-to/` |
| A failure mode and its remedy | a runbook under `docs/how-to/` |
| A reason a design is the way it is | `TECHNICAL_GUIDE.md` |
| Something broken or unfinished | `BACKLOG.md` |
| Something that shipped | `CHANGELOG.md` |

`README.md` links; it does not explain. `TECHNICAL_GUIDE.md` explains; it does not
instruct. `docs/how-to/` instructs; it does not justify.

### CHANGELOG structure

Every released version uses the same sections:

```markdown
### version 0.1.N[: short title] [✓ when complete]

**Objective**: one or two sentences on what the release was for.

**Implemented Features**:
- grouped bullets, each naming the files or commands touched

**Current Notes**:
- behaviour changes a reader must know, caveats, known limitations

**Key Files**:
- the paths most worth reading to understand the release

**Focused Coverage Added**:
- test modules added or extended
```

Do not commit a version section that is still a working checklist. Draft audits go
in `BACKLOG.md` until they are conclusions.

---

## 3. Code conventions

No formatter or linter is configured for Python — there is no `pyproject.toml`,
`ruff`, `flake8`, `mypy`, or `setup.cfg`. Style is by consistency with surrounding
code. The frontend does have ESLint (`npm run lint`).

Observed Python conventions:

- 4-space indent, single quotes for strings in application code.
- Module-level docstring explaining **why the module exists**, not what each
  function does. `apps/markets/benchmarking.py` is the model.
- `verbose_name` on every model field, wrapped in `gettext_lazy` as `_`.
- `Decimal`, never `float`, for money, prices, probabilities, and ratios. Model
  fields use explicit `max_digits` / `decimal_places`.
- `models.TextChoices` / `IntegerChoices` for every enumerated column.
- Point-in-time lookups use `date__lte=as_of` (or `date <= as_of`) with an
  explicit ordering, never a same-day-only filter, and always behind a freshness
  guard.
- Explicit neutral fallbacks with documented values. Never an implicit `None`
  propagating into arithmetic.
- `db_index=True` on columns that are filtered or joined in hot paths, plus
  explicit `Meta.indexes` and `unique_together` where the combination matters.

When adding a management command:

- Every argument gets `help=` text. It is published verbatim into
  `docs/reference/commands.md`.
- Destructive commands default to **dry run** and require `--execute`.
  `purge_pre_floor_historical_data` and
  `reconcile_suspension_ohlcv_overlaps` are the pattern.
- Long commands accept `--chunk-size-days`, `--checkpoint-file`, and
  `--resume-from-checkpoint`, and write checkpoints under `reports/ops_logs/`.
- Date defaults computed from today are fine — the generator normalises them — but
  prefer an explicit fixed floor (`2010-01-01`) for `--start-date`.

When adding a Celery task:

- Decide the queue deliberately. Long or CPU-heavy work does **not** belong on
  `ops`; add a route in `config/settings/base.py`.
- Set an explicit `soft_time_limit` / `time_limit` if the global 60s / 300s is
  wrong for the task. The override is published into
  `docs/reference/celery.md`.
- Catching an exception and returning a string makes Celery report **success**.
  Either let it raise or make the failure unmistakable in the return value.

---

## 4. Tests

Required for behaviour changes. See [`docs/how-to/testing.md`](docs/how-to/testing.md).

```bash
python manage.py test --keepdb          # backend
cd frontend && npm test                 # frontend
```

Always `--keepdb`. Without it a stale test database triggers an interactive drop
prompt that aborts in any non-interactive shell.

Conventions:

- `django.test.TestCase` with `unittest.mock.patch`. No pytest, no fixtures
  framework.
- Patch collaborators **at the import site**, not the defining module.
- Class names encode the area (`Phase15BacktestTests`,
  `BacktestManagementCommandTests`); add to the matching class.
- Configuration tests read `django.conf.settings` rather than restating expected
  values, so they track the real module.
- Seed rows per test, not per class — ordering must not matter.

Known non-hermetic tests are documented in
[`docs/how-to/testing.md`](docs/how-to/testing.md) §6. Do not add to that list.

---

## 5. Migrations

- One logical schema change per migration.
- Do not rename an existing migration that has been applied anywhere.
- After adding a model field, check whether a backfill is needed to populate
  history. A nullable additive column plus a backfill command is the normal
  pattern — see how `pe_ttm` was introduced alongside `pe`.
- Removing a column that fed a model feature is a **model lifecycle event**, not
  just a migration. Check which artifact generations reference it; see
  [`docs/how-to/retrain.md`](docs/how-to/retrain.md).
- `python manage.py makemigrations --check` should be clean before committing.

---

## 6. Model artifacts

`models/` is **tracked in git** — 132 files including `.pkl`, `.pt`,
`metadata.json`, and `summary.json`. This is deliberate: it makes trained artifacts
portable across hosts without external storage, which matters because stored
`artifact_path` values are absolute and do not survive a host change otherwise.

Consequences to respect:

- Commit a new artifact family together with the registry change that activates
  it, so the repository and the database agree.
- Never overwrite an existing artifact directory. Retrain with a new
  `--version-tag`; `(horizon_days, version)` is unique and rollback depends on
  every generation being retained.
- Artifact binaries grow the repository permanently. Before adding a family, ask
  whether it is a keeper or an experiment. Experiments belong on disk, not in git.
- Regenerate `docs/reference/models.md` after any registry change.

Whether this remains the right tradeoff versus object storage is an open question
in `BACKLOG.md`.

---

## 7. Definition of done

Before committing:

- [ ] `python manage.py test --keepdb` passes, or new failures are documented in
      `BACKLOG.md` with a cause
- [ ] `cd frontend && npm test` and `npm run lint` pass, if frontend code changed
- [ ] `python manage.py export_documentation_facts --check` passes
- [ ] `python manage.py makemigrations --check` reports no changes
- [ ] Generated sheets regenerated and their diff reviewed
- [ ] Authored docs updated where behaviour changed
- [ ] Nothing hand-typed into a generated file
- [ ] No row counts, coverage dates, accuracies, or artifact versions added to
      `README.md` or `TECHNICAL_GUIDE.md`
- [ ] Scratch notes went to `BACKLOG.md`, not into a document
- [ ] Commits split by category, conventional format, body explains why

---

## 8. Getting oriented

Read in this order:

1. [`README.md`](README.md) — the shape of the system
2. [`docs/how-to/local-setup.md`](docs/how-to/local-setup.md) — a running stack
3. [`TECHNICAL_GUIDE.md`](TECHNICAL_GUIDE.md) §1 — the universe contract. Every
   cross-sectional behaviour depends on it
4. [`TECHNICAL_GUIDE.md`](TECHNICAL_GUIDE.md) §2–§3 — stored versus runtime
   features, and why staleness is refused rather than tolerated
5. The generated sheets — current facts
6. [`BACKLOG.md`](BACKLOG.md) — what is known to be broken

The single most common mistake is assuming a documented number is current. It
almost never is. Read the generated sheets.
