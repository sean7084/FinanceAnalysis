import type { DashboardCandidateFilters } from './dashboardCandidateFilters'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'
const AUTH_MODE_KEY = 'finance_auth_persistence'
const API_KEY = 'finance_api_key'
// Legacy storage keys from the pre-cookie auth flow. Nothing reads or writes
// them any more; bootstrapSession() wipes them so a stale token from an older
// build cannot shadow the new flow.
const LEGACY_JWT_KEY = 'finance_jwt'
const LEGACY_JWT_REFRESH_KEY = 'finance_jwt_refresh'

// In-memory access-token cache. The refresh token lives in an HttpOnly cookie
// set by /api/v1/auth/login/ and rotated by /api/v1/auth/refresh/, so JS never
// sees it. `sessionActive` mirrors "the browser is believed to hold a valid
// refresh cookie" -- true after a successful login/refresh, false after a
// rejected refresh or explicit logout.
let accessToken: string | null = null
let sessionActive = false
let sessionKnown = false
let proactiveRefreshTimer: number | null = null

// Cross-tab coordination: the first tab to observe a 401 (or fire the proactive
// timer) claims the refresh mutex by broadcasting `refresh-start`; other tabs
// park on that broadcast instead of racing. With BLACKLIST_AFTER_ROTATION on,
// a lost race would otherwise wipe both tabs' session. BroadcastChannel is
// available in every browser we target (Chrome 54+, Firefox 38+, Safari 15.4+);
// when missing we degrade to per-tab single-flight only.
const AUTH_CHANNEL_NAME = 'fa-auth'
const TAB_ID =
  typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `tab-${Math.random().toString(36).slice(2)}-${Date.now()}`
type AuthBroadcast =
  | { type: 'refresh-start'; tabId: string }
  | { type: 'refresh-done'; tabId: string; access: string }
  | { type: 'refresh-failed'; tabId: string }
  | { type: 'signed-in'; tabId: string; access: string }
  | { type: 'signed-out'; tabId: string; reason?: string }
let authChannel: BroadcastChannel | null = null
let pendingCrossTabRefresh: Promise<string | null> | null = null
let pendingCrossTabResolvers: Array<(value: string | null) => void> = []

let jwtRefreshPromise: Promise<string | null> | null = null

export class ApiRequestError extends Error {
  status: number
  detail?: string

  constructor(message: string, status: number, detail?: string) {
    super(message)
    this.name = 'ApiRequestError'
    this.status = status
    this.detail = detail
  }
}

export type AuthPersistenceMode = 'local' | 'session' | 'none'

export interface Paginated<T> {
  count: number
  next: string | null
  previous: string | null
  results: T[]
}

export interface MacroContextDto {
  id: number
  macro_phase: string
  event_tag: string
  starts_at: string
  is_active: boolean
}

export interface ConceptHeatDto {
  concept_name: string
  heat_score: number
}

export interface SignalEventDto {
  id: number
  signal_type: string
}

export interface AlertEventDto {
  id: number
  status: string
}

export interface CandidateDto {
  id: number
  asset_symbol: string
  asset_name: string
  composite_score: number
  bottom_probability_score: number
  prediction_horizon?: number
  target_price?: number | null
  stop_loss_price?: number | null
  risk_reward_ratio?: number | null
  trade_score?: number | null
  suggested?: boolean
}

export interface DashboardStockRowDto {
  asset_id: number
  asset_symbol: string
  asset_name: string
  date: string
  candidate_rank?: number | null
  candidate_rank_value?: number | null
  composite_score: number
  bottom_probability_score: number
  fundamental_score: number
  capital_flow_score: number
  technical_score: number
  factor_sentiment_score: number | null
  pe_percentile_score: number | null
  pb_percentile_score: number | null
  roe_trend_score: number | null
  main_force_flow_score: number | null
  margin_flow_score: number | null
  technical_reversal_score: number | null
  current_close: number | null
  sentiment_score: number | null
  sentiment_label: string
  rsi: number | null
  macd: number | null
  bb_upper: number | null
  bb_lower: number | null
  sma_60: number | null
  heuristic_label: string
  heuristic_up_probability: number | null
  heuristic_confidence: number | null
  heuristic_trade_score: number | null
  heuristic_target_price: number | null
  heuristic_stop_loss_price: number | null
  heuristic_risk_reward_ratio: number | null
  heuristic_suggested: boolean
  lightgbm_label: string
  lightgbm_up_probability: number | null
  lightgbm_confidence: number | null
  lightgbm_trade_score: number | null
  lightgbm_target_price: number | null
  lightgbm_stop_loss_price: number | null
  lightgbm_risk_reward_ratio: number | null
  lightgbm_suggested: boolean
  lstm_label: string
  lstm_up_probability: number | null
  lstm_confidence: number | null
  lstm_trade_score: number | null
  lstm_target_price: number | null
  lstm_stop_loss_price: number | null
  lstm_risk_reward_ratio: number | null
  lstm_suggested: boolean
}

