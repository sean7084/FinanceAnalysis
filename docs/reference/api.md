# API Usage Guide

Hand-authored. The **shapes** of every request and response are published by
drf-spectacular and are always current:

- Swagger UI — `http://localhost:8000/api/v1/schema/swagger-ui/`
- ReDoc — `http://localhost:8000/api/v1/schema/redoc/`
- Raw OpenAPI 3.0 — `http://localhost:8000/api/v1/schema/`

This document covers the **contracts** the schema cannot express: the fact that
no credential is required anywhere, how pagination and caching actually behave,
and the WebSocket protocol.

The route inventory itself is generated — see [`commands.md`](commands.md) for
management commands and [`celery.md`](celery.md) for background tasks.

---

## 1. Base URL and versioning

All endpoints live under `/api/v1/`. There is no unversioned alias. Django admin
is still at `/admin/`; DRF's browsable-API session login at `/api-auth/` is gone,
because there is no API login for it to perform.

Local development reaches the API through the Vite proxy, so frontend code uses
relative paths:

| Frontend path | Proxied to | Configured in |
| --- | --- | --- |
| `/api` | `http://127.0.0.1:8000` | `frontend/vite.config.ts` |
| `/ws` | `ws://127.0.0.1:8000` (`ws: true`) | `frontend/vite.config.ts` |

---

## 2. No authentication

The API is unauthenticated. `REST_FRAMEWORK['DEFAULT_PERMISSION_CLASSES']` is
`AllowAny` and `DEFAULT_AUTHENTICATION_CLASSES` is `[]`, so **no credential is
required for any method on any endpoint** — reads and writes alike. Nothing is
sent and nothing is read: `lib/api.ts` attaches no `Authorization` header and no
`X-API-Key`. A browser that is logged into `/admin/` does still send its
`sessionid` cookie on same-origin API calls, because `fetch` defaults to
`same-origin` credentials; that cookie is inert, since the empty authentication
list means no authenticator ever consumes it. The empty list — not the omission of
a `credentials` option — is what keeps an admin session from changing how the API
behaves.

**Writes and action routes are public too.** Twenty `@action` routes declared with
`methods=['post']` — model training, news and macro ingestion, screener and factor
recalculation, backtest pause / resume / restart — plus the ordinary
create/update/destroy of every model viewset now execute on an unauthenticated
request, and nothing rate-limits them. Any caller who can reach this API can start
multi-minute training jobs or delete run history, so keep it on localhost or a
network you control.

Everything the previous revision of this section documented is gone — the paths
under `/api/v1/` now return 404:

- the JWT pair endpoints (`/auth/token/`, `/auth/token/refresh/`,
  `/auth/token/verify/`) and the login/session endpoints (`/auth/login/`,
  `/auth/refresh/`, `/auth/logout/`, `/auth/session/`)
- the account endpoints under `/users/` (register, verify-email, password-reset,
  password-reset-confirm, profile, subscriptions, usage)
- the API-key portal `/developer/keys/`, and with it `X-API-Key` authentication
- DRF's browsable-API login at `/api-auth/`, which is no longer routed at all

`djangorestframework-simplejwt` is no longer a dependency, and the `apps.users`
app (user profiles, subscriptions, per-request usage counters) has been deleted.
`django.contrib.auth` and `/admin/` remain — a superuser created with
`python manage.py createsuperuser` is an **admin-only** account and grants no API
access, because the API needs none.

Rate limiting is gone with it: `DEFAULT_THROTTLE_CLASSES` and
`DEFAULT_THROTTLE_RATES` are unset, so no request is ever throttled and the API
produces no `429` responses.

Resources that used to belong to someone no longer do: `ScreenerTemplate`,
`AlertRule`, and `BacktestRun` have no owner or user foreign key, so every
caller sees the same set of saved templates, alert rules, and backtest runs.
Alert notifications are not addressed to a person either — the email channel
sends to the comma-separated `ALERT_NOTIFY_EMAILS` setting (env-configurable;
empty by default, which disables the channel). See [`env.md`](env.md).

---

## 3. Pagination

Every list endpoint uses `apps.core.pagination.ApiPageNumberPagination`.

| Property | Value |
| --- | --- |
| Default page size | `50` |
| Override parameter | `?page_size=` |
| Maximum page size | `1000` |
| Page parameter | `?page=` (1-based) |

