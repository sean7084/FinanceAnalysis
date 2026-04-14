const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1'
const AUTH_MODE_KEY = 'finance_auth_persistence'
const JWT_KEY = 'finance_jwt'
const API_KEY = 'finance_api_key'

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
  }>
}

export interface SentimentDto {
  date: string
  sentiment_score: number
}

export interface BacktestRunDto {
  id: number
  strategy_type: string
  status: string
  total_return: number | null
  sharpe_ratio: number | null
  created_at: string
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
  return sessionStorage.getItem(JWT_KEY) ?? localStorage.getItem(JWT_KEY) ?? ''
}

export function getAuthTokenForSocket(): string {
  return readAuthToken()
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

export function saveAuthSettings(token: string, apiKey: string, mode: AuthPersistenceMode): void {
  localStorage.removeItem(JWT_KEY)
  localStorage.removeItem(API_KEY)
  sessionStorage.removeItem(JWT_KEY)
  sessionStorage.removeItem(API_KEY)

  if (mode === 'local') {
    if (token.trim()) localStorage.setItem(JWT_KEY, token.trim())
    if (apiKey.trim()) localStorage.setItem(API_KEY, apiKey.trim())
  } else if (mode === 'session') {
    if (token.trim()) sessionStorage.setItem(JWT_KEY, token.trim())
    if (apiKey.trim()) sessionStorage.setItem(API_KEY, apiKey.trim())
  }

  localStorage.setItem(AUTH_MODE_KEY, mode)
}

export function clearAuthSettings(): void {
  localStorage.removeItem(JWT_KEY)
  localStorage.removeItem(API_KEY)
  sessionStorage.removeItem(JWT_KEY)
  sessionStorage.removeItem(API_KEY)
}

export function hasAnyAuthCredential(): boolean {
  return Boolean(readAuthToken() || readApiKey())
}

export async function obtainJwtToken(username: string, password: string): Promise<string> {
  const response = await fetch(`${API_BASE}/auth/token/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ username, password }),
  })

  if (!response.ok) {
    throw await toApiRequestError(response, 'AUTH token failed')
  }

  const data = (await response.json()) as { access?: string }
  if (!data.access) {
    throw new Error('No access token returned')
  }
  return data.access
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
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'GET',
    headers: getHeaders(),
  })

  if (!response.ok) {
    throw await toApiRequestError(response, `GET ${path} failed`)
  }

  return response.json() as Promise<T>
}

export async function apiPost<T>(path: string, body: object): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify(body),
  })

  if (!response.ok) {
    throw await toApiRequestError(response, `POST ${path} failed`)
  }

  return response.json() as Promise<T>
}

async function apiGetSafe<T>(path: string, fallback: T): Promise<T> {
  try {
    return await apiGet<T>(path)
  } catch {
    return fallback
  }
}

export async function fetchDashboardData() {
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

export async function fetchScreenerRows(topN = 20): Promise<CandidateDto[]> {
  const payload = await apiGet<Paginated<CandidateDto> | { count: number; results: CandidateDto[] }>(
    `/screener/bottom-candidates/?top_n=${topN}`,
  )
  if (isPaginated<CandidateDto>(payload)) {
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
  const rows: OhlcvDto[] = []
  let nextUrl: string | null = `${API_BASE}/ohlcv/?asset=${assetId}&ordering=-date`

  while (nextUrl && rows.length < limit) {
    const response = await fetch(nextUrl, { headers: getHeaders() })
    if (!response.ok) {
      throw await toApiRequestError(response, 'API request failed')
    }

    const payload = (await response.json()) as Paginated<OhlcvDto>
    rows.push(...payload.results)
    nextUrl = payload.next
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