export interface DashboardStocksQuery {
  predictionHorizon?: number
  pageSize?: number
  candidateFilters?: DashboardCandidateFilters
}

export interface AssetDto {
  id: number
  symbol: string
  name: string
}

export interface OhlcvDto {
  date: string
  open: string
  high: string
  low: string
  close: string
}

export interface PredictionStockDto {
  stock_code: string
  date: string
  results: Array<{
    horizon_days: number
    up: number
    flat: number
    down: number
    confidence: number
    predicted_label: string
    target_price?: number | string | null
    stop_loss_price?: number | string | null
    risk_reward_ratio?: number | string | null
    trade_score?: number | string | null
    suggested?: boolean
  }>
}

export interface LightGBMPredictionStockDto {
  stock_code: string
  date: string
  results: Array<{
    horizon_days: number
    up: number
    flat: number
    down: number
    confidence: number
    predicted_label: string
    model_version?: string
    target_price?: number | string | null
    stop_loss_price?: number | string | null
    risk_reward_ratio?: number | string | null
    trade_score?: number | string | null
    suggested?: boolean
  }>
}

export interface SentimentDto {
  date: string
  sentiment_score: number
}

export interface ModelVersionDto {
  id: number
  model_type: string
  version: string
  status: string
  artifact_path: string
  metrics: Record<string, number | string>
  feature_schema: string[]
  training_window_start: string | null
  training_window_end: string | null
  trained_at: string | null
  is_active: boolean
  metadata: Record<string, unknown>
}

export interface LightGBMModelArtifactDto {
  id: number
  horizon_days: number
  version: string
  status: string
  artifact_path: string
  metrics_json: Record<string, number | string>
  feature_names: string[]
  training_window_start: string | null
  training_window_end: string | null
  trained_at: string | null
  is_active: boolean
  feature_importance: Record<string, number>
  metadata: Record<string, unknown>
}

export interface LightGBMPredictionDto {
  id: number
  asset: number
  asset_symbol: string
  asset_name: string
  date: string
  candidate_rank?: number | null
  candidate_rank_value?: number | null
  horizon_days: number
  up_probability: number
  flat_probability: number
  down_probability: number
  lstm_label: string
  lstm_up_probability: number | null
  lstm_confidence: number | null
  lstm_trade_score: number | null
  lstm_target_price: number | null
  lstm_stop_loss_price: number | null
  lstm_risk_reward_ratio: number | null
  lstm_suggested: boolean
  predicted_label: string
  confidence: number
  model_artifact: number | null
  model_version: string
  model_horizon: number | null
  feature_snapshot: Record<string, number>
  raw_scores: Record<string, number>
  calibrated_scores: Record<string, number>
  metadata: Record<string, unknown>
}

export interface EnsembleWeightSnapshotDto {
  id: number
  date: string
  lightgbm_weight: number
  lstm_weight: number
  heuristic_weight: number
  basis_lookback_days: number
  basis_metrics: Record<string, number>
}

export interface FeatureImportanceTrendSnapshotDto {
  model_artifact: number
  model_version: string
  trained_at: string | null
  importance_score: number
  importance_rank: number
  created_at: string
}

export interface FeatureImportanceTrendDto {
  feature_name: string
  snapshots: FeatureImportanceTrendSnapshotDto[]
}

export interface FeatureImportanceTrendGroupDto {
  horizon_days: number
  feature_trends: FeatureImportanceTrendDto[]
}

export interface FeatureImportanceTrendResponseDto {
  limit_models: number
  top_n: number
  results: FeatureImportanceTrendGroupDto[]
}

export interface BacktestRunProgressDto {
  processed_trading_days?: number | null
  total_trading_days?: number | null
}

export interface BacktestRunReportDto {
  progress?: BacktestRunProgressDto
  [key: string]: unknown
}

export interface BacktestRunDto {
  id: number
  name: string
  strategy_type: string
  status: string
  pending_control_action: string
  task_state: string
  has_stale_task_owner: boolean
  start_date: string
  end_date: string
  initial_capital: number
  final_value: number | null
  total_return: number | null
  annualized_return: number | null
  max_drawdown: number | null
  sharpe_ratio: number | null
  win_rate: number | null
  total_trades: number
  winning_trades: number
  parameters: Record<string, unknown>
  report: BacktestRunReportDto
  error_message: string
  started_at: string | null
  completed_at: string | null
  created_at: string
}

export interface BacktestRunActionResponseDto {
  id: number
  message: string
}

export interface BacktestTradeDto {
  id: number
  asset_symbol: string
  asset_name: string
  trade_date: string
  side: string
  quantity: number | string
  price: number | string
  fee: number | string
  slippage: number | string
  amount: number | string
  pnl: number | string
  signal_payload: Record<string, unknown>
}

export interface BacktestComparisonPointDto {
  date: string
  value: number
  drawdown: number
}

export interface BacktestComparisonSeriesDto {
  key: string
  label: string
  kind: 'backtest' | 'benchmark'
  run_id?: number
  index_code?: string
  prediction_source?: string
  points: BacktestComparisonPointDto[]
  total_return: number | null
  max_drawdown: number | null
}