```bash
curl -s 'http://localhost:8000/api/v1/ohlcv/?page=2&page_size=500'
```

Response envelope:

```json
{
  "count": 3330683,
  "next": "http://.../ohlcv/?page=3&page_size=500",
  "previous": "http://.../ohlcv/?page=1&page_size=500",
  "results": [ ... ]
}
```

Requesting `page_size` above `1000` is clamped, not rejected. Always paginate
against the large tables — `markets_ohlcv` and `analytics_technicalindicator`
hold millions of rows (current counts in [`metrics.md`](metrics.md)).

---

## 4. Filtering, searching, ordering

Three filter backends are enabled globally, so these query parameters work on
any `ModelViewSet` that declares the supporting attributes:

| Parameter | Backend | Example |
| --- | --- | --- |
| Exact/lookup filters | `DjangoFilterBackend` | `?asset=12&date__gte=2024-01-01` |
| Free-text search | `SearchFilter` | `?search=kweichow` |
| Sorting | `OrderingFilter` | `?ordering=-date,value` |

`ordering` accepts a comma-separated list; prefix with `-` for descending. The
per-endpoint `filterset_fields`, `search_fields`, and `ordering_fields` are
declared on each viewset and are visible in the OpenAPI schema.

Date-range filtering on price and indicator endpoints is inclusive on both
bounds.

---

## 5. Response caching

Several read endpoints are cached server-side with `cache_page`, so a response
may be older than the underlying table. Current tiers:

| TTL | Endpoints |
| --- | --- |
| 24 h | Market list/detail |
| 2 h | Asset list/detail, OHLCV list/detail, two indicator views |
| 5 min | Indicator ranking and screener-style views |

Cached responses carry no distinguishing header. When you need to be certain you
are reading fresh data — after a backfill or a repair run — bypass the cache by
adding a unique query parameter (`?_=<nonce>`), or flush the Redis cache
database.

---

## 6. WebSocket: live alert stream

| Property | Value |
| --- | --- |
| Path | `/ws/alerts/` |
| Consumer | `apps.analytics.consumers.AlertConsumer` |
| Base class | `AsyncJsonWebsocketConsumer` (JSON frames, not text) |
| Channel layer | `channels_redis.core.RedisChannelLayer` on `REDIS_URL` |
| ASGI app | `config.asgi.application` |
| Group | `alerts` — a single global group every client joins |

### Connection

The stream is open. `config/asgi.py` routes `websocket` through a bare
`URLRouter`, with no `AuthMiddlewareStack` in front of it, and the consumer reads
no credential of any kind:

```
ws://localhost:8000/ws/alerts/
```

`connect()` accepts unconditionally — no session cookie, no `?token=` query
parameter, nothing to expire and nothing to refresh on reconnect. Dropping the
auth middleware also dropped a database round-trip per connection, so a socket
now opens without touching `AUTH_USER`.

When the dashboard is served by Django (default: `http://localhost:8000/`), the
browser connects to `ws://localhost:8000/ws/alerts/` directly — same origin, no
proxy. When the pure-Vite HMR flow is used (`http://localhost:5173/`), the Vite
dev server proxies `/ws` to Django, so the browser still sees
`ws://localhost:5173/ws/alerts/`.

Because every client joins the same group, **every connected client receives
every alert**, whatever produced it. Reconnect logic stays on the client
(`frontend/src/hooks/useAlertsSocket.ts`: five attempts, exponential backoff
capped at 15 s).

### Message format

On acceptance the consumer joins the global `alerts` group. Server-pushed frames:

```json
{
  "type": "alert",
  "event_id": 123,
  "asset_symbol": "600519",
  "alert_name": "RSI oversold",
  "message": "RSI(14) crossed below 30",
  "created_at": "2026-06-30T08:15:00Z"
}
```

Clients send nothing; this is a receive-only stream. Alert events are produced
by `apps.analytics.tasks.check_alert_rules`, scheduled every 5 minutes, and are
also persisted for history at `/api/v1/alert-events/`. `asset_symbol` is `null`
for feed-level alerts (`condition_type=FEED_STALE`), which carry no tradable
asset; the frame's `message` names the lagging benchmark or feature feed.

> **Current state:** the alert-rule tables are empty in the reference database,
> so the stream connects and stays silent. Create rules via `/api/v1/alerts/`
> before expecting frames, or leave the seeded `ETF data feed health` rule in
> place — it is created on the first `check_alert_rules` pass and dispatches to
> whatever channels `ALERT_NOTIFY_EMAILS` and the websocket group enable. Row
> counts are in [`metrics.md`](metrics.md).

