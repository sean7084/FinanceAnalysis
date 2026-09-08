# FinanceAnalysis

A Django-based platform for analysing Chinese A-share markets over the CSI 300 /
CSI A500 benchmark universe. It ingests market, fundamental, macro, and news
data; derives technical and factor features; runs three prediction models; and
backtests trading strategies against point-in-time benchmarks.

Bilingual (English / Simplified Chinese). REST API plus a React dashboard and a
live WebSocket alert stream.

---

## Documentation map

This README is orientation only. Everything else is split by purpose.

| I want to… | Read |
| --- | --- |
| Understand *why* the system works the way it does | [`TECHNICAL_GUIDE.md`](TECHNICAL_GUIDE.md) |
| Set up a machine | [`docs/how-to/local-setup.md`](docs/how-to/local-setup.md) |
| Populate or repair the database | [`docs/how-to/backfill.md`](docs/how-to/backfill.md) |
| Retrain, promote, or roll back a model | [`docs/how-to/retrain.md`](docs/how-to/retrain.md) |
| Run the tests | [`docs/how-to/testing.md`](docs/how-to/testing.md) |
| Call the API | [`docs/reference/api.md`](docs/reference/api.md) |
| Look up a command's options | [`docs/reference/commands.md`](docs/reference/commands.md) *(generated)* |
| Look up a Celery task or queue | [`docs/reference/celery.md`](docs/reference/celery.md) *(generated)* |
| Look up an environment variable | [`docs/reference/env.md`](docs/reference/env.md) *(generated)* |
| Look up current data coverage | [`docs/reference/metrics.md`](docs/reference/metrics.md) *(generated)* |
| Look up the deployed model registry | [`docs/reference/models.md`](docs/reference/models.md) *(generated)* |
| Diagnose a failed sync or stuck task | [`docs/how-to/runbook-sync-failure.md`](docs/how-to/runbook-sync-failure.md) |
| Diagnose missing provider data | [`docs/how-to/runbook-provider-blackout.md`](docs/how-to/runbook-provider-blackout.md) |
| Diagnose stale derived data | [`docs/how-to/runbook-stale-data.md`](docs/how-to/runbook-stale-data.md) |
| Work on the frontend | [`frontend/README.md`](frontend/README.md) |
| Contribute | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| See what shipped | [`CHANGELOG.md`](CHANGELOG.md) |
| See what is unfinished | [`BACKLOG.md`](BACKLOG.md) |

The five *generated* sheets are produced from the live database and repository
configuration, never hand-edited:

```bash
python manage.py export_documentation_facts          # regenerate
python manage.py export_documentation_facts --check   # fail if stale
```

---

## Architecture

Ten Django apps with a strict upstream → derived ordering.

| App | Responsibility |
| --- | --- |
| `core` | Pagination, tiered throttling, historical floor, data-quality validation, docs generation |
| `markets` | Universe and price foundation: assets, OHLCV, trading calendar, suspensions, index membership, benchmarks |
| `analytics` | Stored technical indicators, signal events, alerts, screeners, dashboard DTO |
| `factors` | Fundamentals, money flow, margin detail, capital flow, factor scores |
| `macro` | Monthly macro surface and inferred market regime |
| `sentiment` | News ingestion, article and aggregate scoring, concept heat |
| `prediction` | Heuristic, LightGBM, and LSTM models; trade decisions; model registry |
| `backtest` | Run engine, trades, comparison curves, benchmark export |
| `users` | Authentication, subscriptions, usage metering |
| `developer` | API key portal and public changelog |

### Data flow

```
TuShare / AkShare
        │
        ▼
    markets ──── OHLCV · trading calendar · suspensions · index membership
        │
        ├──────────────┬───────────────┬───────────────┐
        ▼              ▼               ▼               ▼
   analytics        factors          macro         sentiment
  indicators,     fundamentals,    yields, PMI,   news, scores,
  signals, RS     capital flow,    CPI, regime    concept heat
        │              │               │               │
        └──────────────┴───────┬───────┴───────────────┘
                               ▼
                          prediction
              heuristic · LightGBM · LSTM · trade decisions
                               │
                               ▼
                            backtest
        runtime candidate generation · TP/SL exits · fee model
                               │
                               ▼
              REST API · WebSocket · React dashboard · reports/
```

### Cross-cutting contract

One rule governs every cross-sectional calculation, training-sample filter,
backtest candidate pool, benchmark build, and daily prediction:

```
2010-01-04 <= date <  2024-09-23   ->  CSI 300 only
              date >= 2024-09-23   ->  CSI 300 ∪ CSI A500
```

Silent fallback to "all assets" is prohibited — workflows fail closed when
point-in-time membership coverage is missing. The canonical implementation is
`apps/markets/benchmarking.py`. A shared `2010-01-01` historical floor
(`HISTORICAL_DATA_FLOOR`) bounds routine backfills.

### Runtime components

Django + DRF serve the REST API, admin, and OpenAPI schema. Channels over Redis
carries the WebSocket alert stream through ASGI. PostgreSQL is the primary store;
Redis holds the cache, the Celery broker, and the Channels layer. Celery Beat
drives the daily schedule across four queues (`ops`, `backtest`,
`train-lightgbm`, `train-lstm`). The React 19 + Vite dashboard runs on port 5173
and proxies `/api` and `/ws` to the backend.

---

## Quickstart

Full detail, including TA-Lib and service provisioning, is in
[`docs/how-to/local-setup.md`](docs/how-to/local-setup.md).

```bash
# 1. Environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1              # or: source .venv/bin/activate
pip install -r requirements/local.txt

# 2. Configuration
Copy-Item .env.example .env               # or: cp .env.example .env
#    then edit DATABASE_URL, REDIS_URL, CELERY_BROKER_URL, TUSHARE_TOKEN

# 3. Verify PostgreSQL, Redis, and Python dependencies
.\scripts\verify_local_stack.ps1          # or: ./scripts/verify_local_stack.sh

# 4. Schema
python manage.py migrate
python manage.py createsuperuser

# 5. Frontend
cd frontend && npm install && cd ..

# 6. Run everything — VS Code task "Run local stack", or four terminals
```

| Surface | URL |
| --- | --- |
| Dashboard | `http://localhost:5173/` |
| API | `http://localhost:8000/api/v1/` |
| Admin | `http://localhost:8000/admin/` |
| Swagger | `http://localhost:8000/api/v1/schema/swagger-ui/` |

The database starts empty. Populating it is a separate multi-stage workflow —
[`docs/how-to/backfill.md`](docs/how-to/backfill.md).

**Celery workers consume only the queues they are told to.** A single default
worker will never run a backtest or a retrain. See
[`docs/how-to/local-setup.md`](docs/how-to/local-setup.md) §11.

---

## Tests

```bash
python manage.py test --keepdb      # backend, 339 tests
cd frontend && npm test             # frontend, 31 tests
```

Always pass `--keepdb`. Without it a stale test database triggers an interactive
drop prompt that fails in any non-interactive shell. Detail in
[`docs/how-to/testing.md`](docs/how-to/testing.md).

---

## Deployment

**Not implemented.** There is no CI configuration, no infrastructure-as-code, and
no ASGI/WSGI server in `requirements/production.txt` — which the WebSocket
surface would require. The local stack runs `manage.py runserver`.

A cloud deployment (managed PostgreSQL and Redis, container orchestration, CI/CD,
metrics, error tracking, object storage for artifacts and report exports) is a
plan, not a capability. Tracked in [`BACKLOG.md`](BACKLOG.md).

---

## Licence

Private and proprietary — all rights reserved. See [`LICENSE`](LICENSE).