export interface BacktestComparisonTargetDto {
  id: number | string
  name: string
  status: string
}

export interface BacktestComparisonPayloadDto {
  run: {
    id: number
    name: string
    status: string
    start_date: string
    end_date: string
    initial_capital: number
    prediction_source: string
    compare_backtest_run_id?: number | null
  }
  series: BacktestComparisonSeriesDto[]
  compare_target: BacktestComparisonTargetDto | null
  available_series_keys: string[]
  message: string | null
}

export interface BacktestCreatePayload {
  name: string
  strategy_type: 'PREDICTION_THRESHOLD'
  start_date: string
  end_date: string
  initial_capital: string
  parameters: {
    prediction_source: 'heuristic' | 'lightgbm' | 'lstm'
    top_n: number
    horizon_days: 3 | 7 | 30
    up_threshold: number
    entry_weekdays?: Array<'MON' | 'TUE' | 'WED' | 'THU' | 'FRI'>
    holding_period_days: number
    capital_fraction_per_entry: number
    candidate_mode?: 'top_n' | 'trade_score'
    top_n_metric?: 'trade_score' | 'up_prob_3d' | 'up_prob_7d' | 'up_prob_30d'
    trade_score_scope?: 'independent' | 'combined'
    trade_score_threshold?: number
    max_positions?: number
    use_macro_context?: boolean
    enable_stop_target_exit?: boolean
    compare_backtest_run_id?: number
  }
}

function isPaginated<T>(payload: unknown): payload is Paginated<T> {
  return (
    typeof payload === 'object' &&
    payload !== null &&
    'results' in payload &&
    Array.isArray((payload as Paginated<T>).results)
  )
}

function getHeaders(): HeadersInit {
  const token = readAuthToken()
  const apiKey = readApiKey()

  const headers: HeadersInit = {
    'Content-Type': 'application/json',
  }

  if (token) {
    headers.Authorization = `Bearer ${token}`
  }
  if (apiKey) {
    headers['X-API-Key'] = apiKey
  }

  return headers
}

function extractErrorDetail(payload: unknown): string | undefined {
  if (!payload || typeof payload !== 'object') return undefined

  const record = payload as Record<string, unknown>
  const candidates = [record.detail, record.message, record.error, record.non_field_errors]

  for (const item of candidates) {
    if (typeof item === 'string' && item.trim()) {
      return item.trim()
    }
    if (Array.isArray(item) && item.length > 0) {
      const first = item[0]
      if (typeof first === 'string' && first.trim()) {
        return first.trim()
      }
    }
  }

  return undefined
}

async function toApiRequestError(response: Response, prefix: string): Promise<ApiRequestError> {
  let detail: string | undefined
  try {
    const payload = (await response.json()) as unknown
    detail = extractErrorDetail(payload)
  } catch {
    detail = undefined
  }

  return new ApiRequestError(`${prefix}: ${response.status}`, response.status, detail)
}

export function readAuthToken(): string {
  return accessToken ?? ''
}

export function getAuthTokenForSocket(): string {
  return readAuthToken()
}

export async function getSocketAuthToken(): Promise<string> {
  const current = readAuthToken()
  if (current) {
    return current
  }
  const refreshed = await refreshJwtAccessToken()
  return refreshed ?? ''
}

export function readApiKey(): string {
  return sessionStorage.getItem(API_KEY) ?? localStorage.getItem(API_KEY) ?? ''
}

export function readAuthPersistenceMode(): AuthPersistenceMode {
  const val = localStorage.getItem(AUTH_MODE_KEY)
  if (val === 'session' || val === 'none' || val === 'local') {
    return val
  }
  return 'local'
}

/**
 * Persist the API key and the persistence-mode preference. The JWT access
 * token is held in memory only and the refresh token in an HttpOnly cookie,
 * so neither is written to storage any more.
 *
 * The leading `_token` parameter is accepted (and ignored) so existing
 * three-argument call sites keep compiling unchanged.
 *
 * @deprecated JWT persistence is now cookie-driven; this function only manages
 * the API key. Retained for call-site compatibility.
 */
export function saveAuthSettings(
  _token: string,
  apiKey: string,
  mode: AuthPersistenceMode,
): void {
  localStorage.removeItem(API_KEY)
  sessionStorage.removeItem(API_KEY)

  if (mode === 'local' && apiKey.trim()) {
    localStorage.setItem(API_KEY, apiKey.trim())
  } else if (mode === 'session' && apiKey.trim()) {
    sessionStorage.setItem(API_KEY, apiKey.trim())
  }

  localStorage.setItem(AUTH_MODE_KEY, mode)
}

export function clearAuthSettings(): void {
  localStorage.removeItem(API_KEY)
  sessionStorage.removeItem(API_KEY)
  accessToken = null
  sessionActive = false
  cancelProactiveRefresh()
}

/** True once bootstrapSession() has resolved at least once. */
export function isSessionKnown(): boolean {
  return sessionKnown
}

