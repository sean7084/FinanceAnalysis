import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import { I18nProvider } from '../i18n'
import { GENERATION_POLL_MS, StockDetailPage } from './StockDetailPage'
import { resetQueryCache } from '../lib/queryCache'

const apiMocks = vi.hoisted(() => ({
  fetchAssets: vi.fn(),
  fetchAssetBySymbol: vi.fn(),
  fetchOhlcvByAsset: vi.fn(),
  fetchPredictionBySymbol: vi.fn(),
  fetchLightGBMPredictionBySymbol: vi.fn(),
  fetchSentimentByAsset: vi.fn(),
}))

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual('../lib/api')
  return { ...actual, ...apiMocks }
})

// lightweight-charts wants a measurable canvas host, which jsdom does not give
// it. This suite is about which rows reached state, not about pixels.
vi.mock('../components/charts/CandlestickChart', () => ({
  CandlestickChart: () => <div data-testid="kline-chart" />,
}))

// jsdom ships no ResizeObserver and recharts' ResponsiveContainer needs one to
// size itself. Stubbed here rather than in the shared setup, because this is the
// only suite that mounts a real chart.
globalThis.ResizeObserver ??= class {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
} as unknown as typeof ResizeObserver

const QUEUED_NOTICE = 'No stored prediction for this asset yet. Generation is queued and will appear shortly.'
const UNREACHABLE_NOTICE = 'No stored prediction for this asset, and the task queue is unreachable.'

function emptyPrediction(status: 'queued' | 'unavailable') {
  return {
    stock_code: '600519',
    date: '2026-10-20',
    generation: { status, scope: 'asset' },
    results: [],
  }
}

function filledPrediction() {
  return {
    stock_code: '600519',
    date: '2026-10-20',
    results: [
      {
        horizon_days: 7,
        up: 0.61,
        flat: 0.1,
        down: 0.29,
        confidence: 0.72,
        predicted_label: 'UP',
        target_price: 1800,
        stop_loss_price: 1500,
        risk_reward_ratio: 2.4,
        trade_score: 1.1,
        suggested: true,
      },
    ],
  }
}

function renderStockDetail() {
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={['/stock/600519']}>
        <Routes>
          <Route path="/stock/:symbol" element={<StockDetailPage />} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  )
}

/**
 * Watch the poll instead of waiting for it.
 *
 * The five-second interval is the thing under test, so the suite drives its
 * callback by hand rather than making the run last a minute. The spy still
 * delegates to the real timer: testing-library's own `waitFor` polls through
 * `setInterval`, and a stub that swallows every registration breaks it -- which
 * is why the delay, not the call count, identifies the page's interval.
 */
function capturePollInterval() {
  const realSetInterval = window.setInterval.bind(window)
  const realClearInterval = window.clearInterval.bind(window)
  const handlers: Array<() => void> = []
  // Node's timer handle, not a number: under vitest these are Timeout objects,
  // so the page's handle is matched by identity rather than by id.
  const pollHandles = new Set<unknown>()
  let cleared = 0

  vi.spyOn(window, 'setInterval').mockImplementation(((handler: TimerHandler, timeout?: number, ...args: unknown[]) => {
    const handle = realSetInterval(handler, timeout, ...args)
    if (timeout === GENERATION_POLL_MS && typeof handler === 'function') {
      handlers.push(handler as () => void)
      pollHandles.add(handle)
    }
    return handle as ReturnType<typeof window.setInterval>
  }) as typeof window.setInterval)

  vi.spyOn(window, 'clearInterval').mockImplementation(((handle?: unknown) => {
    if (pollHandles.delete(handle)) {
      cleared += 1
    }
    realClearInterval(handle as Parameters<typeof window.clearInterval>[0])
  }) as typeof window.clearInterval)

  return { handlers, clearedCount: () => cleared }
}

