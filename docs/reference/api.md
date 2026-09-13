# API Usage Guide

Hand-authored. The **shapes** of every request and response are published by
drf-spectacular and are always current:

- Swagger UI — `http://localhost:8000/api/v1/schema/swagger-ui/`
- ReDoc — `http://localhost:8000/api/v1/schema/redoc/`
- Raw OpenAPI 3.0 — `http://localhost:8000/api/v1/schema/`

This document covers the **contracts** the schema cannot express: which
credential to use where, how pagination and throttling actually behave, and the
WebSocket protocol.

The route inventory itself is generated — see [`commands.md`](commands.md) for
management commands and [`celery.md`](celery.md) for background tasks.

---

## 1. Base URL and versioning

All endpoints live under `/api/v1/`. There is no unversioned alias. Django admin
is at `/admin/` and DRF's browsable-API session login at `/api-auth/`.

Local development reaches the API through the Vite proxy, so frontend code uses
relative paths:

| Frontend path | Proxied to | Configured in |
| --- | --- | --- |
| `/api` | `http://127.0.0.1:8000` | `frontend/vite.config.ts` |
| `/ws` | `ws://127.0.0.1:8000` (`ws: true`) | `frontend/vite.config.ts` |

---

## 2. Authentication

Three authentication classes are enabled, tried in this order
(`REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES']`):

1. `JWTAuthentication` — `Authorization: Bearer <access>`
2. `APIKeyAuthentication` — `X-API-Key: <key>`
3. `SessionAuthentication` — Django session cookie (admin and browsable API)

The default permission is `IsAuthenticatedOrReadOnly`: **GET/HEAD/OPTIONS are
open to anonymous callers**, everything else requires a credential. Individual
views may tighten this — `ScreenerViewSet` requires `IsAuthenticated` even for
reads.

### 2.1 JWT — for the dashboard and interactive use

```bash
# Obtain a token pair
curl -s -X POST http://localhost:8000/api/v1/auth/token/ \
  -H 'Content-Type: application/json' \
  -d '{"username":"<user>","password":"<password>"}'
# -> {"access":"eyJ...","refresh":"eyJ..."}

# Use it
curl -s http://localhost:8000/api/v1/dashboard/stocks/ \
  -H "Authorization: Bearer eyJ..."

# Refresh (rotation is enabled; the old refresh token is blacklisted)
curl -s -X POST http://localhost:8000/api/v1/auth/token/refresh/ \
  -H 'Content-Type: application/json' \
  -d '{"refresh":"eyJ..."}'

# Verify without calling a resource
curl -s -X POST http://localhost:8000/api/v1/auth/token/verify/ \
  -H 'Content-Type: application/json' \
  -d '{"token":"eyJ..."}'
```

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/v1/auth/token/` | POST | Obtain access + refresh |
| `/api/v1/auth/token/refresh/` | POST | Rotate refresh → new access |
| `/api/v1/auth/token/verify/` | POST | Validate a token |

Token lifetimes (`SIMPLE_JWT`): access **60 minutes**, refresh **7 days**,
`ROTATE_REFRESH_TOKENS=True`, `BLACKLIST_AFTER_ROTATION=True`. The
`token_blacklist` app is installed, so used refresh tokens are persisted as
revoked — do not reuse a refresh token after rotating it.

### 2.2 API key — for programmatic and third-party use

```bash
# Issue a key (requires a JWT; the key is returned once, in plaintext)
curl -s -X POST http://localhost:8000/api/v1/developer/keys/ \
  -H "Authorization: Bearer eyJ..." \
  -H 'Content-Type: application/json' \
  -d '{"name":"my-etl-job"}'

# Use it
curl -s http://localhost:8000/api/v1/ohlcv/?symbol=600519 \
  -H 'X-API-Key: <key>'
```

Key management lives under `/api/v1/developer/keys/` (create, list, rotate,
revoke) and includes sandbox keys for testing. Keys are hashed at rest; the
plaintext is only available in the creation response.

### 2.3 Registration and account flows

| Endpoint | Method | Notes |
| --- | --- | --- |
| `/api/v1/users/register/` | POST | Creates the user and `UserProfile` |
| `/api/v1/users/verify-email/` | POST | Confirms an emailed token |
| `/api/v1/users/password-reset/` | POST | Sends the reset email |
| `/api/v1/users/password-reset-confirm/` | POST | Completes the reset |
| `/api/v1/users/profile/` | GET/PUT | `UserProfile`, incl. `subscription_tier` |
| `/api/v1/users/subscriptions/` | GET | Subscription state |
| `/api/v1/users/usage/` | GET | Per-day API usage counters |

Registration payload:

```json
{
  "username": "testuser",
  "email": "test@example.com",
  "password": "TestPassword123!",
  "password_confirm": "TestPassword123!",
  "first_name": "Test",
  "last_name": "User"
}
```

Outbound links in these emails are built from the `FRONTEND_URL` setting, which
defaults to `http://localhost:5173` — the Vite dev server port, pinned with
`strictPort: true` so it never falls back to another port. Override it for any
deployed environment. See [`env.md`](env.md).

