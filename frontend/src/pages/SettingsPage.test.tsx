import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import { I18nProvider } from '../i18n'
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