describe('StockDetailPage queued generation', () => {
  beforeEach(() => {
    localStorage.setItem('finance_locale', 'en-US')
    resetQueryCache()
    Object.values(apiMocks).forEach((mock) => mock.mockReset())

    apiMocks.fetchAssets.mockResolvedValue([{ symbol: '600519', name: 'Kweichow Moutai' }])
    apiMocks.fetchAssetBySymbol.mockResolvedValue({ id: 1, symbol: '600519', name: 'Kweichow Moutai' })
    apiMocks.fetchOhlcvByAsset.mockResolvedValue([])
    apiMocks.fetchSentimentByAsset.mockResolvedValue([])
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('polls a queued generation and fills in the rows that land', async () => {
    const { handlers, clearedCount } = capturePollInterval()
    apiMocks.fetchPredictionBySymbol
      .mockResolvedValueOnce(emptyPrediction('queued'))
      .mockResolvedValue(filledPrediction())
    apiMocks.fetchLightGBMPredictionBySymbol
      .mockResolvedValueOnce(emptyPrediction('queued'))
      .mockResolvedValue(filledPrediction())

    renderStockDetail()

    await waitFor(() => expect(screen.getByText(QUEUED_NOTICE)).toBeInTheDocument())
    expect(screen.queryByText('7D')).toBeNull()
    // One load, one poll: the first load registered exactly one interval.
    expect(handlers).toHaveLength(1)
    // The initial composed load is one request each, not two -- the poll is what
    // adds the second, below.
    expect(apiMocks.fetchAssetBySymbol).toHaveBeenCalledTimes(1)

    await act(async () => {
      handlers.at(-1)?.()
    })
    expect(apiMocks.fetchPredictionBySymbol).toHaveBeenCalledTimes(2)
    expect(screen.getAllByText('7D').length).toBeGreaterThan(0)
    expect(screen.queryByText(QUEUED_NOTICE)).toBeNull()
    // Rows arrived, so the wait is over and its timer must not outlive it: a
    // leaked interval keeps asking a page that already answered.
    expect(clearedCount()).toBe(1)
    // and the re-read is a cache hit, not five fresh requests.
    expect(apiMocks.fetchAssetBySymbol).toHaveBeenCalledTimes(1)
    expect(apiMocks.fetchOhlcvByAsset).toHaveBeenCalledTimes(1)
  })

  it('does not poll a queue it could not reach', async () => {
    const { handlers } = capturePollInterval()
    apiMocks.fetchPredictionBySymbol.mockResolvedValue(emptyPrediction('unavailable'))
    apiMocks.fetchLightGBMPredictionBySymbol.mockResolvedValue(emptyPrediction('unavailable'))

    renderStockDetail()

    await waitFor(() => expect(screen.getByText(UNREACHABLE_NOTICE)).toBeInTheDocument())
    expect(apiMocks.fetchPredictionBySymbol).toHaveBeenCalledTimes(1)
    // 'unavailable' means the broker is down. Re-asking is not the fix, and a
    // page that retries forever is the failure mode this release removed.
    expect(handlers).toHaveLength(0)
  })

  it('stops asking after a minute of an empty queue', async () => {
    const { handlers, clearedCount } = capturePollInterval()
    apiMocks.fetchPredictionBySymbol.mockResolvedValue(emptyPrediction('queued'))
    apiMocks.fetchLightGBMPredictionBySymbol.mockResolvedValue(emptyPrediction('queued'))

    renderStockDetail()

    await waitFor(() => expect(screen.getByText(QUEUED_NOTICE)).toBeInTheDocument())
    expect(apiMocks.fetchPredictionBySymbol).toHaveBeenCalledTimes(1)

    for (let tick = 1; tick <= 13; tick += 1) {
      await act(async () => {
        handlers.at(-1)?.()
      })
      // Twelve ticks and no more: the cap exists so a cold sweep that outruns a
      // user's patience leaves the notice up instead of a background loop.
      expect(apiMocks.fetchPredictionBySymbol).toHaveBeenCalledTimes(Math.min(1 + tick, 13))
      expect(handlers).toHaveLength(1)
    }
    expect(clearedCount()).toBe(1)
    expect(screen.getByText(QUEUED_NOTICE)).toBeInTheDocument()
  })
})