/**
 * True when the client holds an access token, a believed-valid refresh cookie,
 * or an API key. Pages use this to choose between "please sign in" copy and a
 * generic load-error message.
 */
export function hasAnyAuthCredential(): boolean {
  return Boolean(accessToken || sessionActive || readApiKey())
}

function cancelProactiveRefresh(): void {
  if (proactiveRefreshTimer !== null) {
    window.clearTimeout(proactiveRefreshTimer)
    proactiveRefreshTimer = null
  }
}

/**
 * Decode the `exp` claim from a JWT without pulling in a library. Returns null
 * on malformed input so callers can treat an undecodable token as "refresh now".
 */
function decodeJwtExp(token: string): number | null {
  const parts = token.split('.')
  if (parts.length < 2) return null
  try {
    const base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=')
    const binary = atob(padded)
    const bytes = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i += 1) {
      bytes[i] = binary.charCodeAt(i)
    }
    const payload = JSON.parse(new TextDecoder().decode(bytes)) as { exp?: number }
    return typeof payload.exp === 'number' ? payload.exp : null
  } catch {
    return null
  }
}

function scheduleProactiveRefresh(): void {
  cancelProactiveRefresh()
  if (!accessToken) return
  const exp = decodeJwtExp(accessToken)
  if (exp === null) return
  // Fire 2-2.5 minutes before expiry. The random jitter desynchronises tabs so
  // the first one to fire claims the BroadcastChannel mutex and the others park
  // on its result instead of racing a second refresh against the blacklist.
  const jitter = Math.floor(Math.random() * 30_000)
  const refreshAt = exp * 1000 - 120_000 - jitter
  const delay = Math.max(0, refreshAt - Date.now())
  proactiveRefreshTimer = window.setTimeout(() => {
    proactiveRefreshTimer = null
    void refreshJwtAccessToken()
  }, delay)
}

function broadcast(message: AuthBroadcast): void {
  if (!authChannel) return
  try {
    authChannel.postMessage(message)
  } catch {
    // Channel closed or serialization failed -- safe to ignore.
  }
}

function resolvePendingCrossTabRefresh(value: string | null): void {
  const resolvers = pendingCrossTabResolvers
  pendingCrossTabRefresh = null
  pendingCrossTabResolvers = []
  for (const resolve of resolvers) {
    resolve(value)
  }
}

function initAuthChannel(): void {
  if (authChannel !== null) return
  if (typeof BroadcastChannel === 'undefined') return
  try {
    authChannel = new BroadcastChannel(AUTH_CHANNEL_NAME)
  } catch {
    authChannel = null
    return
  }
  authChannel.onmessage = (event: MessageEvent<AuthBroadcast>) => {
    const message = event.data
    if (!message || message.tabId === TAB_ID) return
    switch (message.type) {
      case 'refresh-start':
        // Another tab is refreshing; park our own attempts on its result.
        if (!pendingCrossTabRefresh) {
          pendingCrossTabRefresh = new Promise<string | null>((resolve) => {
            pendingCrossTabResolvers.push(resolve)
          })
        }
        break
      case 'refresh-done':
        accessToken = message.access
        sessionActive = true
        scheduleProactiveRefresh()
        resolvePendingCrossTabRefresh(message.access)
        break
      case 'refresh-failed':
        resolvePendingCrossTabRefresh(null)
        break
      case 'signed-out':
        accessToken = null
        sessionActive = false
        cancelProactiveRefresh()
        resolvePendingCrossTabRefresh(null)
        break
      case 'signed-in':
        accessToken = message.access
        sessionActive = true
        scheduleProactiveRefresh()
        break
    }
  }
}

async function refreshJwtAccessToken(): Promise<string | null> {
  initAuthChannel()
  // If another tab is already refreshing, wait for its broadcast instead of
  // firing a second refresh that would lose the rotation race.
  if (pendingCrossTabRefresh) {
    return pendingCrossTabRefresh
  }
  if (jwtRefreshPromise) {
    return jwtRefreshPromise
  }

  broadcast({ type: 'refresh-start', tabId: TAB_ID })

  jwtRefreshPromise = (async () => {
    let response: Response
    try {
      response = await fetch(`${API_BASE}/auth/refresh/`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
      })
    } catch {
      broadcast({ type: 'refresh-failed', tabId: TAB_ID })
      resolvePendingCrossTabRefresh(null)
      return null
    }

    if (!response.ok) {
      if (response.status === 401 || response.status === 403) {
        accessToken = null
        sessionActive = false
        cancelProactiveRefresh()
        broadcast({ type: 'signed-out', tabId: TAB_ID, reason: 'refresh_rejected' })
      } else {
        broadcast({ type: 'refresh-failed', tabId: TAB_ID })
      }
      resolvePendingCrossTabRefresh(null)
      return null
    }

    const payload = (await response.json()) as { access?: string }
    const access = payload.access?.trim()
    if (!access) {
      broadcast({ type: 'refresh-failed', tabId: TAB_ID })
      resolvePendingCrossTabRefresh(null)
      return null
    }

    accessToken = access
    sessionActive = true
    scheduleProactiveRefresh()
    broadcast({ type: 'refresh-done', tabId: TAB_ID, access })
    resolvePendingCrossTabRefresh(access)
    return access
  })().finally(() => {
    jwtRefreshPromise = null
  })

  return jwtRefreshPromise
}

