import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'

import { I18nProvider } from '../i18n'
import {
  createBacktestRun,
  deleteBacktestRun,
  fetchBacktestComparisonCurve,
  fetchBacktestRuns,
  fetchBacktestTrades,
  hasAnyAuthCredential,
  pauseBacktestRun,
  restartBacktestRun,
  resumeBacktestRun,
  type BacktestRunDto,
} from '../lib/api'
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
    pauseBacktestRun: vi.fn(async (runId: number) => ({
      id: runId,
      message: 'Backtest pause requested. It will pause after the current chunk.',
    })),
    resumeBacktestRun: vi.fn(async (runId: number) => ({
      id: runId,
      message: 'Backtest resume queued.',
    })),
    restartBacktestRun: vi.fn(async (runId: number) => ({
      id: runId,
      message: 'Backtest restart queued.',
    })),
    deleteBacktestRun: vi.fn(async () => null),
    fetchBacktestRuns: vi.fn(async () => [
      {
        id: 84,
        name: 'Validation-lightgbm-2023-01-01-2024-12-31',
        strategy_type: 'PREDICTION_THRESHOLD',
        status: 'COMPLETED',
        pending_control_action: 'NONE',
        task_state: '',
        has_stale_task_owner: false,
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
          prediction_source: 'lightgbm',
          entry_weekdays: [1, 3],
          holding_period_days: 14,
        },
        error_message: '',
        started_at: '2026-04-24T00:00:00Z',
        completed_at: '2026-04-24T00:30:00Z',
        created_at: '2026-04-24T00:00:00Z',
      },
      {
        id: 77,
        name: 'Running Control Run',
        strategy_type: 'PREDICTION_THRESHOLD',
        status: 'RUNNING',
        pending_control_action: 'NONE',
        task_state: 'PENDING',
        has_stale_task_owner: false,
        start_date: '2023-06-01',
        end_date: '2024-06-01',
        initial_capital: 190000,
        final_value: null,
        total_return: null,
        annualized_return: null,
        max_drawdown: null,
        sharpe_ratio: null,
        win_rate: null,
        total_trades: 0,
        winning_trades: 0,
        parameters: {
          prediction_source: 'heuristic',
          top_n: 4,
          horizon_days: 7,
          up_threshold: 0.48,
          entry_weekdays: ['TUE', 'THU'],
          holding_period_days: 10,
          capital_fraction_per_entry: 0.2,
          candidate_mode: 'top_n',
          top_n_metric: 'up_prob_7d',
          trade_score_scope: 'independent',
          trade_score_threshold: 1,
          max_positions: 4,
          use_macro_context: true,
          enable_stop_target_exit: true,
        },
        report: {
          prediction_source: 'heuristic',
          entry_weekdays: [1, 3],
          holding_period_days: 10,
          progress: {
            processed_trading_days: 3,
            total_trading_days: 12,
          },
        },
        error_message: '',
        started_at: '2026-04-23T12:00:00Z',
        completed_at: null,
        created_at: '2026-04-23T12:00:00Z',
      },
      {
        id: 70,
        name: 'Paused Control Run',
        strategy_type: 'PREDICTION_THRESHOLD',
        status: 'PAUSED',
        pending_control_action: 'NONE',
        task_state: '',
        has_stale_task_owner: false,
        start_date: '2023-05-01',
        end_date: '2024-05-01',
        initial_capital: 175000,
        final_value: null,
        total_return: null,
        annualized_return: null,
        max_drawdown: null,
        sharpe_ratio: null,
        win_rate: null,
        total_trades: 0,
        winning_trades: 0,
        parameters: {
          prediction_source: 'heuristic',
          top_n: 3,
          horizon_days: 7,
          up_threshold: 0.46,
          entry_weekdays: ['MON', 'WED'],
          holding_period_days: 8,
          capital_fraction_per_entry: 0.15,
          candidate_mode: 'top_n',
          top_n_metric: 'up_prob_7d',
          trade_score_scope: 'independent',
          trade_score_threshold: 1,
          max_positions: 3,
          use_macro_context: true,
          enable_stop_target_exit: true,
        },
        report: {
          prediction_source: 'heuristic',
          entry_weekdays: [0, 2],
          holding_period_days: 8,
        },
        error_message: 'Paused after the current chunk.',
        started_at: '2026-04-22T10:00:00Z',
        completed_at: null,
        created_at: '2026-04-22T10:00:00Z',
      },
      {
        id: 60,
        name: 'Validation-lstm-2023-01-01-2024-12-31',
        strategy_type: 'PREDICTION_THRESHOLD',
        status: 'COMPLETED',
        pending_control_action: 'NONE',
        task_state: '',
        has_stale_task_owner: false,
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
        error_message: '',
        started_at: '2026-04-23T00:00:00Z',
        completed_at: '2026-04-23T00:30:00Z',
        created_at: '2026-04-23T00:00:00Z',
      },
      {
        id: 52,
        name: 'Previous LightGBM',
        strategy_type: 'PREDICTION_THRESHOLD',
        status: 'COMPLETED',
        pending_control_action: 'NONE',
        task_state: '',
        has_stale_task_owner: false,
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
        error_message: '',
        started_at: '2026-04-22T00:00:00Z',
        completed_at: '2026-04-22T00:30:00Z',
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
const mockFetchBacktestRuns = vi.mocked(fetchBacktestRuns)
const mockFetchBacktestTrades = vi.mocked(fetchBacktestTrades)
const mockCreateBacktestRun = vi.mocked(createBacktestRun)
const mockHasAnyAuthCredential = vi.mocked(hasAnyAuthCredential)
const mockPauseBacktestRun = vi.mocked(pauseBacktestRun)
const mockResumeBacktestRun = vi.mocked(resumeBacktestRun)
const mockRestartBacktestRun = vi.mocked(restartBacktestRun)
const mockDeleteBacktestRun = vi.mocked(deleteBacktestRun)

function buildBacktestRun(overrides: Partial<BacktestRunDto> & Pick<BacktestRunDto, 'id' | 'name' | 'status'>): BacktestRunDto {
  return {
    id: overrides.id,
    name: overrides.name,
    strategy_type: 'PREDICTION_THRESHOLD',
    status: overrides.status,
    pending_control_action: 'NONE',
    task_state: '',
    has_stale_task_owner: false,
    start_date: '2023-01-01',
    end_date: '2024-12-31',
    initial_capital: 200000,
    final_value: overrides.status === 'COMPLETED' ? 210000 : null,
    total_return: overrides.status === 'COMPLETED' ? 0.05 : null,
    annualized_return: overrides.status === 'COMPLETED' ? 0.03 : null,
    max_drawdown: overrides.status === 'COMPLETED' ? -0.08 : null,
    sharpe_ratio: overrides.status === 'COMPLETED' ? 1.2 : null,
    win_rate: overrides.status === 'COMPLETED' ? 0.55 : null,
    total_trades: overrides.status === 'COMPLETED' ? 10 : 0,
    winning_trades: overrides.status === 'COMPLETED' ? 6 : 0,
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
      prediction_source: 'lightgbm',
      entry_weekdays: [1, 3],
      holding_period_days: 14,
    },
    error_message: '',
    started_at: overrides.status === 'PENDING' ? null : '2026-04-24T00:00:00Z',
    completed_at: overrides.status === 'COMPLETED' ? '2026-04-24T00:30:00Z' : null,
    created_at: '2026-04-24T00:00:00Z',
    ...overrides,
  }
}

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
    vi.useRealTimers()
    mockHasAnyAuthCredential.mockReturnValue(true)
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
      const reuseSelect = screen.getByLabelText('Backtest Rerun')
      expect(within(reuseSelect).getByRole('option', { name: /84\s+Validation-lightgbm-2023-01-01-2024-12-31/ })).toBeInTheDocument()
    })

    await user.selectOptions(screen.getByLabelText('Backtest Rerun'), '84')

    expect((screen.getByLabelText('Run Name Prefix') as HTMLInputElement).value).toBe('rerun#84')
    expect((screen.getByLabelText('Prediction Source') as HTMLSelectElement).value).toBe('lightgbm')
    expect((screen.getByLabelText('Start Date') as HTMLInputElement).value).toBe('2023-01-01')
    expect((screen.getByLabelText('End Date') as HTMLInputElement).value).toBe('2024-12-31')
    expect(screen.queryByLabelText('Compare With Run')).not.toBeInTheDocument()
    expect(screen.getByText('Compare Target: #84 Validation-lightgbm-2023-01-01-2024-12-31')).toBeInTheDocument()
  })

  it('does not render the runner weekday selector for new submissions', async () => {
    const view = renderWorkbench()

    await waitFor(() => {
      expect(screen.getByText('Backtest Runner')).toBeInTheDocument()
    })

    expect(view.container.querySelector('.runner-weekdays')).toBeNull()
  })

  it('submits the reused run itself as the hidden compare target', async () => {
    const user = userEvent.setup()

    renderWorkbench()

    await waitFor(() => {
      expect(screen.getByLabelText('Backtest Rerun')).toBeInTheDocument()
    })

    await user.selectOptions(screen.getByLabelText('Backtest Rerun'), '84')
    await user.click(screen.getByRole('button', { name: 'Submit Backtest Jobs' }))

    await waitFor(() => {
      expect(mockCreateBacktestRun).toHaveBeenCalledWith(expect.objectContaining({
        parameters: expect.objectContaining({
          prediction_source: 'lightgbm',
          compare_backtest_run_id: 84,
        }),
      }))
    })

    const payload = mockCreateBacktestRun.mock.calls[0]?.[0]
    expect(payload?.parameters).not.toHaveProperty('entry_weekdays')
  })

  it('pauses a running run from the table actions', async () => {
    const user = userEvent.setup()

    renderWorkbench()

    await waitFor(() => {
      expect(screen.getByText('Running Control Run')).toBeInTheDocument()
    })

    const row = screen.getByText('Running Control Run').closest('tr')
    expect(row).not.toBeNull()

    await user.click(within(row as HTMLTableRowElement).getByRole('button', { name: 'Pause' }))

    await waitFor(() => {
      expect(mockPauseBacktestRun).toHaveBeenCalledWith(77)
    })

    expect(screen.getByText('Backtest pause requested. It will pause after the current chunk.')).toBeInTheDocument()
  })

  it('shows trading-day completion percent for running rows', async () => {
    renderWorkbench()

    await waitFor(() => {
      expect(screen.getByText('Running Control Run')).toBeInTheDocument()
    })

    const row = screen.getByText('Running Control Run').closest('tr')
    expect(row).not.toBeNull()
    expect(within(row as HTMLTableRowElement).getByText('RUNNING (25%)')).toBeInTheDocument()
  })

  it('resumes a paused run from the table actions', async () => {
    const user = userEvent.setup()

    renderWorkbench()

    await waitFor(() => {
      expect(screen.getByText('Paused Control Run')).toBeInTheDocument()
    })

    const row = screen.getByText('Paused Control Run').closest('tr')
    expect(row).not.toBeNull()

    await user.click(within(row as HTMLTableRowElement).getByRole('button', { name: 'Resume' }))

    await waitFor(() => {
      expect(mockResumeBacktestRun).toHaveBeenCalledWith(70)
    })

    expect(screen.getByText('Backtest resume queued.')).toBeInTheDocument()
  })

  it('restarts a run after confirmation', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderWorkbench()

    await waitFor(() => {
      expect(screen.getByText('Validation-lightgbm-2023-01-01-2024-12-31')).toBeInTheDocument()
    })

    const row = screen.getByText('Validation-lightgbm-2023-01-01-2024-12-31').closest('tr')
    expect(row).not.toBeNull()

    await user.click(within(row as HTMLTableRowElement).getByRole('button', { name: 'Restart' }))

    await waitFor(() => {
      expect(confirmSpy).toHaveBeenCalledWith('Restart #84 Validation-lightgbm-2023-01-01-2024-12-31? This clears the existing trades and metrics.')
      expect(mockRestartBacktestRun).toHaveBeenCalledWith(84)
    })

    expect(screen.getByText('Backtest restart queued.')).toBeInTheDocument()
    confirmSpy.mockRestore()
  })

  it('removes a run even when restart is already pending', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const restartingRun = buildBacktestRun({
      id: 623,
      name: 'Restart Pending Run',
      status: 'RUNNING',
      pending_control_action: 'RESTART',
      final_value: null,
      total_return: null,
      annualized_return: null,
      max_drawdown: null,
      sharpe_ratio: null,
      win_rate: null,
      total_trades: 0,
      winning_trades: 0,
    })

    mockFetchBacktestRuns.mockResolvedValueOnce([restartingRun]).mockResolvedValueOnce([])
    mockDeleteBacktestRun.mockResolvedValueOnce({
      id: 623,
      message: 'Backtest removal requested. It will be removed after the current chunk.',
    })

    renderWorkbench()

    await waitFor(() => {
      expect(screen.getByText('Restart Pending Run')).toBeInTheDocument()
    })

    const row = screen.getByText('Restart Pending Run').closest('tr')
    expect(row).not.toBeNull()

    const removeButton = within(row as HTMLTableRowElement).getByRole('button', { name: 'Remove' })
    expect(removeButton).toBeEnabled()

    await user.click(removeButton)

    await waitFor(() => {
      expect(confirmSpy).toHaveBeenCalledWith('Remove #623 Restart Pending Run? This deletes the run list entry and stored data.')
      expect(mockDeleteBacktestRun).toHaveBeenCalledWith(623)
    })

    expect(screen.getByText('Backtest removal requested. It will be removed after the current chunk.')).toBeInTheDocument()
    confirmSpy.mockRestore()
  })

  it('shows dead status and allows removing a stale running row', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const staleRun = buildBacktestRun({
      id: 623,
      name: 'Stale Delete Run',
      status: 'RUNNING',
      pending_control_action: 'DELETE',
      task_state: 'PENDING',
      has_stale_task_owner: true,
      final_value: null,
      total_return: null,
      annualized_return: null,
      max_drawdown: null,
      sharpe_ratio: null,
      win_rate: null,
      total_trades: 0,
      winning_trades: 0,
    })

    mockFetchBacktestRuns.mockResolvedValueOnce([staleRun]).mockResolvedValueOnce([])
    mockDeleteBacktestRun.mockResolvedValueOnce(null)

    renderWorkbench()

    await waitFor(() => {
      expect(screen.getByText('Stale Delete Run')).toBeInTheDocument()
    })

    const row = screen.getByText('Stale Delete Run').closest('tr')
    expect(row).not.toBeNull()
    expect(within(row as HTMLTableRowElement).getByText('RUNNING (dead · Removal requested)')).toBeInTheDocument()
    expect(within(row as HTMLTableRowElement).getByText('Dead task owner detected · Removal requested')).toBeInTheDocument()

    const removeButton = within(row as HTMLTableRowElement).getByRole('button', { name: 'Remove' })
    expect(removeButton).toBeEnabled()

    await user.click(removeButton)

    await waitFor(() => {
      expect(confirmSpy).toHaveBeenCalledWith('Remove #623 Stale Delete Run? This deletes the run list entry and stored data.')
      expect(mockDeleteBacktestRun).toHaveBeenCalledWith(623)
    })

    expect(screen.getByText('Removed backtest #623 Stale Delete Run.')).toBeInTheDocument()
    confirmSpy.mockRestore()
  })

  it('auto-refreshes the run list and selected trade list', async () => {
    const intervalCallbacks: Array<() => void> = []
    const setIntervalSpy = vi.spyOn(window, 'setInterval').mockImplementation(((handler: TimerHandler) => {
      if (typeof handler === 'function') {
        intervalCallbacks.push(handler as () => void)
      }
      return intervalCallbacks.length as unknown as number
    }) as typeof window.setInterval)
    const clearIntervalSpy = vi.spyOn(window, 'clearInterval').mockImplementation(() => {})

    try {
      mockFetchBacktestRuns.mockResolvedValueOnce([
        buildBacktestRun({ id: 84, name: 'Validation-lightgbm-2023-01-01-2024-12-31', status: 'COMPLETED' }),
        buildBacktestRun({
          id: 77,
          name: 'Running Control Run',
          status: 'RUNNING',
          report: {
            prediction_source: 'heuristic',
            entry_weekdays: [1, 3],
            holding_period_days: 10,
            progress: {
              processed_trading_days: 3,
              total_trading_days: 12,
            },
          },
          parameters: {
            prediction_source: 'heuristic',
            top_n: 4,
            horizon_days: 7,
            up_threshold: 0.48,
            entry_weekdays: ['TUE', 'THU'],
            holding_period_days: 10,
            capital_fraction_per_entry: 0.2,
            candidate_mode: 'top_n',
            top_n_metric: 'up_prob_7d',
            trade_score_scope: 'independent',
            trade_score_threshold: 1,
            max_positions: 4,
            use_macro_context: true,
            enable_stop_target_exit: true,
          },
        }),
      ]).mockResolvedValueOnce([
        buildBacktestRun({ id: 84, name: 'Validation-lightgbm-2023-01-01-2024-12-31', status: 'COMPLETED' }),
        buildBacktestRun({
          id: 77,
          name: 'Running Control Run',
          status: 'RUNNING',
          report: {
            prediction_source: 'heuristic',
            entry_weekdays: [1, 3],
            holding_period_days: 10,
            progress: {
              processed_trading_days: 3,
              total_trading_days: 12,
            },
          },
          parameters: {
            prediction_source: 'heuristic',
            top_n: 4,
            horizon_days: 7,
            up_threshold: 0.48,
            entry_weekdays: ['TUE', 'THU'],
            holding_period_days: 10,
            capital_fraction_per_entry: 0.2,
            candidate_mode: 'top_n',
            top_n_metric: 'up_prob_7d',
            trade_score_scope: 'independent',
            trade_score_threshold: 1,
            max_positions: 4,
            use_macro_context: true,
            enable_stop_target_exit: true,
          },
        }),
      ])
      mockFetchBacktestTrades.mockResolvedValueOnce([]).mockResolvedValueOnce([])

      renderWorkbench()

      await waitFor(() => {
        expect(screen.getByText('Validation-lightgbm-2023-01-01-2024-12-31')).toBeInTheDocument()
      })

      await waitFor(() => {
        expect(mockFetchBacktestRuns).toHaveBeenCalledTimes(1)
        expect(mockFetchBacktestTrades).toHaveBeenCalledTimes(1)
        expect(mockFetchBacktestTrades).toHaveBeenCalledWith(84)
        expect(mockFetchBacktestComparisonCurve).toHaveBeenCalledTimes(1)
        expect(mockFetchBacktestComparisonCurve).toHaveBeenCalledWith(84, [])
      })

      expect(intervalCallbacks.length).toBeGreaterThanOrEqual(2)

      for (const callback of intervalCallbacks) {
        callback()
      }

      await waitFor(() => {
        expect(mockFetchBacktestRuns).toHaveBeenCalledTimes(2)
        expect(mockFetchBacktestTrades).toHaveBeenCalledTimes(2)
        expect(mockFetchBacktestComparisonCurve).toHaveBeenCalledTimes(1)
      })
    } finally {
      setIntervalSpy.mockRestore()
      clearIntervalSpy.mockRestore()
    }
  })

  it('stops background polling when auth credentials are unavailable', async () => {
    const intervalCallbacks: Array<() => void> = []
    const setIntervalSpy = vi.spyOn(window, 'setInterval').mockImplementation(((handler: TimerHandler) => {
      if (typeof handler === 'function') {
        intervalCallbacks.push(handler as () => void)
      }
      return intervalCallbacks.length as unknown as number
    }) as typeof window.setInterval)
    const clearIntervalSpy = vi.spyOn(window, 'clearInterval').mockImplementation(() => {})

    try {
      mockFetchBacktestRuns.mockResolvedValueOnce([
        buildBacktestRun({ id: 84, name: 'Validation-lightgbm-2023-01-01-2024-12-31', status: 'COMPLETED' }),
      ])
      mockFetchBacktestTrades.mockResolvedValueOnce([])

      renderWorkbench()

      await waitFor(() => {
        expect(mockFetchBacktestRuns).toHaveBeenCalledTimes(1)
        expect(mockFetchBacktestTrades).toHaveBeenCalledTimes(1)
      })

      mockHasAnyAuthCredential.mockReturnValue(false)

      for (const callback of intervalCallbacks) {
        callback()
      }

      await new Promise((resolve) => setTimeout(resolve, 0))

      expect(mockFetchBacktestRuns).toHaveBeenCalledTimes(1)
      expect(mockFetchBacktestTrades).toHaveBeenCalledTimes(1)
    } finally {
      setIntervalSpy.mockRestore()
      clearIntervalSpy.mockRestore()
    }
  })

  it('removes a run and selects the next available row after refresh', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const initialRuns = [
      buildBacktestRun({ id: 84, name: 'Validation-lightgbm-2023-01-01-2024-12-31', status: 'COMPLETED' }),
      buildBacktestRun({
        id: 77,
        name: 'Running Control Run',
        status: 'RUNNING',
        parameters: {
          prediction_source: 'heuristic',
          top_n: 4,
          horizon_days: 7,
          up_threshold: 0.48,
          entry_weekdays: ['TUE', 'THU'],
          holding_period_days: 10,
          capital_fraction_per_entry: 0.2,
          candidate_mode: 'top_n',
          top_n_metric: 'up_prob_7d',
          trade_score_scope: 'independent',
          trade_score_threshold: 1,
          max_positions: 4,
          use_macro_context: true,
          enable_stop_target_exit: true,
        },
        report: {
          prediction_source: 'heuristic',
          entry_weekdays: [1, 3],
          holding_period_days: 10,
        },
      }),
    ]
    const remainingRuns = initialRuns.filter((run) => run.id !== 84)

    mockFetchBacktestRuns.mockResolvedValueOnce(initialRuns).mockResolvedValueOnce(remainingRuns)
    mockDeleteBacktestRun.mockResolvedValueOnce(null)

    renderWorkbench()

    await waitFor(() => {
      expect(screen.getByText('Validation-lightgbm-2023-01-01-2024-12-31')).toBeInTheDocument()
    })

    const row = screen.getByText('Validation-lightgbm-2023-01-01-2024-12-31').closest('tr')
    expect(row).not.toBeNull()

    await user.click(within(row as HTMLTableRowElement).getByRole('button', { name: 'Remove' }))

    await waitFor(() => {
      expect(mockDeleteBacktestRun).toHaveBeenCalledWith(84)
    })

    await waitFor(() => {
      const replacementRow = screen.getByText('Running Control Run').closest('tr')
      expect(replacementRow).not.toBeNull()
      expect(replacementRow).toHaveClass('row-selected')
    })

    expect(screen.getByText('Removed backtest #84 Validation-lightgbm-2023-01-01-2024-12-31.')).toBeInTheDocument()
    confirmSpy.mockRestore()
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

  it('shows all trading days when a run does not store entry weekdays', async () => {
    mockFetchBacktestRuns.mockResolvedValueOnce([
      buildBacktestRun({
        id: 91,
        name: 'All Trading Days Run',
        status: 'COMPLETED',
        parameters: {
          prediction_source: 'lightgbm',
          top_n: 5,
          horizon_days: 7,
          up_threshold: 0.5,
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
          prediction_source: 'lightgbm',
          holding_period_days: 14,
        },
      }),
    ])

    renderWorkbench()

    await waitFor(() => {
      expect(screen.getByText('Trade Details')).toBeInTheDocument()
    })

    expect(screen.getAllByText('All trading days').length).toBeGreaterThan(0)
  })

  it('shows count-aware win rate and hh:mm:ss runtime in trade details without the removed summary cards', async () => {
    renderWorkbench()

    await waitFor(() => {
      expect(screen.getByText('Trade Details')).toBeInTheDocument()
    })

    const tradeDetailsCard = screen.getByText('Trade Details').closest('.card')
    expect(tradeDetailsCard).not.toBeNull()

    expect(screen.queryByText('Selected Run')).not.toBeInTheDocument()
    expect(screen.getByText('55.00% 6/10')).toBeInTheDocument()
    expect(screen.getByText('00:30:00')).toBeInTheDocument()
    expect(within(tradeDetailsCard as HTMLElement).queryByText('Winning Trades')).not.toBeInTheDocument()
    expect(within(tradeDetailsCard as HTMLElement).queryByText('Closed Trades')).not.toBeInTheDocument()
  })

  it('navigates to the dashboard with the reused backtest config encoded in the URL', async () => {
    const user = userEvent.setup()

    renderWorkbench()

    await waitFor(() => {
      const reuseSelect = screen.getByLabelText('Backtest Rerun')
      expect(within(reuseSelect).getByRole('option', { name: /84\s+Validation-lightgbm-2023-01-01-2024-12-31/ })).toBeInTheDocument()
    })

    await user.selectOptions(screen.getByLabelText('Backtest Rerun'), '84')
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
