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
also persisted for history at `/api/v1/alert-events/`.

> **Current state:** the alert-rule tables are empty in the reference database,
> so the stream connects and stays silent. Create rules via `/api/v1/alerts/`
> before expecting frames. Row counts are in [`metrics.md`](metrics.md).

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
| `/etf/universe/`, `/etf/daily/`, `/etf/features/`, `/etf/signals/`, `/etf/positions/`, `/etf/backtests/` | etf_rotation | ETF rotation: fund directory, daily bars, point-in-time features, rotation signals, position legs, backtest runs |
| `/developer/changelog/` | developer | Public, read-only API changelog |

### Backtest lifecycle actions

`/backtest/` is not read-only. Beyond create/list/retrieve it exposes `rerun`,
pause, resume, restart, and delete transitions, a comparison-curve payload, and
`/backtest/{id}/trades/`. Runs execute asynchronously on the `backtest` Celery
queue; long runs resume through chunked state stored in `BacktestRun.report`.
See [`celery.md`](celery.md) and `docs/how-to/backfill.md`.

### ETF rotation surface

All six `/etf/` routes are `ReadOnlyModelViewSet`, so they expose GET and
nothing else — unlike `/backtest/` above. Rows are written by the `etf-rotation`
Celery tasks and the `sync_etf_*` / `compute_etf_features` /
`generate_etf_rotation_signal` / `run_etf_rotation_backtest` commands, never by
HTTP, so there are no create, update or delete transitions to document. Being
read-only is the only thing limiting them: under the global `AllowAny` default
any caller who can reach the API can read the whole ETF surface without a
credential.

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

`/etf/signals/latest/` returns the newest signal matching the same filters as
the list route, and answers `404` rather than an empty page when none exists.
`/etf/backtests/` paginates 20 rows at a time (`page_size`, capped at 200)
instead of the global 50, because a `--grid` run writes hundreds of rows and the
leaderboard is only readable ordered: `sort` accepts `alpha`, `sharpe`,
`return`, `drawdown` and `created`, and anything else falls back to newest
first.