async function fetchWithAuthRefresh(url: string, init: RequestInit): Promise<Response> {
  const withCredentials: RequestInit = {
    ...init,
    credentials: init.credentials ?? 'include',
  }
  let response = await fetch(url, withCredentials)
  if (response.status !== 401) return response

  const nextAccess = await refreshJwtAccessToken()
  if (!nextAccess) return response

  const retryHeaders = new Headers(withCredentials.headers as HeadersInit | undefined)
  retryHeaders.set('Authorization', `Bearer ${nextAccess}`)
  response = await fetch(url, {
    ...withCredentials,
    headers: retryHeaders,
  })
  return response
}

/**
 * Exchange username/password for an access token. The refresh token is set by
 * the backend as an HttpOnly cookie and is never visible to this code.
 */
export async function loginWithCredentials(username: string, password: string): Promise<string> {
  initAuthChannel()
  const response = await fetch(`${API_BASE}/auth/login/`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })

  if (!response.ok) {
    throw await toApiRequestError(response, 'AUTH login failed')
  }

  const data = (await response.json()) as { access?: string }
  if (!data.access) {
    throw new Error('No access token returned')
  }
  accessToken = data.access
  sessionActive = true
  sessionKnown = true
  scheduleProactiveRefresh()
  broadcast({ type: 'signed-in', tabId: TAB_ID, access: data.access })
  return data.access
}

/**
 * Blacklist the current refresh token server-side and clear the cookie. The
 * network call is best-effort: even if it fails we drop local state so the UI
 * reflects a signed-out session immediately.
 */
export async function logout(): Promise<void> {
  initAuthChannel()
  try {
    await fetch(`${API_BASE}/auth/logout/`, {
      method: 'POST',
      credentials: 'include',
    })
  } catch {
    // Ignore network failures during logout; local state is cleared below.
  }
  accessToken = null
  sessionActive = false
  cancelProactiveRefresh()
  broadcast({ type: 'signed-out', tabId: TAB_ID, reason: 'explicit_logout' })
}

/**
 * Called once at SPA boot. Probes /auth/session/ to learn whether the browser
 * holds a valid refresh cookie; if so, immediately exchanges it for an access
 * token so the first authenticated API call does not pay a 401 round-trip.
 * Also wipes legacy pre-cookie tokens from storage.
 */
export async function bootstrapSession(): Promise<boolean> {
  initAuthChannel()
  localStorage.removeItem(LEGACY_JWT_KEY)
  localStorage.removeItem(LEGACY_JWT_REFRESH_KEY)
  sessionStorage.removeItem(LEGACY_JWT_KEY)
  sessionStorage.removeItem(LEGACY_JWT_REFRESH_KEY)

  let authenticated = false
  try {
    const response = await fetch(`${API_BASE}/auth/session/`, {
      method: 'GET',
      credentials: 'include',
    })
    if (response.ok) {
      const payload = (await response.json()) as { authenticated?: boolean }
      authenticated = Boolean(payload.authenticated)
    }
  } catch {
    authenticated = false
  }

  if (authenticated) {
    sessionActive = true
    const access = await refreshJwtAccessToken()
    if (!access) {
      sessionActive = false
    }
  } else {
    accessToken = null
    sessionActive = false
  }
  sessionKnown = true
  return Boolean(accessToken)
}

// Proactively refresh when the tab becomes visible and the access token is
// within five minutes of expiry. Cheap enough to leave registered forever.
if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') return
    if (!accessToken) {
      if (sessionActive) void refreshJwtAccessToken()
      return
    }
    const exp = decodeJwtExp(accessToken)
    if (exp === null) return
    if (exp * 1000 - Date.now() < 300_000) {
      void refreshJwtAccessToken()
    }
  })
}

export async function createDeveloperApiKey(name = 'frontend-ui', jwtToken?: string): Promise<string> {
  const headers: Record<string, string> = {
    ...(getHeaders() as Record<string, string>),
  }
  if (jwtToken) {
    headers.Authorization = `Bearer ${jwtToken}`
  }

  const response = await fetch(`${API_BASE}/developer/keys/`, {
    method: 'POST',
    credentials: 'include',
    headers,
    body: JSON.stringify({
      name,
      is_sandbox: false,
    }),
  })

  if (!response.ok) {
    throw await toApiRequestError(response, 'POST /developer/keys/ failed')
  }

  const payload = (await response.json()) as { raw_key?: string }
  if (!payload.raw_key) {
    throw new Error('No raw_key returned')
  }
  return payload.raw_key
}

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetchWithAuthRefresh(`${API_BASE}${path}`, {
    method: 'GET',
    headers: getHeaders(),
  })

  if (!response.ok) {
    throw await toApiRequestError(response, `GET ${path} failed`)
  }

  return response.json() as Promise<T>
}

