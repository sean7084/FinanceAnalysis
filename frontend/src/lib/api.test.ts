import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  apiGet,
  clearAuthSettings,
  fetchOhlcvByAsset,
  getSocketAuthToken,
  readApiKey,
  readAuthToken,
  readRefreshToken,
  saveAuthSettings,
} from './api'

vi.stubGlobal('fetch', vi.fn())

function jsonResponse(status: number, payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      'Content-Type': 'application/json',
    },
  })
}

function deferredResponse() {
  let resolve!: (value: Response) => void
  const promise = new Promise<Response>((innerResolve) => {
    resolve = innerResolve
  })
  return { promise, resolve }
}

describe('api auth refresh handling', () => {
  beforeEach(() => {
    vi.mocked(fetch).mockReset()
    clearAuthSettings()
    localStorage.setItem('finance_locale', 'en-US')
  })

  afterEach(() => {
    clearAuthSettings()
  })

  it('persists rotated refresh tokens after a successful refresh', async () => {
    saveAuthSettings('expired-access', '', 'local', 'old-refresh-token')

    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(401, { detail: 'token_not_valid' }))
      .mockResolvedValueOnce(jsonResponse(200, { access: 'fresh-access-token', refresh: 'fresh-refresh-token' }))
      .mockResolvedValueOnce(jsonResponse(200, { result: 'ok' }))

    await expect(apiGet<{ result: string }>('/probe/')).resolves.toEqual({ result: 'ok' })

    expect(readAuthToken()).toBe('fresh-access-token')
    expect(readRefreshToken()).toBe('fresh-refresh-token')
    expect(vi.mocked(fetch).mock.calls[1]?.[0]).toBe('/api/v1/auth/token/refresh/')
  })

  it('coalesces concurrent refresh attempts behind a single refresh request', async () => {
    saveAuthSettings('expired-access', '', 'local', 'shared-refresh-token')
    const refreshResponse = deferredResponse()
    let refreshCallCount = 0

    vi.mocked(fetch).mockImplementation((input, init) => {
      const url = String(input)
      if (url.endsWith('/auth/token/refresh/')) {
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

    refreshResponse.resolve(jsonResponse(200, { access: 'fresh-access-token', refresh: 'fresh-refresh-token' }))

    await expect(Promise.all([firstRequest, secondRequest])).resolves.toEqual([
      { url: '/api/v1/first/' },
      { url: '/api/v1/second/' },
    ])
    expect(refreshCallCount).toBe(1)
    expect(readRefreshToken()).toBe('fresh-refresh-token')
  })

  it('clears dead JWT tokens but preserves API keys after refresh is rejected', async () => {
    saveAuthSettings('', 'persisted-api-key', 'local', 'dead-refresh-token')
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(401, { detail: 'token_not_valid' }))

    await expect(getSocketAuthToken()).resolves.toBe('')

    expect(readAuthToken()).toBe('')
    expect(readRefreshToken()).toBe('')
    expect(readApiKey()).toBe('persisted-api-key')
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
})