import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import { I18nProvider } from '../i18n'
import { resetQueryCache } from '../lib/queryCache'
import { EtfRotationPage } from './EtfRotationPage'

const apiMocks = vi.hoisted(() => ({
  fetchEtfLatestSignal: vi.fn(),
  fetchEtfSignalFreshness: vi.fn(),
  fetchEtfOpenPositions: vi.fn(),
  fetchEtfBacktests: vi.fn(),
  fetchEtfComparisonCurve: vi.fn(),
  fetchEtfUniverse: vi.fn(),
}))

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual('../lib/api')
  return {
    ...actual,
    fetchEtfLatestSignal: apiMocks.fetchEtfLatestSignal,
    fetchEtfSignalFreshness: apiMocks.fetchEtfSignalFreshness,
    fetchEtfOpenPositions: apiMocks.fetchEtfOpenPositions,
    fetchEtfBacktests: apiMocks.fetchEtfBacktests,
    fetchEtfComparisonCurve: apiMocks.fetchEtfComparisonCurve,
    fetchEtfUniverse: apiMocks.fetchEtfUniverse,
  }
})

const signalRow = {
  id: 9001,
  date: '2026-10-20',
  signal_type: 'ROTATION',
  model_type: 'RULE',
  rankings: [
    { ts_code: '512890.SH', name: 'Bank ETF', score: 1.234567, rank: 1, category: 'FINANCIAL' },
  ],
  selected_etfs: [
    { ts_code: '512890.SH', name: 'Bank ETF', weight: 0.4, reason: 'momentum' },
    { ts_code: '510500.SH', name: 'CSI 500 ETF', weight: 0.26, reason: 'momentum' },
  ],
  defensive_allocation: '0.3400',
  defensive_asset: 'CASH',
  model_version: null,
  benchmark_return_20d: '0.0338',
  max_positions: 3,
  metadata: {},
  created_at: '2026-10-20T09:00:00Z',
  updated_at: '2026-10-20T09:00:00Z',
}

const positionRow = {
  id: 441,
  signal: 9001,
  signal_date: '2026-10-20',
  etf: 12,
  etf_ts_code: '512890.SH',
  etf_name: 'Bank ETF',
  entry_date: '2026-10-21',
  exit_date: null,
  entry_price: '1.2340',
  exit_price: null,
  weight: '0.4000',
  shares: 8200,
  pnl: null,
  return_pct: null,
  status: 'OPEN',
  metadata: {},
  created_at: '2026-10-21T09:00:00Z',
}

const backtestRow = {
  id: 82,
  name: 'grid-rule-monthly-p3-mom20-abs-score',
  status: 'COMPLETED',
  model_type: 'RULE',
  benchmark_code: 'H00905.CSI',
  alpha_vs_benchmark: '0.1821',
  start_date: '2024-01-01',
  end_date: '2026-10-20',
  parameters: {},
}

// The shape the backend returns for a row that predates the recompute path: the
// strategy curve plus its benchmark, rescaled to the opening equity.
const comparisonPayload = {
  run: {
    id: 82,
    name: 'grid-rule-monthly-p3-mom20-abs-score',
    status: 'COMPLETED',
    start_date: '2024-01-01',
    end_date: '2026-10-20',
    initial_capital: 480000,
    benchmark_code: 'H00905.CSI',
  },
  series: [
    {
      key: 'selected_run',
      label: 'Strategy Equity',
      kind: 'backtest',
      total_return: 0.7756,
      max_drawdown: -0.19998,
      points: [
        { date: '2024-01-02', value: 480000, drawdown: 0 },
        { date: '2024-01-03', value: 492000, drawdown: 0 },
      ],
    },
    {
      key: 'h00905_csi',
      label: 'CSI 500 Total Return (H00905.CSI)',
      kind: 'benchmark',
      total_return: 0.3268,
      max_drawdown: -0.2472,
      points: [
        { date: '2024-01-02', value: 480000, drawdown: 0 },
        { date: '2024-01-03', value: 486000, drawdown: -0.0041 },
      ],
    },
  ],
  compare_target: null,
  available_series_keys: ['selected_run', 'h00905_csi'],
  message: null,
}