export async function apiPost<T>(path: string, body: object): Promise<T> {
  const response = await fetchWithAuthRefresh(`${API_BASE}${path}`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify(body),
  })

  if (!response.ok) {
    throw await toApiRequestError(response, `POST ${path} failed`)
  }

  return response.json() as Promise<T>
}

export async function apiDelete<T>(path: string): Promise<T | null> {
  const response = await fetchWithAuthRefresh(`${API_BASE}${path}`, {
    method: 'DELETE',
    headers: getHeaders(),
  })

  if (!response.ok) {
    throw await toApiRequestError(response, `DELETE ${path} failed`)
  }

  if (response.status === 204) {
    return null
  }

  const text = await response.text()
  if (!text.trim()) {
    return null
  }

  return JSON.parse(text) as T
}

async function apiGetSafe<T>(path: string, fallback: T): Promise<T> {
  try {
    return await apiGet<T>(path)
  } catch {
    return fallback
  }
}

export interface DashboardSummaryDto {
  macro_phase: string
  hot_concepts: string
  prediction_signals: number
  alert_triggers: number
  completed_backtests: number
  avg_bottom_probability: number
  generated_at: string
}

const DASHBOARD_SUMMARY_FALLBACK: DashboardSummaryDto = {
  macro_phase: 'N/A',
  hot_concepts: 'N/A',
  prediction_signals: 0,
  alert_triggers: 0,
  completed_backtests: 0,
  avg_bottom_probability: 0,
  generated_at: '',
}

/**
 * Single-call replacement for the seven requests `fetchDashboardDataLegacy`
 * fires. Served by `GET /dashboard/stocks/summary/`, which computes the same six
 * values server-side and caches them per user, because the alert count is
 * owner-scoped.
 *
 * Two values may legitimately differ from the legacy result: `alertTriggers` and
 * `completedBacktests` are true counts, where the legacy code tallied within a
 * capped page (200 and 50 rows) and so silently saturated. `completedBacktests`
 * is the visible one -- it reported at most 50 against a real total of 431.
 *
 * Uses `apiGetSafe` rather than `apiGet` to keep the legacy behaviour of
 * degrading to placeholder values instead of failing the whole page.
 */
export async function fetchDashboardData() {
  const summary = await apiGetSafe<DashboardSummaryDto>(
    '/dashboard/stocks/summary/',
    DASHBOARD_SUMMARY_FALLBACK,
  )

  return {
    macroPhase: summary.macro_phase || 'N/A',
    hotConcepts: summary.hot_concepts || 'N/A',
    predictionSignals: summary.prediction_signals ?? 0,
    alertTriggers: summary.alert_triggers ?? 0,
    completedBacktests: summary.completed_backtests ?? 0,
    avgBottomProbability: summary.avg_bottom_probability ?? 0,
  }
}

/** Retained for one release so the switch to the summary endpoint is reversible. */
export async function fetchDashboardDataLegacy() {
  const [macroCurrent, macroList, conceptsTop, recentSignals, recentAlerts, backtests, screener] = await Promise.all([
    apiGetSafe<MacroContextDto | { detail: string }>('/macro/contexts/current/', { detail: 'No active context found.' }),
    apiGetSafe<Paginated<MacroContextDto>>('/macro/contexts/?page_size=1', { count: 0, next: null, previous: null, results: [] }),
    apiGetSafe<{ date?: string; results: ConceptHeatDto[] }>('/sentiment/concepts/top/?limit=3', { results: [] }),
    apiGetSafe<Paginated<SignalEventDto>>('/signals/recent/?days=1&page_size=200', { count: 0, next: null, previous: null, results: [] }),
    apiGetSafe<Paginated<AlertEventDto>>('/alert-events/?page_size=200', { count: 0, next: null, previous: null, results: [] }),
    apiGetSafe<Paginated<BacktestRunDto>>('/backtest/?page_size=50', { count: 0, next: null, previous: null, results: [] }),
    apiGetSafe<Paginated<CandidateDto> | { count: number; results: CandidateDto[] }>('/screener/bottom-candidates/?top_n=20', { count: 0, results: [] }),
  ])

  const macro = 'macro_phase' in macroCurrent ? macroCurrent : macroList.results[0]
  const activeAlerts = recentAlerts.results.filter((x) => x.status !== 'SENT').length
  const completedBacktests = backtests.results.filter((x) => x.status === 'COMPLETED').length

  const screenerRows = isPaginated<CandidateDto>(screener) ? screener.results : screener.results
  const avgBottomProbability = screenerRows.length
    ? screenerRows.reduce((acc, row) => acc + Number(row.bottom_probability_score), 0) / screenerRows.length
    : 0

  return {
    macroPhase: macro?.macro_phase ?? 'N/A',
    hotConcepts: conceptsTop.results.map((x) => x.concept_name).join(', ') || 'N/A',
    predictionSignals: recentSignals.count,
    alertTriggers: activeAlerts,
    completedBacktests,
    avgBottomProbability,
  }
}

