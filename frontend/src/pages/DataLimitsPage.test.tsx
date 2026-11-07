import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import { I18nProvider } from '../i18n'
import { ApiRequestError } from '../lib/api'
import { resetQueryCache } from '../lib/queryCache'
import { DataLimitsPage } from './DataLimitsPage'

const apiMocks = vi.hoisted(() => ({ fetchDataLimits: vi.fn() }))

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual('../lib/api')
  return { ...actual, fetchDataLimits: apiMocks.fetchDataLimits }
})

const payload = {
  datasets: [
    {
      label: 'Stock OHLCV', model: 'markets.OHLCV', field: 'date',
      earliest: '2008-07-02', latest: '2026-11-06', rows_estimate: 6523067,
      note: 'no date-leading index',
    },
    {
      label: 'ETF feature snapshots', model: 'etf_rotation.ETFFeatureSnapshot', field: 'date',
      earliest: '2016-01-04', latest: '2026-11-05', rows_estimate: 2970655, note: '',
    },
  ],
  etf_coverage: {
    listed_funds: 3009, funds_with_first_trade_date: 2730,
    earliest_first_trade_date: '2016-01-04', latest_first_trade_date: '2026-10-22',
    eligible_on_latest_snapshot: 512,
  },
  thresholds: [
    {
      key: 'walk_forward_min_train_sessions', label: 'Walk-forward minimum train',
      value: 250, unit: 'trading sessions', source: 'run_etf_rotation_backtest.py',
      configured_in: 'hardcoded fallback',
    },
  ],
  max_positions_conflict: {
    values: [3, 5],
    entries: [],
  },
  generated_at: '2026-11-06T10:00:00',
  contract: { escape_hatch_flag: 'allow-pre-contract-history', note: '', asset_classes: [] },
}

function renderPage() {
  return render(
    <I18nProvider>
      <MemoryRouter>
        <DataLimitsPage />
      </MemoryRouter>
    </I18nProvider>,
  )
}

beforeEach(() => {
  apiMocks.fetchDataLimits.mockReset()
  resetQueryCache()
  localStorage.setItem('finance_locale', 'en-US')
})

describe('DataLimitsPage', () => {
  it('shows the stored extent and the thresholds that size a window', async () => {
    apiMocks.fetchDataLimits.mockResolvedValue(payload)
    renderPage()

    await waitFor(() => expect(screen.getByText('Stock OHLCV')).toBeInTheDocument())
    expect(screen.getByText('2008-07-02')).toBeInTheDocument()
    expect(screen.getByText('ETF feature snapshots')).toBeInTheDocument()
    expect(screen.getByText('250 trading sessions')).toBeInTheDocument()
  })

  it('labels row counts as estimates rather than exact counts', async () => {
    apiMocks.fetchDataLimits.mockResolvedValue(payload)
    renderPage()

    await waitFor(() => expect(screen.getByText('6,523,067')).toBeInTheDocument())
    expect(screen.getByText('Rows (est.)')).toBeInTheDocument()
  })

  it('surfaces the max-positions divergence between the two trainers', async () => {
    apiMocks.fetchDataLimits.mockResolvedValue(payload)
    renderPage()

    await waitFor(() => expect(screen.getByText('3 vs 5')).toBeInTheDocument())
  })

  it('keeps reporting table extent instead of inventing a coverage figure', async () => {
    apiMocks.fetchDataLimits.mockResolvedValue(payload)
    renderPage()

    await waitFor(() => expect(screen.getByText('Stored data extent')).toBeInTheDocument())
    expect(screen.getByText('no date-leading index')).toBeInTheDocument()
    expect(screen.queryByText(/dataLimits\.|settings\.contract/)).not.toBeInTheDocument()
  })

  it('surfaces a load failure', async () => {
    apiMocks.fetchDataLimits.mockRejectedValue(new Error('offline'))
    renderPage()

    await waitFor(() =>
      expect(screen.getByText('Failed to load data limits.')).toBeInTheDocument(),
    )
  })
})

const BACKEND_DOWN =
  'The backend is not answering. Check that ./scripts/run_local_stack.sh is running (the SPA and the API are both on http://localhost:8000/).'
const LOAD_ERROR = 'Failed to load data limits.'

const flush = async (ms: number) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

describe('DataLimitsPage failure states', () => {
  beforeEach(() => {
    localStorage.setItem('finance_locale', 'en-US')
    apiMocks.fetchDataLimits.mockReset()
    resetQueryCache()
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('retries a cold-start refusal before claiming the backend is down', async () => {
    // This page is the first thing an operator opens when the stack looks wrong,
    // so a premature "backend is down" here is actively misleading.
    apiMocks.fetchDataLimits.mockRejectedValue(new TypeError('Failed to fetch'))
    renderPage()
    await flush(0)

    expect(screen.queryByText(BACKEND_DOWN)).not.toBeInTheDocument()
    expect(screen.queryByText(LOAD_ERROR)).not.toBeInTheDocument()
    expect(apiMocks.fetchDataLimits).toHaveBeenCalledTimes(1)

    await flush(20_000)

    expect(screen.getByText(BACKEND_DOWN)).toBeInTheDocument()
    expect(apiMocks.fetchDataLimits).toHaveBeenCalledTimes(5)
  })

  it('keeps the generic copy for a 4xx refusal', async () => {
    apiMocks.fetchDataLimits.mockRejectedValue(
      new ApiRequestError('GET /api/v1/system/data-limits/ failed: 404', 404),
    )
    renderPage()
    await flush(0)

    expect(screen.getByText(LOAD_ERROR)).toBeInTheDocument()
    expect(screen.queryByText(BACKEND_DOWN)).not.toBeInTheDocument()
    expect(apiMocks.fetchDataLimits).toHaveBeenCalledTimes(1)
  })
})
