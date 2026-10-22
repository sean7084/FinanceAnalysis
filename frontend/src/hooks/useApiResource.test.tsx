import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { resetQueryCache } from '../lib/queryCache'
import { useApiResource } from './useApiResource'

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
})

function get(testId: string): string {
  return screen.getByTestId(testId).textContent ?? ''
}