export async function fetchDashboardProbabilityData() {
  // Prefer top screener symbol, then fallback to a stable large-cap symbol.
  let symbol = '600519'
  try {
    const screener = await fetchScreenerRows(1)
    if (screener.length > 0 && screener[0].asset_symbol) {
      symbol = screener[0].asset_symbol
    }
  } catch {
    // Keep fallback symbol when screener endpoint is unavailable.
  }

  const prediction = await fetchPredictionBySymbol(symbol)
  if (!prediction?.results?.length) {
    return { symbol, data: [] as Array<{ horizon: string; up: number; flat: number; down: number }> }
  }

  return {
    symbol,
    data: prediction.results.map((x) => ({
      horizon: `${x.horizon_days}D`,
      up: Number(x.up),
      flat: Number(x.flat),
      down: Number(x.down),
    })),
  }
}

export async function fetchScreenerRows(topN = 20, sortBy = 'bottom_probability_score', predictionHorizon = 7): Promise<CandidateDto[]> {
  const payload = await apiGet<Paginated<CandidateDto> | { count: number; results: CandidateDto[] }>(
    `/screener/bottom-candidates/?top_n=${topN}&sort_by=${encodeURIComponent(sortBy)}&prediction_horizon=${predictionHorizon}`,
  )
  if (isPaginated<CandidateDto>(payload)) {
    return payload.results
  }
  return payload.results
}

export async function fetchDashboardStocks(query: DashboardStocksQuery = {}): Promise<DashboardStockRowDto[]> {
  const params = new URLSearchParams()
  params.set('prediction_horizon', String(query.predictionHorizon ?? 7))
  params.set('page_size', String(query.pageSize ?? 120))

  if (query.candidateFilters) {
    params.set('prediction_source', query.candidateFilters.predictionSource)
    params.set('horizon_days', String(query.candidateFilters.horizonDays))
    params.set('candidate_mode', query.candidateFilters.candidateMode)
    params.set('up_threshold', String(query.candidateFilters.upThreshold))
    params.set('top_n', String(query.candidateFilters.topN))
    params.set('top_n_metric', query.candidateFilters.topNMetric)
    params.set('trade_score_scope', query.candidateFilters.tradeScoreScope)
    params.set('trade_score_threshold', String(query.candidateFilters.tradeScoreThreshold))
    params.set('max_positions', String(query.candidateFilters.maxPositions))
    params.set('use_macro_context', query.candidateFilters.useMacroContext ? 'true' : 'false')
  }

  const payload = await apiGet<Paginated<DashboardStockRowDto> | { count: number; results: DashboardStockRowDto[] }>(
    `/dashboard/stocks/?${params.toString()}`,
  )
  if (isPaginated<DashboardStockRowDto>(payload)) {
    return payload.results
  }
  return payload.results
}

export async function fetchMacroContexts(limit = 20): Promise<MacroContextDto[]> {
  const payload = await apiGet<Paginated<MacroContextDto>>(`/macro/contexts/?page_size=${limit}`)
  return payload.results
}

export async function fetchBacktestRuns(limit = 20): Promise<BacktestRunDto[]> {
  const payload = await apiGet<Paginated<BacktestRunDto>>(`/backtest/?page_size=${limit}`)
  return payload.results
}

export async function fetchBacktestTrades(runId: number): Promise<BacktestTradeDto[]> {
  return await apiGet<BacktestTradeDto[]>(`/backtest/${runId}/trades/`)
}

export async function fetchBacktestComparisonCurve(
  runId: number,
  extraCompareRunIds: number[] = [],
): Promise<BacktestComparisonPayloadDto> {
  const params = new URLSearchParams()
  for (const extraRunId of new Set(extraCompareRunIds)) {
    if (Number.isInteger(extraRunId) && extraRunId > 0) {
      params.append('extra_compare_run_id', String(extraRunId))
    }
  }
  const query = params.toString()
  return await apiGet<BacktestComparisonPayloadDto>(`/backtest/${runId}/comparison_curve/${query ? `?${query}` : ''}`)
}

export async function createBacktestRun(payload: BacktestCreatePayload): Promise<BacktestRunDto> {
  return await apiPost<BacktestRunDto>('/backtest/', payload)
}

export async function pauseBacktestRun(runId: number): Promise<BacktestRunActionResponseDto> {
  return await apiPost<BacktestRunActionResponseDto>(`/backtest/${runId}/pause/`, {})
}

export async function resumeBacktestRun(runId: number): Promise<BacktestRunActionResponseDto> {
  return await apiPost<BacktestRunActionResponseDto>(`/backtest/${runId}/resume/`, {})
}

export async function restartBacktestRun(runId: number): Promise<BacktestRunActionResponseDto> {
  return await apiPost<BacktestRunActionResponseDto>(`/backtest/${runId}/restart/`, {})
}

