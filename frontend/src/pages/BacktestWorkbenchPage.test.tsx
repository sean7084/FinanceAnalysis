import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'

import { I18nProvider } from '../i18n'
import { createBacktestRun, fetchBacktestComparisonCurve } from '../lib/api'
import { BacktestWorkbenchPage } from './BacktestWorkbenchPage'

vi.stubGlobal(
  'ResizeObserver',
  class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
)

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual('../lib/api')
  return {
    ...actual,
    createBacktestRun: vi.fn(async () => ({ id: 1 })),
    fetchBacktestRuns: vi.fn(async () => [
      {
        id: 84,
        name: 'Validation-lightgbm-2023-01-01-2024-12-31',
        strategy_type: 'PREDICTION_THRESHOLD',
        status: 'COMPLETED',
        start_date: '2023-01-01',
        end_date: '2024-12-31',
        initial_capital: 200000,
        final_value: 210000,
        total_return: 0.05,
        annualized_return: 0.03,
        max_drawdown: -0.08,
        sharpe_ratio: 1.2,
        win_rate: 0.55,
        total_trades: 10,
        winning_trades: 6,
        parameters: {
          prediction_source: 'lightgbm',
          top_n: 5,
          horizon_days: 7,
          up_threshold: 0.5,
          entry_weekdays: ['TUE', 'THU'],
          holding_period_days: 14,
          capital_fraction_per_entry: 0.2,
          candidate_mode: 'top_n',
          top_n_metric: 'up_prob_7d',
          trade_score_scope: 'independent',
          trade_score_threshold: 1,
          max_positions: 5,
          use_macro_context: true,
          enable_stop_target_exit: true,
        },
        report: {
          entry_weekdays: [1, 3],
          holding_period_days: 14,
        },
        created_at: '2026-04-24T00:00:00Z',
      },
      {
        id: 60,
        name: 'Validation-lstm-2023-01-01-2024-12-31',
        strategy_type: 'PREDICTION_THRESHOLD',
        status: 'COMPLETED',
        start_date: '2023-01-01',
        end_date: '2024-12-31',
        initial_capital: 180000,
        final_value: 189000,
        total_return: 0.05,
        annualized_return: 0.03,
        max_drawdown: -0.07,
        sharpe_ratio: 1.1,
        win_rate: 0.52,
        total_trades: 9,
        winning_trades: 5,
        parameters: {
          prediction_source: 'lstm',
          top_n: 4,
          horizon_days: 7,
          up_threshold: 0.48,
          entry_weekdays: ['MON', 'WED'],
          holding_period_days: 10,
          capital_fraction_per_entry: 0.25,
          candidate_mode: 'top_n',
          top_n_metric: 'up_prob_7d',
          trade_score_scope: 'independent',
          trade_score_threshold: 1,
          max_positions: 4,
          use_macro_context: true,
          enable_stop_target_exit: true,
        },
        report: {
          prediction_source: 'lstm',
          entry_weekdays: [0, 2],
          holding_period_days: 10,
        },
        created_at: '2026-04-23T00:00:00Z',
      },
      {
        id: 52,
        name: 'Previous LightGBM',
        strategy_type: 'PREDICTION_THRESHOLD',
        status: 'COMPLETED',
        start_date: '2022-01-01',
        end_date: '2023-12-31',
        initial_capital: 150000,
        final_value: 156000,
        total_return: 0.04,
        annualized_return: 0.02,
        max_drawdown: -0.09,
        sharpe_ratio: 0.9,
        win_rate: 0.5,
        total_trades: 8,
        winning_trades: 4,
        parameters: {
          prediction_source: 'lightgbm',
          top_n: 6,
          horizon_days: 7,
          up_threshold: 0.47,
          entry_weekdays: ['TUE', 'FRI'],
          holding_period_days: 12,
          capital_fraction_per_entry: 0.15,
          candidate_mode: 'top_n',
          top_n_metric: 'up_prob_7d',
          trade_score_scope: 'independent',
          trade_score_threshold: 1,
          max_positions: 4,
          use_macro_context: true,
          enable_stop_target_exit: true,
        },
        report: {
          prediction_source: 'lightgbm',
          entry_weekdays: [1, 4],
          holding_period_days: 12,
        },
        created_at: '2026-04-22T00:00:00Z',
      },
    ]),
    fetchBacktestTrades: vi.fn(async () => []),
    fetchBacktestComparisonCurve: vi.fn(async () => ({
      run: {
        id: 84,
        name: 'Validation-lightgbm-2023-01-01-2024-12-31',
        status: 'COMPLETED',
        start_date: '2023-01-01',
        end_date: '2024-12-31',
        initial_capital: 200000,
        prediction_source: 'lightgbm',
        compare_backtest_run_id: null,
      },
      series: [
        {
          key: 'selected_run',
          label: '#84 Validation-lightgbm-2023-01-01-2024-12-31',
          kind: 'backtest',
          run_id: 84,
          prediction_source: 'lightgbm',
          total_return: 0.05,
          max_drawdown: -0.08,
          points: [
            { date: '2023-01-01', value: 200000, drawdown: 0 },
            { date: '2024-12-31', value: 210000, drawdown: -0.02 },
          ],
        },
        {
          key: 'csi300',
          label: 'CSI 300',
          kind: 'benchmark',
          index_code: '000300.SH',
          total_return: 0.03,
          max_drawdown: -0.06,
          points: [
            { date: '2023-01-01', value: 200000, drawdown: 0 },
            { date: '2024-12-31', value: 206000, drawdown: -0.03 },
          ],
        },
        {
          key: 'csia500',
          label: 'CSI A500',
          kind: 'benchmark',
          index_code: '000510.CSI',
          total_return: 0.01,
          max_drawdown: -0.05,
          points: [
            { date: '2023-01-01', value: 200000, drawdown: 0 },
            { date: '2024-12-31', value: 202000, drawdown: -0.04 },
          ],
        },
      ],
      compare_target: null,
      available_series_keys: ['selected_run', 'csi300', 'csia500'],
      message: null,
    })),
    hasAnyAuthCredential: vi.fn(() => true),
  }
})

