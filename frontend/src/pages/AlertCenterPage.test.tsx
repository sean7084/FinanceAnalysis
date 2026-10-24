import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '../i18n'
import { AlertCenterPage } from './AlertCenterPage'

const apiMocks = vi.hoisted(() => ({ apiGet: vi.fn() }))

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual('../lib/api')
  return { ...actual, apiGet: apiMocks.apiGet }
})

vi.mock('../hooks/useAlertsSocket', () => ({
  useAlertsSocket: () => ({ connected: true, reconnecting: false, messages: [] }),
}))

// Feed-level alerts carry no asset: the serializer resolves ``asset_symbol`` to null
// instead of traversing a missing relation, and the page must still name the event.
const feedEvent = {
  id: 41,
  alert_rule: 9,
  alert_name: 'ETF data feed health',
  status: 'SENT',
  asset: null,
  asset_symbol: null,
  message: 'The benchmark H00905.CSI is 1 session behind the newest ETF bar.',
  metadata: { condition_type: 'FEED_STALE', reason_code: 'benchmark_lag' },
  created_at: '2026-10-24T09:00:00Z',
}

const assetEvent = {
  ...feedEvent,
  id: 42,
  alert_name: 'Price breakout',
  asset: 3,
  asset_symbol: '600000',
  metadata: { condition_type: 'PRICE_ABOVE' },
}

describe('AlertCenterPage', () => {
  it('names an asset-less feed event instead of rendering a null asset', async () => {
    apiMocks.apiGet.mockResolvedValue({
      count: 2,
      next: null,
      previous: null,
      results: [feedEvent, assetEvent],
    })

    render(
      <I18nProvider>
        <AlertCenterPage />
      </I18nProvider>,
    )

    expect(await screen.findByText(/ETF data feed health/)).toBeInTheDocument()
    expect(screen.getByText(/数据源健康/)).toBeInTheDocument()
    expect(screen.getByText(/600000/)).toBeInTheDocument()
    expect(screen.queryByText(/null/)).toBeNull()
  })
})
