# FinanceAnalysis Frontend

React 19 + TypeScript + Vite dashboard for the FinanceAnalysis platform. Talks to
the Django API over a dev proxy and receives live alerts over WebSocket.

This replaces the original Vite scaffold README, which described the template
rather than this application.

---

## Commands

```bash
npm install
npm run dev        # http://localhost:5173
npm test           # vitest run — single pass
npm run lint       # eslint
npm run build      # tsc -b && vite build
npm run preview    # serve the production build
```

### Why the scripts call Node directly

`package.json` invokes binaries as `node ./node_modules/vite/bin/vite.js` rather
than `vite`. This is deliberate: it bypasses npm's platform shim layer, which is
where a class of Windows path-resolution failures originates in this environment.

Keep that form when adding scripts. Use `npm run <script>`, not `npx <binary>` —
`npx` reintroduces the shim.

To watch tests during development, call Vitest without the `run` argument:

```bash
node ./node_modules/vitest/vitest.mjs
```

---

## Dev server and proxy contract

`vite.config.ts`:

```ts
server: {
  host: '0.0.0.0',
  port: 5173,
  strictPort: true,
  proxy: {
    '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    '/ws':  { target: 'ws://127.0.0.1:8000',   changeOrigin: true, ws: true },
  },
}
```

`strictPort: true` means Vite **fails** rather than silently moving to 5174 when
the port is taken. That is intentional — the backend's `FRONTEND_URL` and any
bookmarked links assume 5173.

Because both `/api` and `/ws` are proxied, application code uses **relative**
paths and never hardcodes a backend origin. `lib/api.ts` defaults to
`import.meta.env.VITE_API_BASE_URL ?? '/api/v1'`.

The backend must be running on `127.0.0.1:8000`. See
[`../docs/how-to/local-setup.md`](../docs/how-to/local-setup.md).

---

## Routes

Defined in `App.tsx` with `createBrowserRouter`, all nested under an `AppShell`
layout and lazy-loaded through `React.lazy` + `Suspense`.

| Path | Page | Purpose |
| --- | --- | --- |
| `/` | `DashboardPage` | Composite stock board: factors, indicators, sentiment, dual-model trade decisions |
| `/indicator-board` | `IndicatorBoardPage` | All-stocks technical indicator board |
| `/stock/:symbol` | `StockDetailPage` | Single-asset detail with candlestick and probability charts |
| `/macro` | `MacroContextPage` | Macro snapshots and inferred market regime |
| `/models` | `ModelMonitoringPage` | Model registry, artifacts, ensemble weights, feature importance |
| `/backtest` | `BacktestWorkbenchPage` | Run creation, rolling batches, comparison curves, trade detail |
| `/alerts` | `AlertCenterPage` | Live WebSocket stream plus `/alert-events/` history |
| `/settings` | `SettingsPage` | Credentials and auth persistence mode |

> `pages/ScreenerPage.tsx` exists but is **not routed** — nothing imports it. The
> screener workflow was absorbed into the dashboard. Remove it or wire it up;
> tracked in `../BACKLOG.md`.

---

## Layout

```
src/
  App.tsx                     Router + lazy page loading
  main.tsx                    Entry point
  i18n.tsx                    Bilingual dictionary and useI18n()
  types.ts                    Shared DTO types
  App.css / index.css         Styling (no CSS framework)
  lib/
    api.ts                    HTTP client, auth storage, all endpoint calls
    dashboardCandidateFilters.ts   Dashboard filter bundle <-> URL search params
  hooks/
    useAlertsSocket.ts        WebSocket alert stream
  components/
    layout/AppShell.tsx           Chrome, navigation
    layout/AuthSettingsPanel.tsx  Credential entry and persistence mode
    charts/CandlestickChart.tsx        lightweight-charts
    charts/ProbabilityChart.tsx        recharts
    charts/BacktestComparisonChart.tsx recharts
  pages/                      One file per route, plus *.test.tsx siblings
  test/setup.ts               Vitest setup, registered by vite.config.ts
```

Two chart libraries, deliberately: `lightweight-charts` for the candlestick view
(it is built for financial time series and handles large bar counts), `recharts`
for everything else.

---

## Authentication

`lib/api.ts` owns all credential handling. Storage keys:

| Key | Holds |
| --- | --- |
| `finance_jwt` | Access token |
| `finance_jwt_refresh` | Refresh token |
| `finance_api_key` | Developer API key |
| `finance_auth_persistence` | The persistence mode itself |

`AuthPersistenceMode` is `'local' | 'session' | 'none'`:

| Mode | Storage | Survives | Use when |
| --- | --- | --- | --- |
| `local` | `localStorage` | Browser restart | Your own machine |
| `session` | `sessionStorage` | Tab only | Shared machine |
| `none` | memory only | Page lifetime | Untrusted environment |

Reads check `sessionStorage` first, then `localStorage`, so a session-mode
credential shadows a stale local one. `saveAuthSettings()` clears **all three**
keys before writing, which is what makes switching modes safe — a mode change
without the clear would leave the previous mode's token recoverable.

