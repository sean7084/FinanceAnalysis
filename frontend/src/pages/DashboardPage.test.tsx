import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import { I18nProvider } from '../i18n'
import { ApiRequestError } from '../lib/api'
import { resetQueryCache } from '../lib/queryCache'
import { DashboardPage } from './DashboardPage'

const apiMocks = vi.hoisted(() => ({
  fetchDashboardData: vi.fn(),
  fetchDashboardStocks: vi.fn(),
}))

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual('../lib/api')
  return {
    ...actual,
    fetchDashboardData: apiMocks.fetchDashboardData,
    fetchDashboardStocks: apiMocks.fetchDashboardStocks,
  }
})

const dashboardRow = {
  asset_id: 1,
  asset_symbol: '600061',
  asset_name: 'Sample Asset',
  date: '2026-04-24',
  candidate_rank: 1,
  candidate_rank_value: 0.358172,
  composite_score: 0.82,
  bottom_probability_score: 0.41,
  fundamental_score: 0.6,
  capital_flow_score: 0.52,
  technical_score: 0.57,
  pe_ttm_percentile_score: 0.3,
  pb_percentile_score: 0.25,
  roe_trend_score: 0.44,
  main_force_flow_score: 0.51,
  margin_flow_score: 0.32,
  technical_reversal_score: 0.55,
  current_close: 18.4,
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
  lightgbm_up_probability: 0.358172,
  lightgbm_confidence: 0.66,
  lightgbm_trade_score: 1.42,
  lightgbm_target_price: 21.1,
  lightgbm_stop_loss_price: 17.1,
  lightgbm_risk_reward_ratio: 1.74,
  lightgbm_suggested: true,
  lstm_label: 'UP',
  lstm_up_probability: 0.71,
  lstm_confidence: 0.71,
  lstm_trade_score: 1.84,
  lstm_target_price: 21.8,
  lstm_stop_loss_price: 16.9,
  lstm_risk_reward_ratio: 2.08,
  lstm_suggested: true,
}

const secondDashboardRow = {
  ...dashboardRow,
  asset_id: 2,
  asset_symbol: '600099',
  asset_name: 'Zeta Asset',
  candidate_rank: 2,
  candidate_rank_value: 0.341111,
  lightgbm_trade_score: 2.2,
  lightgbm_up_probability: 0.341111,
  lightgbm_suggested: false,
  lstm_trade_score: 2.55,
  lstm_up_probability: 0.62,
}

function renderDashboard(initialEntry = '/') {
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  )
}

// A fresh (<60s) cache entry is served without calling the loader at all, so a
// failure test that inherits the previous test's healthy summary would assert
// against a value it never asked for.
beforeEach(() => {
  resetQueryCache()
})