---

## 7. Error format

DRF's default renderer is JSON, with the browsable renderer also enabled.

| Status | Meaning |
| --- | --- |
| 400 | Validation failure. Body maps field names to lists of messages |
| 404 | Unknown primary key, or a route that no longer exists — including every former `/auth/` and `/users/` path |
| 500 | Unhandled server error |

With no authentication and no throttling there is no credential to reject and no
limit to exceed, so the API returns no 401, no 403, and no 429. (Django's
`/admin/` still enforces its own session and permission checks.)

Validation errors:

```json
{
  "start_date": ["Date has wrong format (expected YYYY-MM-DD)."],
  "top_n": ["Ensure this value is less than or equal to 100."]
}
```

---

## 8. Endpoint groups

Registered in `config/urls.py` on a single `DefaultRouter`. Full request and
response schemas are in Swagger; this is the map.

| Prefix | App | Contents |
| --- | --- | --- |
| `/markets/`, `/assets/`, `/ohlcv/` | markets | Exchanges, instruments, daily bars |
| `/indicators/` | analytics | Stored indicators plus `compare`, `recalculate`, `fibonacci_levels`, and the `top_rsi` / `bottom_rsi` / `trending_strong` / `overbought_stoch` rankings |
| `/dashboard/stocks/` | analytics | Composite board: factors, indicators, sentiment, dual-model trade decisions |
| `/screeners/`, `/screener-templates/` | analytics | 4 pre-built screeners (`overbought_oversold`, `breakout_candidates`, `high_volume`, `trend_reversal`) plus saved templates |
| `/screener/bottom-candidates/` | factors | Bottom-fishing candidate screener |
| `/alerts/`, `/alert-events/` | analytics | Alert rules and fired events |
| `/signals/` | analytics | Signal events |
| `/factors/fundamentals/`, `/factors/capital-flows/` | factors | Snapshot tables behind `FactorScore` |
| `/macro/snapshots/`, `/macro/contexts/`, `/macro/event-impacts/` | macro | Monthly macro surface and inferred regime |
| `/sentiment/news/`, `/sentiment/`, `/sentiment/concepts/` | sentiment | Articles, scores, concept heat |
| `/prediction/`, `/prediction-model-versions/` | prediction | Heuristic predictions and the model registry |
| `/lightgbm-predictions/`, `/lightgbm-models/`, `/ensemble-weights/` | prediction | LightGBM surface |
| `/lstm-predictions/` | prediction | LSTM surface |
| `/backtest/`, `/backtest-trades/` | backtest | Runs, lifecycle actions, comparison curves, trade detail |
| `/etf/universe/`, `/etf/daily/`, `/etf/features/`, `/etf/signals/`, `/etf/positions/`, `/etf/backtests/` | etf_rotation | ETF rotation: fund directory, daily bars, point-in-time features, rotation signals, position legs, and a create/pause/resume/rerun/restart/delete backtest surface |
| `/developer/changelog/` | developer | Public, read-only API changelog |

### Backtest lifecycle actions

`/backtest/` is not read-only. Beyond create/list/retrieve it exposes `rerun`,
pause, resume, restart, and delete transitions, a comparison-curve payload, and
`/backtest/{id}/trades/`. Runs execute asynchronously on the `backtest` Celery
queue; long runs resume through chunked state stored in `BacktestRun.report`.
See [`celery.md`](celery.md) and `docs/how-to/backfill.md`.

### ETF rotation surface

Five of the six `/etf/` routes are `ReadOnlyModelViewSet`: `universe`, `daily`,
`features`, `signals` and `positions` expose GET and nothing else, and their rows
are written by the `etf-rotation` Celery tasks and the `sync_etf_*` /
`compute_etf_features` / `generate_etf_rotation_signal` /
`run_etf_rotation_backtest` commands, never by HTTP. `/etf/backtests/` is the
exception — it now takes the same write surface as `/backtest/` above, so see
**ETF backtest lifecycle** below for what a caller may POST.

Being unauthenticated is the only thing limiting them: under the global
`AllowAny` default any caller who can reach the API can read the whole ETF
surface without a credential, and can launch RULE backtests through
`/etf/backtests/`. Both are accepted because the API is deployed on an internal
network; the write surface is bounded against *accidental* load rather than
against adversaries (see the lifecycle section).