Requests attach `Authorization: Bearer <jwt>` when a JWT is present, else
`X-API-Key: <key>`. `hasAnyAuthCredential()` gates UI that requires auth.

Token acquisition helpers: `obtainJwtToken()` (access only) and
`obtainJwtTokenPair()` (access + refresh). `createDeveloperApiKey()` exchanges a
JWT for a long-lived API key, which is the path the dashboard uses to avoid
re-authenticating every hour.

Access tokens live 60 minutes; refresh tokens 7 days with rotation and
blacklisting. See [`../docs/reference/api.md`](../docs/reference/api.md) §2.

Generic transport is `apiGet` / `apiPost` / `apiDelete`, all typed and all
funnelling through the same auth and error handling.

---

## WebSocket alerts

`hooks/useAlertsSocket.ts` connects to `/ws/alerts/` and exposes the live alert
stream to `AlertCenterPage`.

URL resolution:

```ts
const SOCKET_URL = import.meta.env.VITE_ALERTS_WS_URL ?? defaultSocketUrl()
```

`defaultSocketUrl()` returns a Vite-proxied dev URL in development, and otherwise
derives from `window.location` — choosing `wss:` under HTTPS and `ws:` under HTTP,
so a production deployment behind TLS works without configuration.

Authentication is a JWT access token passed as `?token=`, obtained through
`getSocketAuthToken()` in `lib/api.ts`. The server accepts either a session or the
query token; if neither yields an authenticated user it closes the socket without
accepting it, so the client sees a close event rather than a 401. Reconnect and
token-refresh logic therefore belongs in the hook.

Frames are JSON:

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

The socket is receive-only. Because alert rules are user-provisioned and the table
starts empty, a silent stream is the expected state on a fresh database — see
[`../docs/reference/metrics.md`](../docs/reference/metrics.md).

---

## Internationalisation

`i18n.tsx` is a self-contained bilingual dictionary (English and Simplified
Chinese) exposed through `useI18n()`, which returns `{ t, locale, ... }`. There is
no external i18n library and no lazy-loaded locale bundles.

Every user-facing string goes through `t('some.key')`. Keys are dotted and grouped
by area (`common.loading`, `backtest.*`, `dashboard.*`). When adding UI, add both
translations — a missing key renders the key itself, which is visible but ugly.

The backend is bilingual too: `django-modeltranslation` translates model fields,
and `LANGUAGES` is `en` + `zh-hans`. Frontend `t()` keys and backend translated
fields are independent systems; do not assume a key exists on both sides.

---

## Dashboard filter state

`lib/dashboardCandidateFilters.ts` serialises the dashboard's candidate filters to
and from URL search params, so a filtered view is shareable and survives a reload.

It owns the weekday label mapping (`MON`…`FRI` ↔ ISO 1–5), the mapping from a
top-N metric to its implied horizon (`topNMetricHorizon`), candidate limit and
horizon resolution, the default filter bundle, and the
`buildDashboardFilterBundleFromRunnerConfig` bridge that lets a backtest runner
configuration pre-populate the dashboard.

This is the module to change when adding a dashboard filter — the URL contract
lives here, not in the page component.

---

## Testing

Vitest with `jsdom`, setup file `src/test/setup.ts`, `@testing-library/react` plus
`@testing-library/user-event` and `@testing-library/jest-dom`.

| File | Focus |
| --- | --- |
| `pages/BacktestWorkbenchPage.test.tsx` | Runner form behaviour, parameter submission, horizon/metric coupling |
| `pages/DashboardPage.test.tsx` | Filter application, candidate rendering |
| `lib/api.test.ts` | Auth storage and transport helpers |
| `pages/IndicatorBoardPage.test.tsx` | Board rendering |

Tests colocate with their subject as `<Name>.test.tsx`. They mock `lib/api.ts`
rather than the network, so no backend is needed.

Run the whole suite with `npm test`. There is no watch-mode script and no coverage
configuration.

---

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `VITE_API_BASE_URL` | `/api/v1` | API base; leave relative so the proxy handles it |
| `VITE_ALERTS_WS_URL` | derived | Absolute WebSocket URL; only needed when the socket is not proxied |

Vite exposes only variables prefixed `VITE_`, and they are inlined at build time —
a change requires a rebuild, not just a restart. Set them in `frontend/.env.local`
(gitignored) rather than editing `vite.config.ts`.

---

## Related documentation

| Topic | Location |
| --- | --- |
| API contracts, auth, pagination, throttling, WebSocket protocol | [`../docs/reference/api.md`](../docs/reference/api.md) |
| Backend setup and services | [`../docs/how-to/local-setup.md`](../docs/how-to/local-setup.md) |
| Backtest parameter semantics | [`../TECHNICAL_GUIDE.md`](../TECHNICAL_GUIDE.md) §7 |
| Model registry facts | [`../docs/reference/models.md`](../docs/reference/models.md) |