export async function deleteBacktestRun(runId: number): Promise<BacktestRunActionResponseDto | null> {
  return await apiDelete<BacktestRunActionResponseDto>(`/backtest/${runId}/`)
}

export async function fetchAssetBySymbol(symbol: string): Promise<AssetDto | null> {
  const payload = await apiGet<Paginated<AssetDto>>(`/assets/?search=${encodeURIComponent(symbol)}&page_size=20`)
  const exact = payload.results.find((x) => x.symbol === symbol)
  return exact ?? payload.results[0] ?? null
}

export async function fetchAssets(limit = 200, search = ''): Promise<AssetDto[]> {
  const query = search ? `&search=${encodeURIComponent(search)}` : ''
  const payload = await apiGet<Paginated<AssetDto>>(`/assets/?page_size=${limit}${query}`)
  return payload.results
}

export async function fetchOhlcvByAsset(assetId: number, limit = 120): Promise<OhlcvDto[]> {
  const pageSize = Math.min(Math.max(limit, 1), 1000)
  const baseUrl = `${API_BASE}/ohlcv/?asset=${assetId}&ordering=-date&page_size=${pageSize}`

  const getPage = async (url: string): Promise<Paginated<OhlcvDto>> => {
    const response = await fetchWithAuthRefresh(url, { headers: getHeaders() })
    if (!response.ok) {
      throw await toApiRequestError(response, 'API request failed')
    }
    return (await response.json()) as Paginated<OhlcvDto>
  }

  const first = await getPage(baseUrl)
  const rows: OhlcvDto[] = [...first.results]

  // Once `count` is known the remaining pages are independent, so request them
  // together instead of walking `next` one round trip at a time. Promise.all
  // preserves order, and each page is ordered -date, so the result stays
  // newest-first exactly as the serial version produced it.
  const total = typeof first.count === 'number' ? first.count : rows.length
  const wanted = Math.min(total, limit)
  const totalPages = Math.ceil(wanted / pageSize)
  let nextUrl: string | null = first.next ?? null
  if (totalPages > 1) {
    const rest = await Promise.all(
      Array.from({ length: totalPages - 1 }, (_, index) => getPage(`${baseUrl}&page=${index + 2}`)),
    )
    for (const page of rest) {
      rows.push(...page.results)
    }
    nextUrl = rest[rest.length - 1]?.next ?? null
  }

  // `count` and `page_size` normally pin the page count exactly. If a page still
  // came back short, fall back to walking `next` rather than silently returning
  // truncated chart history.
  while (nextUrl && rows.length < wanted) {
    const page = await getPage(nextUrl)
    rows.push(...page.results)
    nextUrl = page.next
  }

  return rows.slice(0, limit)
}

export async function fetchPredictionBySymbol(symbol: string): Promise<PredictionStockDto | null> {
  try {
    return await apiGet<PredictionStockDto>(`/prediction/${encodeURIComponent(symbol)}/`)
  } catch {
    return null
  }
}

export async function fetchSentimentByAsset(assetId: number): Promise<SentimentDto[]> {
  try {
    const payload = await apiGet<Paginated<SentimentDto>>(`/sentiment/?asset=${assetId}&score_type=ASSET_7D&page_size=20`)
    return payload.results
  } catch {
    return []
  }
}

export async function fetchModelVersions(modelType?: string, limit = 30): Promise<ModelVersionDto[]> {
  const query = modelType ? `&model_type=${encodeURIComponent(modelType)}` : ''
  const payload = await apiGet<Paginated<ModelVersionDto>>(`/prediction-model-versions/?page_size=${limit}${query}`)
  return payload.results
}

export async function fetchLightGBMModels(limit = 30, horizonDays?: number): Promise<LightGBMModelArtifactDto[]> {
  const query = horizonDays ? `&horizon_days=${horizonDays}` : ''
  const payload = await apiGet<Paginated<LightGBMModelArtifactDto>>(`/lightgbm-models/?page_size=${limit}${query}`)
  return payload.results
}

export async function fetchLightGBMPredictionBySymbol(symbol: string): Promise<LightGBMPredictionStockDto | null> {
  try {
    return await apiGet<LightGBMPredictionStockDto>(`/lightgbm-predictions/${encodeURIComponent(symbol)}/`)
  } catch {
    return null
  }
}

export async function fetchLightGBMFeatureImportanceTrends(horizonDays?: number, limitModels = 5, topN = 10): Promise<FeatureImportanceTrendResponseDto> {
  const params = new URLSearchParams({
    limit_models: String(limitModels),
    top_n: String(topN),
  })
  if (horizonDays) {
    params.set('horizon_days', String(horizonDays))
  }
  return apiGet<FeatureImportanceTrendResponseDto>(`/lightgbm-models/feature-importance-trends/?${params.toString()}`)
}

export async function fetchEnsembleWeights(limit = 30): Promise<EnsembleWeightSnapshotDto[]> {
  const payload = await apiGet<Paginated<EnsembleWeightSnapshotDto>>(`/ensemble-weights/?page_size=${limit}`)
  return payload.results
}