const universeRow = {
  id: 12,
  ts_code: '512890.SH',
  name: 'Bank ETF',
  category: 'FINANCIAL',
  sub_category: 'BANK',
  benchmark: 'H30269.CSI',
  list_date: '2017-08-03',
  effective_list_date: '2017-09-01',
  is_rankable: true,
}

// `signal_date` is the data cut, so a fresh signal is one whose as-of date is already the
// newest eligible snapshot -- lag 0. The stale case is the same object with the lag and
// `is_stale` flipped, which is exactly how the backend computes it.
const freshPayload = {
  signal_date: '2026-10-20',
  latest_snapshot_date: '2026-10-20',
  latest_bar_date: '2026-10-20',
  lag_sessions: 0,
  is_stale: false,
  model_version: 'rule-v1:mom60-score',
}

const stalePayload = {
  ...freshPayload,
  latest_snapshot_date: '2026-10-22',
  latest_bar_date: '2026-10-22',
  lag_sessions: 2,
  is_stale: true,
}

function renderEtfPage() {
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={['/etf']}>
        <Routes>
          <Route path="/etf" element={<EtfRotationPage />} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  )
}

// 'Bank ETF', '40.00%' and the signal date each appear in more than one card, and
// `getByText` throws on an ambiguous match, so every assertion is scoped to the
// card that owns it. A card is a `.card` whose leading child is its `h3`.
async function cardByTitle(title: string): Promise<HTMLElement> {
  const heading = await screen.findByRole('heading', { name: title })
  const card = heading.closest('.card')
  if (!card) {
    throw new Error(`No .card rendered for "${title}"`)
  }
  return card as HTMLElement
}

// Several knobs share a value ('2' is both `max_per_category` and the excluded count), so
// a parameter is read off its own row rather than searched for anywhere in the card.
function paramValue(card: HTMLElement, label: string): string {
  const cell = within(card).getByText(label)
  const tr = cell.closest('tr')
  if (!tr) {
    throw new Error(`No row rendered for parameter "${label}"`)
  }
  const value = within(tr as HTMLElement).getAllByRole('cell')[1]
  return (value?.textContent ?? '').trim()
}