The prefixes are namespaced under `/etf/` because `signals` and `positions` are
already registered by the analytics `SignalEventViewSet` and the prediction
surface; a second registration on those prefixes would shadow them.

Four contracts the schema does not express:

- **Units.** `/etf/daily/` returns `volume` in 份 and `amount` in 元. TuShare's
  `fund_daily` publishes 手 and 千元, and the conversion happens at ingest, so
  `amount / (volume / 100) ≈ close` holds on every row.
- **Label columns are look-only.** `/etf/features/` exposes `fwd_return`,
  `label_rank_pct` and `label_top_bucket` alongside the features. They are
  training targets derived from sessions *after* the snapshot `date`. Reading
  them is safe; feeding them back into a live ranking decision is look-ahead
  bias.
- **Metric scale.** On `/etf/backtests/`, `total_return`, `annualized_return`,
  `max_drawdown`, `win_rate`, `alpha_vs_benchmark` and `benchmark_return` are
  fractions (`0.0775`, not `7.75`). `run_etf_rotation_backtest` prints the same
  numbers as percents, so do not compare the console output to the API payload
  without converting one of them.
- **Delisted funds are included.** `/etf/universe/` returns funds with
  `status='D'` and a `delist_date` on purpose — dropping them would make every
  backtest survivorship-biased. Use `is_active=true`, or `listed_before` (which
  falls back to `first_trade_date` when `list_date` is null), to reproduce a
  point-in-time pool.
- **The defensive sleeve must be a listed money-market ETF.**
  `GET /etf/defensive_assets/` answers the question "what can I actually park
  risk-off cash in", richest first, with the `avg_amount_20d` from the newest
  eligible cross-section next to the liquidity floor the engine applies. It is an
  `@action` on the universe viewset published at a short path rather than
  `/etf/universe/defensive_assets/`, and it does not change how many read-only
  routes exist. A `defensive_asset` with no bars is refused at create time
  (`400 {'defensive_asset': ...}`) rather than discovered at settlement: the
  engine only buys the sleeve on a rebalance, so a dead code used to complete
  successfully while every risk-off month sat in phantom cash. Brokerage cash
  sweep products (a 零钱宝-style 保证金理财) are not in this list and cannot be:
  no row in `ETFUniverse` matches `零钱`, because they are OTC contracts with a
  NAV series rather than an exchange-listed fund with daily bars, so they can be a
  sleeve in an account statement but not in a bar-driven backtest. The nearest
  listed equivalents are the money-market ETFs the endpoint already returns.
  `ETF_DEFENSIVE_ASSET` (default `511880.SH`) is the single
  source for the default; the engine, the model field and the workbench all read it.

### ETF signal freshness

`GET /api/v1/etf/signals/freshness/` answers what the signal row cannot answer
about itself: whether the pool underneath it has moved on. `signal_date` is an
**as-of** date, not a generation time, so the card is only honest while the two
agree. The endpoint compares it against the newest `ETFFeatureSnapshot` with
`is_eligible=true` and counts the `ETFDaily` sessions strictly between them —
sessions the strategy *could* have been re-planned on — so `lag_sessions=0` means
current whatever the calendar says, and a long weekend is not three sessions of
staleness. Payload: `{signal_date, latest_snapshot_date, latest_bar_date,
lag_sessions, is_stale, model_version, last_attempt, feed_stale}`; `is_stale` is
`lag_sessions >= 1`. With no signal row the answer is `signal_date=null,
lag_sessions=0, is_stale=false`, which the page renders as "unknown freshness"
rather than as a confident date. For a RULE signal `model_version` is the row's
own derived tag from the knobs that change the book —
`rule-v1:mom60-score:dedup:cap2` — because RULE has no `ModelVersion` row to
point at; it degrades to bare `rule-v1` when the configuration is unavailable.

`last_attempt` is the newest non-health `ETFRotationSignalRun` (`date`,
`signal_type`, `outcome`, `reason_code`, `message`, `created_at`) or `null` when
no attempt has ever been recorded; the page warns whenever `outcome` is not `OK`,
so a refusal that wrote no signal row is still visible. Feed-health watchdog rows
are excluded, because a stale feed is reported by `feed_stale` and must not make
the page claim the last signal job failed. `feed_stale` is the newest
`FEED_STALE` health row **for the newest `ETFDaily` date only** — once bars
advance without a new defect the block clears itself rather than repeating
yesterday's alert. Both blocks are independent of `is_stale`, which describes
the signal row's own as-of lag.

