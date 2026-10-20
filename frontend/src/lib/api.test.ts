import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiRequestError, apiDelete, apiGet, fetchOhlcvByAsset } from './api'

vi.stubGlobal('fetch', vi.fn())

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

describe('api transport', () => {
  beforeEach(() => {
    vi.mocked(fetch).mockReset()
    localStorage.setItem('finance_locale', 'en-US')
  })

  it('apiGet surfaces an ApiRequestError carrying status and detail on a 4xx', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(404, { detail: 'Not found.' }))

    let error: ApiRequestError | undefined
    try {
      await apiGet('/missing/')
    } catch (err) {
      error = err as ApiRequestError
    }

    expect(error).toBeInstanceOf(ApiRequestError)
    expect(error?.status).toBe(404)
    expect(error?.detail).toBe('Not found.')  })

  it('apiDelete resolves to null for a 204 response', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(emptyResponse(204))

    await expect(apiDelete<{ id: number }>('/backtest/7/')).resolves.toBeNull()
  })

  it('sends no credential headers and leaves fetch credentials at the default', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(200, { result: 'ok' }))

    await apiGet<{ result: string }>('/probe/')

    const [url, init] = vi.mocked(fetch).mock.calls[0]!
    expect(url).toBe('/api/v1/probe/')
    expect(init?.credentials).toBeUndefined()
    const headers = new Headers(init?.headers)
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(headers.has('Authorization')).toBe(false)
    expect(headers.has('X-API-Key')).toBe(false)
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
