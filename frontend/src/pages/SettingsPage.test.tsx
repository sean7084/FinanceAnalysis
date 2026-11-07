import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import { I18nProvider } from '../i18n'
import { ApiRequestError } from '../lib/api'
import { resetQueryCache } from '../lib/queryCache'
import { SettingsPage } from './SettingsPage'

const apiMocks = vi.hoisted(() => ({ fetchDateContract: vi.fn() }))

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual('../lib/api')
  return { ...actual, fetchDateContract: apiMocks.fetchDateContract }
})

const contract = {
  escape_hatch_flag: 'allow-pre-contract-history',
  note: 'Raw-data bounds are observations about stored history, not enforced limits.',
  asset_classes: [
    {
      key: 'stocks', raw_data_from: '2008-07-02',
      training_start: '2016-06-01', backtest_start: '2018-01-02',
    },
    {
      key: 'etf', raw_data_from: '2016-01-04',
      training_start: '2020-01-02', backtest_start: '2021-01-13',
    },
  ],
}

function renderPage() {
  return render(
    <I18nProvider>
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    </I18nProvider>,
  )
}

beforeEach(() => {
  apiMocks.fetchDateContract.mockReset()
  resetQueryCache()
  localStorage.setItem('finance_locale', 'en-US')
})

describe('SettingsPage date contract', () => {
  it('renders every boundary from the API', async () => {
    apiMocks.fetchDateContract.mockResolvedValue(contract)
    renderPage()

    await waitFor(() => expect(screen.getByText('2016-06-01')).toBeInTheDocument())
    expect(screen.getByText('2018-01-02')).toBeInTheDocument()
    expect(screen.getByText('2020-01-02')).toBeInTheDocument()
    expect(screen.getByText('2021-01-13')).toBeInTheDocument()
    expect(screen.getByText('2008-07-02')).toBeInTheDocument()
  })

  it('localises the table rather than leaking raw dictionary keys', async () => {
    apiMocks.fetchDateContract.mockResolvedValue(contract)
    renderPage()

    await waitFor(() => expect(screen.getByText('Date contract')).toBeInTheDocument())
    expect(screen.getByText('Training starts')).toBeInTheDocument()
    expect(screen.queryByText(/settings\.contract/)).not.toBeInTheDocument()
    expect(screen.queryByText(/dataLimits\./)).not.toBeInTheDocument()
  })

  it('labels the two asset classes distinctly', async () => {
    apiMocks.fetchDateContract.mockResolvedValue(contract)
    renderPage()

    await waitFor(() => expect(screen.getByText('Stocks')).toBeInTheDocument())
    expect(screen.getByText('ETFs')).toBeInTheDocument()
  })

  it('surfaces a load failure instead of an empty table', async () => {
    apiMocks.fetchDateContract.mockRejectedValue(new Error('offline'))
    renderPage()

    await waitFor(() =>
      expect(screen.getByText('Failed to load the date contract.')).toBeInTheDocument(),
    )
  })
})

const BACKEND_DOWN =
  'The backend is not answering. Check that ./scripts/run_local_stack.sh is running (the SPA and the API are both on http://localhost:8000/).'
const LOAD_ERROR = 'Failed to load the date contract.'

const flush = async (ms: number) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

describe('SettingsPage contract failure states', () => {
  beforeEach(() => {
    localStorage.setItem('finance_locale', 'en-US')
    apiMocks.fetchDateContract.mockReset()
    resetQueryCache()
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('retries a cold-start refusal before claiming the backend is down', async () => {
    // /settings is a plausible first tab while Django is still booting, so it must
    // not accuse the backend on the first refused connection.
    apiMocks.fetchDateContract.mockRejectedValue(new TypeError('Failed to fetch'))
    renderPage()
    await flush(0)

    expect(screen.queryByText(BACKEND_DOWN)).not.toBeInTheDocument()
    expect(screen.queryByText(LOAD_ERROR)).not.toBeInTheDocument()
    expect(apiMocks.fetchDateContract).toHaveBeenCalledTimes(1)

    await flush(20_000)

    expect(screen.getByText(BACKEND_DOWN)).toBeInTheDocument()
    expect(apiMocks.fetchDateContract).toHaveBeenCalledTimes(5)
  })

  it('keeps the generic copy for a 4xx refusal', async () => {
    // A 404 is the backend answering and refusing; retrying only delays it, and
    // telling the reader to start a stack that is already running sends them after
    // the wrong problem.
    apiMocks.fetchDateContract.mockRejectedValue(
      new ApiRequestError('GET /api/v1/system/date-contract/ failed: 404', 404),
    )
    renderPage()
    await flush(0)

    expect(screen.getByText(LOAD_ERROR)).toBeInTheDocument()
    expect(screen.queryByText(BACKEND_DOWN)).not.toBeInTheDocument()
    expect(apiMocks.fetchDateContract).toHaveBeenCalledTimes(1)
  })
})