`/etf/signals/latest/` returns the newest signal matching the same filters as
the list route, and answers `404` rather than an empty page when none exists.
`/etf/backtests/` paginates 20 rows at a time (`page_size`, capped at 200)
instead of the global 50, because a `--grid` run writes hundreds of rows and the
leaderboard is only readable ordered: `sort` accepts `alpha`, `sharpe`,
`return`, `drawdown` and `created`, and anything else falls back to newest
first. Runs that never computed a metric sort last on every key rather than
first, so an aborted run cannot head the table. The route also accepts
`?dedup_track_index=true|false`: the 2026-10 rows written while
`--no-dedup-track-index` was silently applied describe themselves in that column,
so `true` reads the production-rule rows alone without hiding the intended
dedup-off control (row 355) when nobody passes the filter.

Two read surfaces separate the ML rematch from production without a second table.
`/etf/signals/` and `/etf/signals/latest/` exclude `signal_type=SHADOW` unless a
caller asks for it by name, and `/etf/positions/` applies the same rule (matching
either the leg's own signal or its `metadata.signal_type`), so the realised-PnL
card sums the production book alone. `?signal_type=SHADOW` is how the shadow book
is inspected.

One field in `report['closed_trades']` is not comparable across generations.
`position_fees_paid` carried commission only before 0.1.23; later rows also include
the slippage their fills embedded. The field never stored the split, so there is no
backfill — compare across versions with the `total_fees` / `total_slippage_cost`
metric columns instead.

### ETF backtest lifecycle

`/etf/backtests/` is the one ETF route that writes. It answers GET (list and
retrieve), POST, and DELETE; `http_method_names` omits PUT and PATCH on purpose,
because a run's configuration *is* its identity: the fingerprint in
`report['runtime_state']` is what lets a paused row resume, so editing the window
or the knobs of a live row would resume a different computation under resumable
state belonging to another one. Change a run by creating a new one.

`POST /etf/backtests/` validates, then acts in the order dedupe → cap → persist →
queue, and the status code says which step answered:

- `400` from the serializer: a non-`RULE` `model_type` (ML backtests need a
  booster trained and registered by command, so they go through
  `run_etf_rotation_backtest`, not HTTP), `end_date` before `start_date`, an
  `end_date` in the future, or a non-positive `max_positions`,
  `momentum_lookback` or `initial_capital`.
- `200`, not `202`, with the *existing* row when an in-flight PENDING/RUNNING run
  already carries the same `BacktestConfig` fingerprint. Nothing new was accepted,
  so a client that retries a POST it already has running gets its row back instead
  of a second copy of the same computation — and never a 429 it cannot act on,
  because dedupe runs before the cap.
- `429` when `ETF_MAX_CONCURRENT_BACKTESTS` (default 2) rows are already PENDING
  or RUNNING. DRF throttling is not configured repo-wide, so this application-level
  semaphore is the entire bound. It counts CLI and `--grid` rows too, excludes
  PAUSED on purpose (a paused row holds a cursor, not a worker slot), and fails
  closed: a cap of 0 or less refuses every creation.
- `202` with the new row. The task is queued on the `etf-rotation` Celery queue
  inside `transaction.on_commit`, so nothing is published for a row that never
  committed.

The `202` body is the **list** serializer — `get_serializer_class` switches on
`self.detail` and a POST is not a detail view — so it carries no `report`, exactly
like a list row. That is the same contract as the dropped
`equity_curve`/`trade_log`/`report`: **progress is detail-only**, so a client
watches `report['runtime_state']['cursor']` by polling `GET /etf/backtests/{id}/`.

Writable fields are `name`, `start_date`, `end_date`, `rebalance_frequency`,
`max_positions`, `momentum_lookback`, `absolute_momentum_filter`,
`defensive_asset`, `weighting` and `initial_capital`. `model_type` is writable but
pinned to RULE, because a read-only field would silently drop a caller's `LSTM`
and answer 202 with a RULE row it did not ask for — worse than the 400. The cost
model is not writable at all: `commission_rate`, `min_commission`, `slippage`,
`liquidity_floor_multiple` and `benchmark_code` come from their column defaults,
which are the same numbers `config/settings/base.py` assigns to
`ETF_COMMISSION_RATE`, `ETF_MIN_COMMISSION`, `ETF_SLIPPAGE`,
`ETF_LIQUIDITY_FLOOR_MULTIPLE` and `ETF_BENCHMARK_CODE`, and
`walk_forward_steps` is pinned to 0 because the API does not do walk-forward runs.
Note the asymmetry: the API reads the *column* default and the CLI reads the
*settings* value, so overriding one of those in `.env` decouples the two surfaces
and an API-launched run will no longer dedupe against the identical CLI-launched
one.

Lifecycle transitions are `POST /etf/backtests/{id}/pause|resume|rerun|restart/`
plus `DELETE /etf/backtests/{id}/`. They answer with `{'message', 'id'}` rather
than serializer output: the row a client should act on is the one it re-reads, and
echoing a body for an intent that has not landed yet would be misleading.

- **pause** — `400` unless the row is PENDING or RUNNING. PENDING, and RUNNING
  whose task owner is gone, go to `PAUSED` in place, because there is no live
  chunk to protect; pausing a PENDING row is also what stops its queued task from
  starting work, since the task re-reads `PAUSED` before it touches the engine.
  RUNNING with a live owner writes `pending_control_action=PAUSE`.
- **resume** — `400` unless `PAUSED`; otherwise PENDING again with the control
  intent cleared and re-queued. `report['runtime_state']` is untouched, so the run
  continues from its cursor rather than restarting.
- **restart** and **rerun** are one action under two names, matching the stock
  route. A live-owned RUNNING row gets `pending_control_action=RESTART` and the
  engine hands the halt back to the task wrapper, which wipes the result columns
  and re-queues; any other status is reset and re-queued immediately.
- **delete** — a live-owned RUNNING row gets `pending_control_action=DELETE` and
  the loop deletes itself at the boundary; everything else is deleted outright with
  `204`. Deleting a RUNNING row outright would let `_persist_progress` re-INSERT it
  under the same primary key and leave a row with no owner.

Pause, restart and delete are **coarse**: they land at a `chunk_trading_days`
boundary (default 120 sessions), so a 202 means "requested" and the status changes
when the engine gets there. ETF never revokes the worker task to make a request
land faster — a live task reaches the boundary on its own, a dead one cannot be
revoked, `terminate=True` discards the in-flight chunk, and revoking a task that is
already winding down would make the stale-owner check flag a finishing row as
orphaned.

Both list and detail rows carry `task_state` and `has_stale_task_owner`, resolved
by `apps.core.task_health` — the same helper [`celery.md`](celery.md) describes for
the stock route — because `status='RUNNING'` cannot by itself tell an advancing run
from one whose worker died. They short-circuit for any non-RUNNING row and are
resolved once per row per request, not once per field, so a 20-row page costs at
most 20 broker lookups.

List filtering covers the dimensions a grid sweep varies: `status`, `model_type`
and `rebalance_frequency` are compared uppercased, `weighting` lowercased — it is a
column since the lifecycle migration and the grid stores `equal`/`score`, so
`?weighting=SCORE` matches — plus `window_start`/`window_end` as inclusive bounds
on the run's own window and a case-insensitive `name` substring, which is how
`grid-rule-*` rows are found by prefix.

`/etf/backtests/{id}/comparison_curve/` is the one ETF action that is not a table
read. It rebuilds the strategy series from `equity_curve` and every benchmark
series by querying `BenchmarkIndexDaily` for the row's window, because
`_comparison_bundle` computes a benchmark curve but never persists one --
recomputing is what makes a run that predates this endpoint chartable at all. All
series are rescaled onto the strategy's opening equity and forward-filled onto the
strategy's own session grid, so the line on the chart is the sample the stored
metrics were measured against rather than a different sample of the same index. A
requested code with no rows is named in `message` instead of being silently
dropped. `?extra_ids=1,2` (or repeated `?extra_compare_run_id=`, which is what the
stock chart component sends) overlays other COMPLETED runs on the same scale, and
an unparseable overlay id is ignored rather than failing the request. Terminal
statuses are cached under `BACKTEST_PAYLOAD_CACHE_SECONDS` with `status`,
`updated_at` and the overlay ids in the key, so a write to a finished row or a
different overlay set cannot serve a stale curve.
