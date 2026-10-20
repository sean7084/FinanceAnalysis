import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  apiGet,
  bootstrapSession,
  clearAuthSettings,
  fetchOhlcvByAsset,
  getSocketAuthToken,
  hasAnyAuthCredential,
  loginWithCredentials,
  logout,
  readApiKey,
  readAuthToken,
  saveAuthSettings,
} from './api'
import { cacheSnapshot, seed } from './queryCache'

vi.stubGlobal('fetch', vi.fn())

// Shared BroadcastChannel mock so we can assert cross-tab broadcasts.
// The real channel is created lazily on first auth call and persists across
// tests; postMessage pushes into this shared array which we clear per test.
const broadcastMessages: Array<Record<string, unknown>> = []

class MockBroadcastChannel {
  name: string
  onmessage: ((event: MessageEvent) => void) | null = null
  constructor(name: string) {
    this.name = name
  }
  postMessage(message: unknown) {
    broadcastMessages.push(message as Record<string, unknown>)
  }
  close() {}
  addEventListener() {}
  removeEventListener() {}
  dispatchEvent() { return true }
}

vi.stubGlobal('BroadcastChannel', MockBroadcastChannel)

function jsonResponse(status: number, payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      'Content-Type': 'application/json',
    },
  })
}

function emptyResponse(status: number): Response {
  return new Response(null, { status })
}

function deferredResponse() {
  let resolve!: (value: Response) => void
  const promise = new Promise<Response>((innerResolve) => {
    resolve = innerResolve
  })
  return { promise, resolve }
}