describe('DashboardPage', () => {
  beforeEach(() => {
    localStorage.setItem('finance_locale', 'en-US')
    apiMocks.fetchDashboardData.mockReset()
    apiMocks.fetchDashboardStocks.mockReset()
    apiMocks.fetchDashboardData.mockResolvedValue({
      macroPhase: 'RECOVERY',
      predictionSignals: 12,
      alertTriggers: 3,
      completedBacktests: 21,
      avgBottomProbability: 0.22,
    })
    apiMocks.fetchDashboardStocks.mockResolvedValue([dashboardRow])
  })

  it('hydrates dashboard filters and reminders from URL params', async () => {
    renderDashboard('/?prediction_source=lightgbm&horizon_days=7&candidate_mode=top_n&top_n=5&top_n_metric=up_prob_30d&up_threshold=0.45&trade_score_scope=independent&trade_score_threshold=1&max_positions=5&use_macro_context=true&source_run_id=84&source_run_name=Validation-lightgbm-2023-01-01-2024-12-31&reminder_holding_period_days=14&reminder_enable_stop_target_exit=true&reminder_capital_fraction_per_entry=0.2&reminder_initial_capital=200000.00&reminder_entry_weekdays=TUE,THU')

    await waitFor(() => {
      expect(apiMocks.fetchDashboardStocks).toHaveBeenCalledWith({
        predictionHorizon: 30,
        pageSize: 5,
        candidateFilters: {
          predictionSource: 'lightgbm',
          horizonDays: 7,
          upThreshold: 0.45,
          candidateMode: 'top_n',
          topN: 5,
          topNMetric: 'up_prob_30d',
          tradeScoreScope: 'independent',
          tradeScoreThreshold: 1,
          maxPositions: 5,
          useMacroContext: true,
        },
      })
    })

    expect(screen.getByText('Applied Backtest Reminder Fields')).toBeInTheDocument()
    expect(screen.getByText('#84 Validation-lightgbm-2023-01-01-2024-12-31')).toBeInTheDocument()
    expect(screen.getByText('TUE, THU')).toBeInTheDocument()
    expect(screen.queryByText('All Stocks Indicator Board')).not.toBeInTheDocument()
  })

  it('applies trade-score mode filters from the dashboard form', async () => {
    const user = userEvent.setup()
    renderDashboard()

    await waitFor(() => {
      expect(apiMocks.fetchDashboardStocks).toHaveBeenCalled()
    })

    apiMocks.fetchDashboardStocks.mockClear()

    await user.selectOptions(screen.getByLabelText('Candidate Selection Mode'), 'trade_score')

    expect(screen.queryByLabelText('Candidate Pool Size (Top N)')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Max Concurrent Positions')).toBeInTheDocument()

    await user.clear(screen.getByLabelText('Max Concurrent Positions'))
    await user.type(screen.getByLabelText('Max Concurrent Positions'), '6')
    await user.click(screen.getByRole('button', { name: 'Apply Dashboard Filters' }))

    await waitFor(() => {
      expect(apiMocks.fetchDashboardStocks).toHaveBeenLastCalledWith({
        predictionHorizon: 7,
        pageSize: 6,
        candidateFilters: {
          predictionSource: 'lightgbm',
          horizonDays: 7,
          upThreshold: 0.45,
          candidateMode: 'trade_score',
          topN: 8,
          topNMetric: 'up_prob_7d',
          tradeScoreScope: 'independent',
          tradeScoreThreshold: 1,
          maxPositions: 6,
          useMacroContext: true,
        },
      })
    })
  })

  it('renders only the selected prediction source columns', async () => {
    renderDashboard('/?prediction_source=lstm&horizon_days=7&candidate_mode=top_n&top_n=4&top_n_metric=up_prob_7d&up_threshold=0.45&trade_score_scope=independent&trade_score_threshold=1&max_positions=5&use_macro_context=true')

    const table = await screen.findByRole('table')

    expect(within(table).getByRole('button', { name: 'LSTM' })).toBeInTheDocument()
    expect(within(table).queryByRole('button', { name: 'LightGBM' })).not.toBeInTheDocument()
    expect(within(table).queryByRole('button', { name: 'Heuristic' })).not.toBeInTheDocument()
    expect(screen.getByText('Filtered Candidates')).toBeInTheDocument()
  })

  it('renders the KPI cards as soon as the summary lands, without waiting for candidates', async () => {
    apiMocks.fetchDashboardStocks.mockReturnValue(new Promise(() => {}))
    renderDashboard()

    await waitFor(() => {
      expect(screen.getByText('RECOVERY')).toBeInTheDocument()
    })
    expect(screen.queryByText('Sample Asset')).not.toBeInTheDocument()
  })

  it('sorts the filtered candidate list from sortable headers', async () => {
    const user = userEvent.setup()
    apiMocks.fetchDashboardStocks.mockResolvedValue([dashboardRow, secondDashboardRow])
    renderDashboard()

    const table = await screen.findByRole('table')
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent('600061')

    await user.click(within(table).getByRole('button', { name: 'Trade Score' }))

    expect(within(table).getAllByRole('row')[1]).toHaveTextContent('600099')
  })
})

const BACKEND_DOWN = 'The backend is not answering. Check that ./scripts/run_local_stack.sh is running (the SPA and the API are both on http://localhost:8000/).'
const LOAD_ERROR = 'Failed to load dashboard data.'

const degradedSummary = {
  macroPhase: 'N/A',
  predictionSignals: 0,
  alertTriggers: 0,
  completedBacktests: 0,
  avgBottomProbability: 0,
  degraded: true,
}

function metricCard(label: string) {
  return within(screen.getByText(label).closest('article')!)
}

// The retry backoff is real setTimeout, so the budget has to be driven rather than
// waited out. RTL's waitFor polls on those same timers and would never advance.
const flush = async (ms: number) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

describe('DashboardPage failure states', () => {
  beforeEach(() => {
    localStorage.setItem('finance_locale', 'en-US')
    apiMocks.fetchDashboardData.mockReset()
    apiMocks.fetchDashboardStocks.mockReset()
    apiMocks.fetchDashboardData.mockResolvedValue({ ...degradedSummary, degraded: false })
    apiMocks.fetchDashboardStocks.mockResolvedValue([dashboardRow])
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders N/A on the KPI cards when the summary endpoint degrades', async () => {
    // The old apiGetSafe path turned a failed summary into zeros, which read as a
    // real measurement: no backtests ever run, no bottom probability. N/A is the
    // honest answer, and it is the only thing separating the two on screen.
    apiMocks.fetchDashboardData.mockResolvedValue(degradedSummary)
    renderDashboard()
    await flush(0)

    expect(metricCard('Completed Backtests').getByText('N/A')).toBeInTheDocument()
    expect(metricCard('Completed Backtests').queryByText('0')).not.toBeInTheDocument()
    expect(metricCard('Avg Bottom Probability').getByText('N/A')).toBeInTheDocument()
    expect(metricCard('Avg Bottom Probability').queryByText('0.0%')).not.toBeInTheDocument()

    // Degradation is card-scoped. fetchDashboardData resolves rather than rejects,
    // so the candidates still render and no banner is claimed.
    expect(screen.getByText('Sample Asset')).toBeInTheDocument()
    expect(screen.queryByText(LOAD_ERROR)).not.toBeInTheDocument()
    expect(screen.queryByText(BACKEND_DOWN)).not.toBeInTheDocument()
  })

  it('shows the backend-down banner after the retry budget is spent', async () => {
    apiMocks.fetchDashboardData.mockResolvedValue(degradedSummary)
    apiMocks.fetchDashboardStocks.mockRejectedValue(new TypeError('Failed to fetch'))
    renderDashboard()
    await flush(0)

    // A page opened while Django is still starting must not accuse the backend of
    // being down on the first refused connection: it retries for ~15s first.
    expect(screen.queryByText(BACKEND_DOWN)).not.toBeInTheDocument()
    expect(screen.queryByText(LOAD_ERROR)).not.toBeInTheDocument()
    expect(metricCard('Suggested Setups').getByText('Loading...')).toBeInTheDocument()
    expect(apiMocks.fetchDashboardStocks).toHaveBeenCalledTimes(1)

    await flush(20_000)

    expect(screen.getByText(BACKEND_DOWN)).toBeInTheDocument()
    expect(apiMocks.fetchDashboardStocks).toHaveBeenCalledTimes(5)
    expect(apiMocks.fetchDashboardData).toHaveBeenCalledTimes(1)
    expect(metricCard('Completed Backtests').getByText('N/A')).toBeInTheDocument()
  })

  it('keeps the generic load-error copy for a 4xx refusal', async () => {
    // A 404 is the backend answering and refusing. Retrying it only delays the
    // error, and telling the reader to go start a stack that is already running
    // sends them after the wrong problem.
    apiMocks.fetchDashboardStocks.mockRejectedValue(
      new ApiRequestError('GET /api/v1/dashboard/stocks/ failed: 404', 404),
    )
    renderDashboard()
    await flush(0)

    expect(screen.getByText(LOAD_ERROR)).toBeInTheDocument()
    expect(screen.queryByText(BACKEND_DOWN)).not.toBeInTheDocument()
    expect(apiMocks.fetchDashboardStocks).toHaveBeenCalledTimes(1)
  })
})