describe('EtfRotationPage', () => {
  beforeEach(() => {
    localStorage.setItem('finance_locale', 'en-US')
    resetQueryCache()
    Object.values(apiMocks).forEach((mock) => mock.mockReset())
    apiMocks.fetchEtfLatestSignal.mockResolvedValue(signalRow)
    apiMocks.fetchEtfSignalFreshness.mockResolvedValue(freshPayload)
    apiMocks.fetchEtfOpenPositions.mockResolvedValue([positionRow])
    apiMocks.fetchEtfBacktests.mockResolvedValue({ count: 1, next: null, previous: null, results: [backtestRow] })
    apiMocks.fetchEtfComparisonCurve.mockResolvedValue(comparisonPayload)
    apiMocks.fetchEtfUniverse.mockResolvedValue({ count: 1, next: null, previous: null, results: [universeRow] })
  })

  it('renders the signal card with percentages built from fractional metrics', async () => {
    renderEtfPage()

    const signalCard = await cardByTitle('Signal of the Day')
    expect(within(signalCard).getByText('RULE')).toBeInTheDocument()
    expect(within(signalCard).getByText('2026-10-20')).toBeInTheDocument()
    expect(within(signalCard).getByText('34.00%')).toBeInTheDocument()
    expect(within(signalCard).getByText('3.38%')).toBeInTheDocument()

    // Selected ETFs renders first, the full ranking second.
    const [selectedTable, rankingTable] = within(signalCard).getAllByRole('table')
    expect(within(selectedTable).getByText('Bank ETF')).toBeInTheDocument()
    expect(within(selectedTable).getByText('40.00%')).toBeInTheDocument()
    expect(within(selectedTable).getByText('26.00%')).toBeInTheDocument()
    expect(within(rankingTable).getByText('1.2346')).toBeInTheDocument()

    const positionsCard = await cardByTitle('Open Positions')
    expect(within(positionsCard).getByText('2026-10-21')).toBeInTheDocument()
    expect(within(positionsCard).getByText('1.234')).toBeInTheDocument()
    expect(within(positionsCard).getByText('40.00%')).toBeInTheDocument()
  })

  it('plots the alpha-leading run against its benchmark without a click', async () => {
    renderEtfPage()

    await waitFor(() => {
      expect(apiMocks.fetchEtfComparisonCurve).toHaveBeenCalledWith(82)
    })
    expect(apiMocks.fetchEtfBacktests).toHaveBeenCalledWith({ limit: 5, sort: 'alpha' })

    const comparisonCard = await cardByTitle('Strategy versus Benchmark')
    // Both series arrive in one payload, so the summary cards and the two lines
    // are driven by the same list; recharts' legend may or may not render in jsdom.
    expect(within(comparisonCard).getAllByText('Strategy Equity').length).toBeGreaterThan(0)
    expect(within(comparisonCard).getAllByText('CSI 500 Total Return (H00905.CSI)').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /18\.21%/ })).toBeInTheDocument()
  })

  it('falls back to the empty state when the latest signal endpoint has nothing to return', async () => {
    // /etf/signals/latest/ answers 404 and the fetcher resolves null, not throws.
    apiMocks.fetchEtfLatestSignal.mockResolvedValue(null)
    renderEtfPage()

    expect(
      await screen.findByText('No rotation signal yet — the daily signal job has not produced one.'),
    ).toBeInTheDocument()
    expect(screen.queryByText('Selected ETFs and Weights')).not.toBeInTheDocument()
  })

  it('switches to the universe tab and asks the server for rankable rows only', async () => {
    const user = userEvent.setup()
    renderEtfPage()

    await user.click(screen.getByRole('button', { name: 'ETF Universe' }))

    await waitFor(() => {
      expect(apiMocks.fetchEtfUniverse).toHaveBeenCalledWith({
        page: 1,
        pageSize: 20,
        search: '',
        isRankable: true,
      })
    })

    const table = await screen.findByRole('table')
    expect(within(table).getByText('H30269.CSI')).toBeInTheDocument()
    expect(within(table).getByText('Yes')).toBeInTheDocument()
    expect(screen.queryByText('Selected ETFs and Weights')).not.toBeInTheDocument()
  })

  it('labels the data as-of date apart from the generated and updated timestamps', async () => {
    apiMocks.fetchEtfLatestSignal.mockResolvedValue({
      ...signalRow,
      created_at: '2026-10-21T02:00:00Z',
      updated_at: '2026-10-22T08:30:00Z',
    })
    renderEtfPage()

    const signalCard = await cardByTitle('Signal of the Day')
    // Timestamps are shown in the browser's own zone, so the expectation formats them the
    // way the page does instead of hardcoding one machine's UTC offset.
    const generatedAt = new Date('2026-10-21T02:00:00Z').toLocaleString()
    const updatedAt = new Date('2026-10-22T08:30:00Z').toLocaleString()
    expect(generatedAt).not.toBe(updatedAt)

    // The as-of date stays the bare session; only the two write times are localised.
    expect(within(signalCard).getByText('Data as-of')).toBeInTheDocument()
    expect(within(signalCard).getByText('2026-10-20')).toBeInTheDocument()
    expect(within(signalCard).getByText('Generated at')).toBeInTheDocument()
    expect(within(signalCard).getByText(generatedAt)).toBeInTheDocument()
    expect(within(signalCard).getByText('Last updated')).toBeInTheDocument()
    expect(within(signalCard).getByText(updatedAt)).toBeInTheDocument()

    // lag 0 is not stale, so the card says nothing about freshness at all.
    expect(within(signalCard).queryByText(/trails the newest eligible feature snapshot/)).not.toBeInTheDocument()
  })

  it('falls back to a dash when the signal row carries no write timestamps', async () => {
    apiMocks.fetchEtfLatestSignal.mockResolvedValue({ ...signalRow, created_at: null, updated_at: undefined })
    renderEtfPage()

    const signalCard = await cardByTitle('Signal of the Day')
    expect(within(signalCard).getAllByText('--')).toHaveLength(2)
    expect(within(signalCard).getByText('2026-10-20')).toBeInTheDocument()
  })

  it('warns on the signal card when the newest snapshot is ahead of the signal', async () => {
    apiMocks.fetchEtfSignalFreshness.mockResolvedValue(stalePayload)
    renderEtfPage()

    const signalCard = await cardByTitle('Signal of the Day')
    const warning = await within(signalCard).findByText(/trails the newest eligible feature snapshot by 2 trading session/)
    // The load-error idiom is the red one: a stale signal is a thing that is broken.
    expect(warning).toHaveClass('status', 'disconnected')
    expect(warning.textContent).toContain('Data as-of 2026-10-20')
    expect(warning.textContent).toContain('newest snapshot 2026-10-22')
  })

  it('renders every knob in metadata.config, including one the frontend has never heard of', async () => {
    apiMocks.fetchEtfLatestSignal.mockResolvedValue({
      ...signalRow,
      model_version: 'rule-v1:mom60-score',
      metadata: {
        model_type: 'RULE',
        momentum_column: 'mom_60',
        weight_method: 'score',
        qualified_count: 14,
        absolute_momentum_excluded_count: 2,
        defensive_reason: 'all_negative_to_defensive',
        model_versions: { rule: 'rule-v1:mom60-score' },
        config: {
          max_positions: 3,
          dedup_track_index: true,
          max_per_category: 2,
          kelly_fraction: 0.125,
          brand_new_backend_knob: 'surprise',
        },
        scoring_metadata: {
          rule_weights: { momentum: 0.5, relative_strength: 0.3, volatility_penalty: 0.2 },
          momentum_column: 'mom_60',
          relative_strength_column: 'rs_60',
          available_momentum_lookbacks: [20, 60, 120],
        },
      },
    })
    renderEtfPage()

    const paramsCard = await cardByTitle('Model and Strategy Parameters')
    expect(within(paramsCard).getByText('RULE')).toBeInTheDocument()
    expect(within(paramsCard).getByText('rule-v1:mom60-score')).toBeInTheDocument()

    // The table walks Object.entries, so a knob added on the backend arrives under the
    // key the backend chose, with no label of its own and no frontend change.
    expect(within(paramsCard).getByText('brand_new_backend_knob')).toBeInTheDocument()
    expect(paramValue(paramsCard, 'brand_new_backend_knob')).toBe('surprise')
    expect(paramValue(paramsCard, 'dedup_track_index')).toBe('Yes')
    expect(paramValue(paramsCard, 'max_positions')).toBe('3')
    expect(paramValue(paramsCard, 'kelly_fraction')).toBe('0.1250')

    expect(paramValue(paramsCard, 'Momentum Weight')).toBe('0.5000')
    expect(paramValue(paramsCard, 'Relative Strength Weight')).toBe('0.3000')
    expect(paramValue(paramsCard, 'Volatility Penalty Weight')).toBe('0.2000')
    expect(paramValue(paramsCard, 'Momentum Column')).toBe('mom_60')
    expect(paramValue(paramsCard, 'Relative Strength Column')).toBe('rs_60')
    expect(paramValue(paramsCard, 'Weight Method')).toBe('score')
    expect(paramValue(paramsCard, 'Qualified Count')).toBe('14')
    expect(paramValue(paramsCard, 'Absolute Momentum Excluded Count')).toBe('2')
    expect(paramValue(paramsCard, 'Defensive Reason')).toBe('all_negative_to_defensive')
    expect(paramValue(paramsCard, 'Available Momentum Lookbacks')).toBe('20, 60, 120')
    expect(paramValue(paramsCard, 'Model Version Tags')).toBe('{"rule":"rule-v1:mom60-score"}')
  })
})
