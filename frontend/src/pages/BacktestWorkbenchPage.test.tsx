import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '../i18n'
import { BacktestWorkbenchPage } from './BacktestWorkbenchPage'

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
        report: {},
        created_at: '2026-04-24T00:00:00Z',
      },
    ]),
    fetchBacktestTrades: vi.fn(async () => []),
    hasAnyAuthCredential: vi.fn(() => true),
  }
})

describe('BacktestWorkbenchPage runner controls', () => {
  beforeEach(() => {
    localStorage.setItem('finance_locale', 'en-US')
  })

  it('toggles mode-scoped controls visibility', async () => {
    const user = userEvent.setup()

    render(
      <I18nProvider>
        <BacktestWorkbenchPage />
      </I18nProvider>,
    )

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

    render(
      <I18nProvider>
        <BacktestWorkbenchPage />
      </I18nProvider>,
    )

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

    render(
      <I18nProvider>
        <BacktestWorkbenchPage />
      </I18nProvider>,
    )

    await waitFor(() => {
      expect(screen.getByRole('option', { name: '#84 Validation-lightgbm-2023-01-01-2024-12-31' })).toBeInTheDocument()
    })

    await user.selectOptions(screen.getByLabelText('Reuse Previous Backtest Config'), '84')

    expect((screen.getByLabelText('Run Name Prefix') as HTMLInputElement).value).toBe('rerun#84')
    expect((screen.getByLabelText('Prediction Source') as HTMLSelectElement).value).toBe('lightgbm')
    expect((screen.getByLabelText('Start Date') as HTMLInputElement).value).toBe('2023-01-01')
    expect((screen.getByLabelText('End Date') as HTMLInputElement).value).toBe('2024-12-31')
  })
})
