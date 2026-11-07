import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiRequestError } from '../lib/api'
import { resetQueryCache } from '../lib/queryCache'
import { trackViewResource, useApiResource } from './useApiResource'

/**
 * Guards on the time-to-data instrument.
 *
 * `data-tti-open` and `data-tti` on `<body>` are what
 * scripts/measure_page_api_latency.sh derives FRONTEND_OVERHEAD_SECONDS from, so
 * the harness's browser term is only as trustworthy as these attributes. Nothing
 * renders from them, which is exactly why they need tests: an unobserved
 * diagnostic silently stops firing.
 */
function Resource({ name, load }: { name: string; load: () => Promise<unknown> }) {
  const { data, loading } = useApiResource<unknown>(name, load)
  return <span data-testid={name}>{loading ? 'pending' : String(data)}</span>
}

describe('useApiResource time-to-data instrument', () => {
  beforeEach(() => {
    resetQueryCache()
    document.body.removeAttribute('data-tti')
    document.body.removeAttribute('data-tti-open')
    document.body.removeAttribute('data-tti-path')
    // The view is module state keyed on the pathname, so each test navigates to a
    // distinct route instead of inheriting the previous test's stamped view.
    window.history.pushState({}, '', '/guard')
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('stamps once every mounted resource has a first value, not before', async () => {
    let releaseSlow: (value: unknown) => void = () => undefined
    window.history.pushState({}, '', '/two-resources')

    render(
      <>
        <Resource name="fast" load={async () => 'fast-value'} />
        <Resource name="slow" load={() => new Promise((resolve) => { releaseSlow = resolve })} />
      </>,
    )

    await waitFor(() => expect(get('fast')).toBe('fast-value'))
    expect(document.body.dataset.tti).toBeUndefined()

    releaseSlow('slow-value')
    await waitFor(() => expect(get('slow')).toBe('slow-value'))

    expect(document.body.dataset.ttiPath).toBe('/two-resources')
    expect(Number(document.body.dataset.tti)).toBeGreaterThanOrEqual(0)
  })

  it('releases the stamp when a resource fails instead of waiting forever', async () => {
    window.history.pushState({}, '', '/failing')
    render(<Resource name="broken" load={() => Promise.reject(new Error('boom'))} />)

    await waitFor(() => expect(document.body.dataset.ttiPath).toBe('/failing'))
    expect(Number(document.body.dataset.tti)).toBeGreaterThanOrEqual(0)
  })

  it('keeps data-tti when the engine has no usable UserTiming measure', async () => {
    // jsdom ships no performance.mark/measure at all, and some engines throw on the
    // options form. The record is the attribute; the mark is a convenience, so a
    // missing measure may not erase the number the harness reads.
    const throwing = vi.fn(() => {
      throw new TypeError('measure is not supported')
    })
    vi.stubGlobal('performance', Object.create(performance, {
      now: { value: () => 1234.5 },
      measure: { value: throwing, configurable: true },
      mark: { value: vi.fn(), configurable: true },
    }))
    window.history.pushState({}, '', '/no-user-timing')

    render(<Resource name="alone" load={async () => 1} />)

    await waitFor(() => expect(document.body.dataset.ttiPath).toBe('/no-user-timing'))
    expect(throwing).toHaveBeenCalled()
  })

  it('reopens the view for a resource mounted after the first quiet moment', async () => {
    // Chained pages ask for the second thing only once the first has landed, so a
    // stamp that latches at the first quiet moment reports data the user is still
    // waiting for as already arrived. That biases the whole budget toward a pass.
    window.history.pushState({}, '', '/chained')
    const wait = () => new Promise((resolve) => setTimeout(() => resolve('late-value'), 80))

    const rendered = render(<Resource name="first" load={async () => 'first-value'} />)
    await waitFor(() => expect(document.body.dataset.ttiPath).toBe('/chained'))
    const firstStamp = Number(document.body.dataset.tti)

    rendered.rerender(
      <>
        <Resource name="first" load={async () => 'first-value'} />
        <Resource name="late" load={wait} />
      </>,
    )

    await waitFor(() => expect(Number(document.body.dataset.tti)).toBeGreaterThan(firstStamp))
    expect(document.body.dataset.ttiPath).toBe('/chained')
    await waitFor(() => expect(get('late')).toBe('late-value'))
  })

  it('reports the open offset and the wait separately, which is what the budget needs', async () => {
    const measure = vi.fn()
    vi.stubGlobal('performance', Object.create(performance, {
      now: { value: () => 5000 },
      measure: { value: measure, configurable: true },
      mark: { value: vi.fn(), configurable: true },
    }))
    window.history.pushState({}, '', '/marked')

    render(<Resource name="solo" load={async () => 'done'} />)

    await waitFor(() => expect(measure).toHaveBeenCalled())
    const ready = measure.mock.calls.find(([n]) => n === 'page-data-ready')
    expect(ready).toBeDefined()
    const [, options] = ready!
    expect(options.detail).toBe('/marked')
    // queryCache names which resource resolved and where it came from, which is
    // the only thing that separates "the app is slow" from "the API is slow".
    expect(measure.mock.calls).toContainEqual([
      'api-duration:solo',
      expect.objectContaining({ detail: 'network' }),
    ])
    // FRONTEND_OVERHEAD_SECONDS is `data-tti-open` plus the non-network part of
    // `data-tti`, so the attributes and the mark have to describe the same interval.
    expect(document.body.dataset.ttiOpen).toBe(Number(options.start).toFixed(0))
    expect(document.body.dataset.tti).toBe(Number(options.end).toFixed(0))
  })

  it('stamps a page that composes its requests without the hook', async () => {
    // StockDetailPage and BacktestWorkbenchPage call cachedGet by hand -- status
    // specific error copy and a live progress feed -- so they register through this
    // primitive instead. If it stopped firing, the browser harness loses two of the
    // eight routes and can only report "no measurement" for pages users wait on.
    window.history.pushState({}, '', '/stock/600519')
    const release = trackViewResource('stock/600519/detail')
    expect(document.body.dataset.ttiPath).toBeUndefined()

    release()
    expect(document.body.dataset.ttiPath).toBe('/stock/600519')
    expect(Number(document.body.dataset.tti)).toBeGreaterThanOrEqual(0)
    // Both the response handler and the effect cleanup release the same key.
    expect(() => release()).not.toThrow()
  })
})

/**
 * Guards on the bounded retry.
 *
 * The dashboard used to latch its error on the first failure and never ask again
 * until the key remounted, so a browser that opened seconds before Django could
 * answer kept reading "Failed to load dashboard data" on a stack that had since
 * come up. These pin the three properties that make the retry safe rather than
 * merely optimistic: it only retries an outage, it stops, and it does not lie to
 * the latency instrument about when data arrived.
 */
describe('useApiResource retry budget', () => {
  function Probe({ name, load }: { name: string; load: () => Promise<unknown> }) {
    const resource = useApiResource<unknown>(name, load)
    const status = resource.error
      ? `|error:${resource.backendUnavailable ? 'unreachable' : 'failed'}`
      : ''
    return (
      <span data-testid={name}>
        {`${resource.loading ? 'pending' : String(resource.data)}${
          resource.isRetrying ? '|retrying' : ''
        }${status}`}
      </span>
    )
  }

  beforeEach(() => {
    vi.useFakeTimers()
    resetQueryCache()
    document.body.removeAttribute('data-tti')
    document.body.removeAttribute('data-tti-open')
    document.body.removeAttribute('data-tti-path')
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  // Advancing a fake timer resolves the backoff, but the state the retry loop then
  // sets lands in React outside the test's synchronous frame, so each step runs
  // inside act() or the assertion below it reads the pre-update DOM.
  const flush = async (ms: number) => {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms)
    })
  }

  it('recovers on its own when a startup failure is followed by a success', async () => {
    let attempts = 0
    const load = async () => {
      attempts += 1
      // 502 with an empty body is what Vite's proxy answers while Django is still
      // starting -- the exact status that produced the permanent banner.
      if (attempts === 1) throw new ApiRequestError('GET /dashboard/stocks/ failed: 502', 502)
      return 'recovered'
    }
    window.history.pushState({}, '', '/retry-blip')

    render(<Probe name="blip" load={load} />)
    await flush(0)
    expect(get('blip')).toContain('retrying')
    // The cache publishes the first failure immediately, so without suppressing it
    // for the duration of the backoff the page wears an error banner for ~15s and
    // only then admits the backend was still starting.
    expect(get('blip')).not.toContain('error:')

    await flush(1000)
    expect(attempts).toBe(2)
    expect(get('blip')).toContain('recovered')
  })

  it('does not retry a 4xx, which is an answer rather than an outage', async () => {
    let attempts = 0
    const load = async () => {
      attempts += 1
      throw new ApiRequestError('GET /dashboard/stocks/ failed: 404', 404)
    }
    window.history.pushState({}, '', '/retry-404')

    render(<Probe name="notfound" load={load} />)
    await flush(20_000)

    expect(attempts).toBe(1)
    // Retrying a refusal would only delay the error the user has to act on, and
    // would report it as an unreachable backend, which it is not.
    expect(get('notfound')).toContain('error:failed')
  })

  it('spends a bounded budget and then reports the backend as unreachable', async () => {
    let attempts = 0
    const load = async () => {
      attempts += 1
      throw new TypeError('Failed to fetch')
    }
    window.history.pushState({}, '', '/retry-outage')

    render(<Probe name="outage" load={load} />)
    await flush(20_000)

    expect(attempts).toBe(5)
    expect(get('outage')).toContain('error:unreachable')
  })

  it('does not stamp time-to-data at the first failure it intends to retry', async () => {
    // Stamping at the first 502 would report an earlier time-to-data than the one
    // the user actually waited for, which biases the latency budget toward a pass.
    let attempts = 0
    const load = async () => {
      attempts += 1
      if (attempts === 1) throw new ApiRequestError('GET /dashboard/stocks/ failed: 502', 502)
      return 'arrived'
    }
    window.history.pushState({}, '', '/retry-stamp')

    render(<Probe name="stamped" load={load} />)
    await flush(0)
    expect(document.body.dataset.tti).toBeUndefined()

    await flush(1000)
    expect(document.body.dataset.tti).toBeDefined()
    expect(Number(document.body.dataset.tti)).toBeGreaterThanOrEqual(1000)
  })
})

function get(testId: string): string {
  return screen.getByTestId(testId).textContent ?? ''
}