describe('cookie-based auth flow', () => {
  beforeEach(() => {
    vi.mocked(fetch).mockReset()
    broadcastMessages.length = 0
    clearAuthSettings()
    localStorage.setItem('finance_locale', 'en-US')
  })

  afterEach(() => {
    clearAuthSettings()
  })

  it('loginWithCredentials stores access in memory and nothing in web storage', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse(200, { access: 'fresh-access-token' }),
    )

    const access = await loginWithCredentials('admin', 'password')

    expect(access).toBe('fresh-access-token')
    expect(readAuthToken()).toBe('fresh-access-token')
    // The refresh token lives in an HttpOnly cookie; nothing JWT-related is
    // written to localStorage or sessionStorage.
    expect(localStorage.getItem('finance_jwt')).toBeNull()
    expect(sessionStorage.getItem('finance_jwt')).toBeNull()
    expect(localStorage.getItem('finance_jwt_refresh')).toBeNull()
    expect(sessionStorage.getItem('finance_jwt_refresh')).toBeNull()
    // credentials:'include' is required so the browser accepts Set-Cookie.
    const [, init] = vi.mocked(fetch).mock.calls[0]!
    expect(init?.credentials).toBe('include')
  })

  it('retries a 401 through /auth/refresh/ with credentials and the rotated access token', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(200, { access: 'seed-access' }))       // login
      .mockResolvedValueOnce(jsonResponse(401, { detail: 'token_not_valid' }))   // data call
      .mockResolvedValueOnce(jsonResponse(200, { access: 'rotated-access' }))    // refresh
      .mockResolvedValueOnce(jsonResponse(200, { result: 'ok' }))                // retry

    await loginWithCredentials('admin', 'password')
    const result = await apiGet<{ result: string }>('/probe/')

    expect(result).toEqual({ result: 'ok' })
    expect(readAuthToken()).toBe('rotated-access')

    const refreshCall = vi.mocked(fetch).mock.calls[2]!
    expect(refreshCall[0]).toBe('/api/v1/auth/refresh/')
    expect(refreshCall[1]?.method).toBe('POST')
    expect(refreshCall[1]?.credentials).toBe('include')
    // The cookie carries the refresh token; no body is sent.
    expect(refreshCall[1]?.body).toBeUndefined()

    const retryCall = vi.mocked(fetch).mock.calls[3]!
    const retryHeaders = new Headers(retryCall[1]?.headers as HeadersInit)
    expect(retryHeaders.get('Authorization')).toBe('Bearer rotated-access')
  })

  it('coalesces concurrent 401 retries behind a single refresh request', async () => {
    const refreshResponse = deferredResponse()
    let refreshCallCount = 0

    vi.mocked(fetch).mockImplementation((input, init) => {
      const url = String(input)
      if (url.endsWith('/auth/refresh/')) {
        refreshCallCount += 1
        return refreshResponse.promise
      }
      const authorizationHeader = new Headers(init?.headers).get('Authorization')
      if (authorizationHeader === 'Bearer fresh-access-token') {
        return Promise.resolve(jsonResponse(200, { url }))
      }
      return Promise.resolve(jsonResponse(401, { detail: 'token_not_valid' }))
    })

    const firstRequest = apiGet<{ url: string }>('/first/')
    const secondRequest = apiGet<{ url: string }>('/second/')

    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(refreshCallCount).toBe(1)

    refreshResponse.resolve(jsonResponse(200, { access: 'fresh-access-token' }))

    await expect(Promise.all([firstRequest, secondRequest])).resolves.toEqual([
      { url: '/api/v1/first/' },
      { url: '/api/v1/second/' },
    ])
    expect(refreshCallCount).toBe(1)
  })

  it('clears the access token, preserves the API key, and broadcasts signed-out after refresh is rejected', async () => {
    saveAuthSettings('', 'persisted-api-key', 'local')

    // getSocketAuthToken -> readAuthToken() is empty -> refreshJwtAccessToken()
    // fires a single POST to /auth/refresh/ which the server rejects.
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse(401, { detail: 'token_not_valid' }),
    )

    await expect(getSocketAuthToken()).resolves.toBe('')

    expect(readAuthToken()).toBe('')
    expect(readApiKey()).toBe('persisted-api-key')
    expect(hasAnyAuthCredential()).toBe(true) // API key still present
    // The rejection must be broadcast so other tabs drop their session too.
    const signedOut = broadcastMessages.find((m) => m.type === 'signed-out')
    expect(signedOut).toBeDefined()
    expect(signedOut?.reason).toBe('refresh_rejected')
  })

  it('bootstrapSession exchanges a valid cookie for an access token', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(200, { authenticated: true, username: 'admin' }))
      .mockResolvedValueOnce(jsonResponse(200, { access: 'bootstrapped-access' }))

    const signedIn = await bootstrapSession()

    expect(signedIn).toBe(true)
    expect(readAuthToken()).toBe('bootstrapped-access')
    expect(vi.mocked(fetch).mock.calls[0]?.[0]).toBe('/api/v1/auth/session/')
    expect(vi.mocked(fetch).mock.calls[1]?.[0]).toBe('/api/v1/auth/refresh/')
  })

  it('bootstrapSession leaves the client anonymous when no cookie is present', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(200, { authenticated: false }))

    const signedIn = await bootstrapSession()

    expect(signedIn).toBe(false)
    expect(readAuthToken()).toBe('')
    expect(hasAnyAuthCredential()).toBe(false)
    // Only the session probe fires; no refresh attempt.
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1)
  })

  it('logout clears local state and posts to /auth/logout/', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(200, { access: 'seed-access' })) // login
      .mockResolvedValueOnce(emptyResponse(204))                            // logout

    await loginWithCredentials('admin', 'password')
    expect(readAuthToken()).toBe('seed-access')

    await logout()

    expect(readAuthToken()).toBe('')
    expect(hasAnyAuthCredential()).toBe(false)
    const logoutCall = vi.mocked(fetch).mock.calls[1]!
    expect(logoutCall[0]).toBe('/api/v1/auth/logout/')
    expect(logoutCall[1]?.credentials).toBe('include')
  })

  it('drops cached queries on logout, because the summary and alert rows are owner-scoped', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(emptyResponse(204))
    seed('dashboard/summary', { macroPhase: 'RECOVERY' })
    expect(cacheSnapshot('dashboard/summary').hasValue).toBe(true)

    await logout()

    expect(cacheSnapshot('dashboard/summary').hasValue).toBe(false)
  })

  it('requests larger OHLCV pages to avoid excessive stock-detail request fan-out', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(200, {
        count: 3,
        next: '/api/v1/ohlcv/?asset=7&ordering=-date&page=2&page_size=3',
        previous: null,
        results: [
          { date: '2024-01-03', open: '3', high: '4', low: '2', close: '3.5', volume: '100' },
          { date: '2024-01-02', open: '2', high: '3', low: '1', close: '2.5', volume: '100' },
        ],
      }))
      .mockResolvedValueOnce(jsonResponse(200, {
        count: 3,
        next: null,
        previous: '/api/v1/ohlcv/?asset=7&ordering=-date&page_size=3',
        results: [
          { date: '2024-01-01', open: '1', high: '2', low: '0.5', close: '1.5', volume: '100' },
        ],
      }))

    const rows = await fetchOhlcvByAsset(7, 3)

    expect(vi.mocked(fetch).mock.calls[0]?.[0]).toBe('/api/v1/ohlcv/?asset=7&ordering=-date&page_size=3')
    expect(rows).toHaveLength(3)
  })

  it('fetches OHLCV pages concurrently and preserves order and truncation', async () => {
    const rowsFor = (prefix: string, count: number) =>
      Array.from({ length: count }, (_, index) => ({
        date: `${prefix}-${index}`, open: '1', high: '2', low: '0.5', close: '1.5', volume: '100',
      }))

    let inFlight = 0
    let peakInFlight = 0
    let release!: () => void
    const gate = new Promise<void>((resolve) => { release = resolve })

    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input)
      if (!url.includes('page=')) {
        return jsonResponse(200, {
          count: 2500,
          next: '/api/v1/ohlcv/?asset=7&ordering=-date&page=2&page_size=1000',
          previous: null,
          results: rowsFor('p1', 1000),
        })
      }
      inFlight += 1
      peakInFlight = Math.max(peakInFlight, inFlight)
      await gate
      inFlight -= 1
      const isPage3 = url.includes('page=3')
      return jsonResponse(200, {
        count: 2500,
        next: isPage3 ? null : '/api/v1/ohlcv/?asset=7&ordering=-date&page=3&page_size=1000',
        previous: null,
        results: rowsFor(isPage3 ? 'p3' : 'p2', isPage3 ? 500 : 1000),
      })
    })

    const pending = fetchOhlcvByAsset(7, 3000)
    // Both follow-up pages must be in flight at once; the serial version could
    // never reach two, because it only learned of page 3 after page 2 resolved.
    await vi.waitFor(() => expect(peakInFlight).toBe(2))
    release()
    const rows = await pending

    expect(vi.mocked(fetch).mock.calls).toHaveLength(3)
    expect(rows).toHaveLength(2500)
    expect(rows[0]?.date).toBe('p1-0')
    expect(rows[1000]?.date).toBe('p2-0')
    expect(rows[2000]?.date).toBe('p3-0')
  })
})
