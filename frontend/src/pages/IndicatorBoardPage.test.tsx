import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import { I18nProvider } from '../i18n'
import { IndicatorBoardPage } from './IndicatorBoardPage'

const apiMocks = vi.hoisted(() => ({
  fetchDashboardStocks: vi.fn(),
}))

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual('../lib/api')
  return {
    ...actual,
    fetchDashboardStocks: apiMocks.fetchDashboardStocks,
    hasAnyAuthCredential: vi.fn(() => true),
  }
})

describe('IndicatorBoardPage', () => {
  beforeEach(() => {
    localStorage.setItem('finance_locale', 'en-US')
    apiMocks.fetchDashboardStocks.mockReset()
    apiMocks.fetchDashboardStocks.mockResolvedValue([
      {
        asset_id: 1,
        asset_symbol: '300394',
        asset_name: 'Indicator Asset',
        date: '2026-04-24',
        composite_score: 0.82,
        bottom_probability_score: 0.41,
        fundamental_score: 0.6,
        capital_flow_score: 0.52,
        technical_score: 0.57,
        factor_sentiment_score: 0.48,
        pe_percentile_score: 0.3,
        pb_percentile_score: 0.25,
        roe_trend_score: 0.44,
        main_force_flow_score: 0.51,
        margin_flow_score: 0.32,
        technical_reversal_score: 0.55,
        current_close: 18.4,
        sentiment_score: 0.22,
        sentiment_label: 'POSITIVE',
        rsi: 55.2,
        macd: 0.18,
        bb_upper: 20.1,
        bb_lower: 16.2,
        sma_60: 17.8,
        heuristic_label: 'UP',
        heuristic_up_probability: 0.31,
        heuristic_confidence: 0.61,
        heuristic_trade_score: 1.21,
        heuristic_target_price: 20.4,
        heuristic_stop_loss_price: 17.2,
        heuristic_risk_reward_ratio: 1.63,
        heuristic_suggested: true,
        lightgbm_label: 'UP',
        lightgbm_up_probability: 0.35,
        lightgbm_confidence: 0.66,
        lightgbm_trade_score: 1.42,
        lightgbm_target_price: 21.1,
        lightgbm_stop_loss_price: 17.1,
        lightgbm_risk_reward_ratio: 1.74,
        lightgbm_suggested: true,
      },
    ])
  })

  it('renders the standalone indicator board and loads dashboard rows', async () => {
    render(
      <I18nProvider>
        <MemoryRouter initialEntries={['/indicator-board']}>
          <Routes>
            <Route path="/indicator-board" element={<IndicatorBoardPage />} />
          </Routes>
        </MemoryRouter>
      </I18nProvider>,
    )

    await waitFor(() => {
      expect(apiMocks.fetchDashboardStocks).toHaveBeenCalledWith({ predictionHorizon: 7, pageSize: 120 })
    })

    expect(screen.getByText('All Stocks Indicator Board')).toBeInTheDocument()
    expect(screen.getByText('Indicator Asset')).toBeInTheDocument()
  })
})