const mockFetchBacktestComparisonCurve = vi.mocked(fetchBacktestComparisonCurve)
const mockCreateBacktestRun = vi.mocked(createBacktestRun)

function LocationDisplay() {
  const location = useLocation()
  return <div data-testid="location-display">{`${location.pathname}${location.search}`}</div>
}

function renderWorkbench() {
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={['/backtest']}>
        <LocationDisplay />
        <Routes>
          <Route path="/backtest" element={<BacktestWorkbenchPage />} />
          <Route path="/" element={<div data-testid="dashboard-target">dashboard</div>} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  )
}

describe('BacktestWorkbenchPage runner controls', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.setItem('finance_locale', 'en-US')
  })

  it('toggles mode-scoped controls visibility', async () => {
    const user = userEvent.setup()

    renderWorkbench()

    expect(screen.getByLabelText('Candidate Pool Size (Top N)')).toBeInTheDocument()
    expect(screen.getByLabelText('Top N Ranking Metric')).toBeInTheDocument()
    expect(screen.queryByLabelText('Max Concurrent Positions')).not.toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Candidate Selection Mode'), 'trade_score')

    expect(screen.queryByLabelText('Candidate Pool Size (Top N)')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Top N Ranking Metric')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Max Concurrent Positions')).toBeInTheDocument()
  })

  it('auto-syncs horizon when top-n metric uses fixed horizon', async () => {
    const user = userEvent.setup()

    renderWorkbench()

    expect(screen.queryByLabelText('Forecast Horizon (Days)')).not.toBeInTheDocument()

    const metricSelect = screen.getByLabelText('Top N Ranking Metric') as HTMLSelectElement

    await user.selectOptions(metricSelect, 'up_prob_30d')

    await user.selectOptions(screen.getByLabelText('Candidate Selection Mode'), 'trade_score')

    const horizonSelect = screen.getByLabelText('Forecast Horizon (Days)') as HTMLSelectElement
    expect(horizonSelect.value).toBe('30')

    await user.selectOptions(screen.getByLabelText('Candidate Selection Mode'), 'top_n')

    const metricSelectAfterSwitch = screen.getByLabelText('Top N Ranking Metric') as HTMLSelectElement
    await user.selectOptions(metricSelectAfterSwitch, 'trade_score')

    await user.selectOptions(screen.getByLabelText('Candidate Selection Mode'), 'trade_score')
    const horizonSelectAfterSwitch = screen.getByLabelText('Forecast Horizon (Days)') as HTMLSelectElement
    expect(horizonSelectAfterSwitch.value).toBe('30')
  })

  it('loads a previous run config and updates the prefix to rerun id', async () => {
    const user = userEvent.setup()

    renderWorkbench()

    await waitFor(() => {
      const reuseSelect = screen.getByLabelText('Reuse Previous Backtest Config')
      expect(within(reuseSelect).getByRole('option', { name: /84\s+Validation-lightgbm-2023-01-01-2024-12-31/ })).toBeInTheDocument()
    })

    await user.selectOptions(screen.getByLabelText('Reuse Previous Backtest Config'), '84')

    expect((screen.getByLabelText('Run Name Prefix') as HTMLInputElement).value).toBe('rerun#84')
    expect((screen.getByLabelText('Prediction Source') as HTMLSelectElement).value).toBe('lightgbm')
    expect((screen.getByLabelText('Start Date') as HTMLInputElement).value).toBe('2023-01-01')
    expect((screen.getByLabelText('End Date') as HTMLInputElement).value).toBe('2024-12-31')
    expect(screen.queryByLabelText('Compare With Run')).not.toBeInTheDocument()
    expect(screen.getByText('Compare Target: #84 Validation-lightgbm-2023-01-01-2024-12-31')).toBeInTheDocument()
  })

  it('submits the reused run itself as the hidden compare target', async () => {
    const user = userEvent.setup()

    renderWorkbench()

    await waitFor(() => {
      expect(screen.getByLabelText('Reuse Previous Backtest Config')).toBeInTheDocument()
    })

    await user.selectOptions(screen.getByLabelText('Reuse Previous Backtest Config'), '84')
    await user.click(screen.getByRole('button', { name: 'Submit Backtest Jobs' }))

    await waitFor(() => {
      expect(mockCreateBacktestRun).toHaveBeenCalledWith(expect.objectContaining({
        parameters: expect.objectContaining({
          prediction_source: 'lightgbm',
          compare_backtest_run_id: 84,
        }),
      }))
    })
  })

  it('hides trade-score-only config cards for top-n runs in the details view', async () => {
    renderWorkbench()

    await waitFor(() => {
      expect(screen.getByText('Trade Details')).toBeInTheDocument()
    })

    expect(screen.queryByText('Max Concurrent Positions')).not.toBeInTheDocument()
    expect(screen.queryByText('Trade Score Scope')).not.toBeInTheDocument()
    expect(screen.queryByText('Trade Score Threshold')).not.toBeInTheDocument()
  })

  it('formats stored numeric entry weekdays as weekday labels in the details view', async () => {
    renderWorkbench()

    await waitFor(() => {
      expect(screen.getByText('Trade Details')).toBeInTheDocument()
    })

    expect(screen.getAllByText('TUE, THU').length).toBeGreaterThan(0)
    expect(screen.queryByText('1, 3')).not.toBeInTheDocument()
  })

  it('navigates to the dashboard with the reused backtest config encoded in the URL', async () => {
    const user = userEvent.setup()

    renderWorkbench()

    await waitFor(() => {
      const reuseSelect = screen.getByLabelText('Reuse Previous Backtest Config')
      expect(within(reuseSelect).getByRole('option', { name: /84\s+Validation-lightgbm-2023-01-01-2024-12-31/ })).toBeInTheDocument()
    })

    await user.selectOptions(screen.getByLabelText('Reuse Previous Backtest Config'), '84')
    await user.click(screen.getByRole('button', { name: 'Open Dashboard With Current Config' }))

    await waitFor(() => {
      expect(screen.getByTestId('dashboard-target')).toBeInTheDocument()
    })

    const locationText = screen.getByTestId('location-display').textContent ?? ''
    const query = new URLSearchParams(locationText.split('?')[1] ?? '')
    expect(query.get('prediction_source')).toBe('lightgbm')
    expect(query.get('candidate_mode')).toBe('top_n')
    expect(query.get('top_n')).toBe('5')
    expect(query.get('reminder_holding_period_days')).toBe('14')
    expect(query.get('source_run_id')).toBe('84')
  })

  it('renders the comparison chart with official benchmark summary cards', async () => {
    renderWorkbench()

    await waitFor(() => {
      expect(screen.getAllByText('CSI 300').length).toBeGreaterThan(0)
    })

    expect(screen.getByText('Equity Curve Comparison')).toBeInTheDocument()
    expect(screen.getAllByText('CSI 300').length).toBeGreaterThan(0)
    expect(screen.getAllByText('CSI A500').length).toBeGreaterThan(0)
    expect(screen.getByText('Compare Target')).toBeInTheDocument()
    expect(screen.getAllByText('No compare target').length).toBeGreaterThan(0)
  })

  it('shows compare target metadata only when the comparison payload includes it', async () => {
    mockFetchBacktestComparisonCurve.mockResolvedValueOnce({
      run: {
        id: 84,
        name: 'Validation-lightgbm-2023-01-01-2024-12-31',
        status: 'COMPLETED',
        start_date: '2023-01-01',
        end_date: '2024-12-31',
        initial_capital: 200000,
        prediction_source: 'lightgbm',
        compare_backtest_run_id: 52,
      },
      series: [
        {
          key: 'selected_run',
          label: '#84 Validation-lightgbm-2023-01-01-2024-12-31',
          kind: 'backtest',
          run_id: 84,
          prediction_source: 'lightgbm',
          total_return: 0.05,
          max_drawdown: -0.08,
          points: [
            { date: '2023-01-01', value: 200000, drawdown: 0 },
            { date: '2024-12-31', value: 210000, drawdown: -0.02 },
          ],
        },
        {
          key: 'compare_run',
          label: '#52 Previous LightGBM',
          kind: 'backtest',
          run_id: 52,
          prediction_source: 'lightgbm',
          total_return: 0.02,
          max_drawdown: -0.07,
          points: [
            { date: '2023-01-01', value: 200000, drawdown: 0 },
            { date: '2024-12-31', value: 204000, drawdown: -0.04 },
          ],
        },
      ],
      compare_target: {
        id: 52,
        name: 'Previous LightGBM',
        status: 'COMPLETED',
      },
      available_series_keys: ['selected_run', 'compare_run'],
      message: null,
    })

    renderWorkbench()

    await waitFor(() => {
      expect(screen.getAllByText('#52 Previous LightGBM').length).toBeGreaterThan(0)
    })

    expect(screen.getByText('Compare Target')).toBeInTheDocument()
    expect(screen.getAllByText('#52 Previous LightGBM').length).toBeGreaterThan(0)
  })

  it('refetches comparison curves with multiple extra comparison runs', async () => {
    const user = userEvent.setup()

    renderWorkbench()

    await waitFor(() => {
      expect(screen.getByLabelText('Extra Comparison Runs')).toBeInTheDocument()
    })

    const extraRunsSelect = screen.getByLabelText('Extra Comparison Runs')
    expect(mockFetchBacktestComparisonCurve).toHaveBeenCalledWith(84, [])

    await user.selectOptions(extraRunsSelect, ['60', '52'])

    await waitFor(() => {
      expect(mockFetchBacktestComparisonCurve).toHaveBeenLastCalledWith(84, [60, 52])
    })
  })
})