Email is sent through the console backend by default, so reset and verification
messages are printed to the Django process stdout rather than delivered.

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

## 5. Rate limiting

Throttling is tier-based, keyed on `request.user.profile.subscription_tier`.

| Scope | Limit | Applies to |
| --- | --- | --- |
| `anon` | 100/day | Unauthenticated callers, by IP |
| `auth` | 100/day | JWT login/refresh endpoints only |
| `free` | 100/day | Authenticated `FREE` tier |
| `pro` | 1000/day | Authenticated `PRO` tier |
| `premium` | 10000/day | Authenticated `PREMIUM` tier |
| `user` | 1000/day | Legacy scope, kept for backward compatibility |

The three tier throttles (`FreeUserRateThrottle`, `ProUserRateThrottle`,
`PremiumUserRateThrottle`) each **short-circuit to allow** when the caller's
tier does not match their scope, so exactly one tier limit applies per request.
The `auth` scope is deliberately separate from `anon` so login traffic can be
relaxed independently of the public API surface.

When a limit is exceeded DRF returns:

```http
HTTP/1.1 429 Too Many Requests
Allow: GET, HEAD, OPTIONS
Retry-After: 43200
Content-Type: application/json

{"detail": "Request was throttled. Expected available in 43200 seconds."}
```

Counters live in the Redis cache (`REDIS_URL`, database 1 by default) and reset
on the DRF throttle window, not at local midnight. Per-user request history is
also persisted to `users_apiusage` by `apps.users.middleware.APIUsageMiddleware`
and exposed at `/api/v1/users/usage/`.

---

## 6. Response caching

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

## 7. WebSocket: live alert stream

| Property | Value |
| --- | --- |
| Path | `/ws/alerts/` |
| Consumer | `apps.analytics.consumers.AlertConsumer` |
| Base class | `AsyncJsonWebsocketConsumer` (JSON frames, not text) |
| Channel layer | `channels_redis.core.RedisChannelLayer` on `REDIS_URL` |
| ASGI app | `config.asgi.application` |

### Authentication

Two mechanisms, tried in order:

1. **Session cookie.** `config/asgi.py` wraps the router in
   `AuthMiddlewareStack`, so an existing Django session authenticates the socket.
2. **JWT query parameter.** When the scope user is anonymous, the consumer reads
   `?token=<access token>` and validates it as a SimpleJWT `AccessToken`.

```
ws://localhost:8000/ws/alerts/?token=eyJhbGciOiJIUzI1NiIs...
```

Through the Vite proxy the frontend connects to `ws://localhost:5173/ws/alerts/`.

If neither yields an authenticated, active user the consumer **closes the socket
immediately without accepting it** — the client sees a close event, not a 401.
Retry logic belongs on the client (`frontend/src/hooks/useAlertsSocket.ts`).

The access token expires after 60 minutes. A long-lived socket opened with a
query token is not re-validated after connect, but a reconnect needs a fresh
token; `frontend/src/lib/api.ts` exposes `getSocketAuthToken()` for exactly this.

### Message format

On acceptance the consumer joins the per-user group `alerts_user_{user_id}`.
Server-pushed frames:

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

## 8. Error format

DRF's default renderer is JSON, with the browsable renderer also enabled.

| Status | Meaning |
| --- | --- |
| 400 | Validation failure. Body maps field names to lists of messages |
| 401 | Missing or invalid credential on a write, or on a read requiring auth |
| 403 | Authenticated but not permitted |
| 404 | Unknown primary key, or a detail route that does not exist |
| 429 | Throttled — see §5 |
| 500 | Unhandled server error |

Validation errors:

```json
{
  "start_date": ["Date has wrong format (expected YYYY-MM-DD)."],
  "top_n": ["Ensure this value is less than or equal to 100."]
}
```

---

## 9. Endpoint groups

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
| `/developer/keys/`, `/developer/changelog/` | developer | API key portal and public changelog |
| `/users/profile/`, `/users/subscriptions/`, `/users/usage/` | users | Account surface |

### Backtest lifecycle actions

`/backtest/` is not read-only. Beyond create/list/retrieve it exposes `rerun`,
pause, resume, restart, and delete transitions, a comparison-curve payload, and
`/backtest/{id}/trades/`. Runs execute asynchronously on the `backtest` Celery
queue; long runs resume through chunked state stored in `BacktestRun.report`.
See [`celery.md`](celery.md) and `docs/how-to/backfill.md`.
