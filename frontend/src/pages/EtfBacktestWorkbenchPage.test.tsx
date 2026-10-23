import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import { ApiRequestError, type EtfBacktestDto, type EtfDefensiveAssetDto } from '../lib/api'
import { I18nProvider } from '../i18n'
import { resetQueryCache } from '../lib/queryCache'
import { EtfBacktestWorkbenchPage } from './EtfBacktestWorkbenchPage'

const apiMocks = vi.hoisted(() => ({
  fetchEtfBacktests: vi.fn(),
  fetchEtfBacktest: vi.fn(),
  createEtfBacktest: vi.fn(),
  pauseEtfBacktest: vi.fn(),
  resumeEtfBacktest: vi.fn(),
  restartEtfBacktest: vi.fn(),
  deleteEtfBacktest: vi.fn(),
  fetchEtfComparisonCurve: vi.fn(),
  fetchEtfDefensiveAssets: vi.fn(),
}))

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual('../lib/api')
  return {
    ...actual,
    fetchEtfBacktests: apiMocks.fetchEtfBacktests,
    fetchEtfBacktest: apiMocks.fetchEtfBacktest,
    createEtfBacktest: apiMocks.createEtfBacktest,
    pauseEtfBacktest: apiMocks.pauseEtfBacktest,
    resumeEtfBacktest: apiMocks.resumeEtfBacktest,
    restartEtfBacktest: apiMocks.restartEtfBacktest,
    deleteEtfBacktest: apiMocks.deleteEtfBacktest,
    fetchEtfComparisonCurve: apiMocks.fetchEtfComparisonCurve,
    fetchEtfDefensiveAssets: apiMocks.fetchEtfDefensiveAssets,
  }
})

const ROUTE = '/etf/backtest'
const POLL_INTERVAL_MS = 5_000

function makeRun(overrides: Partial<EtfBacktestDto>): EtfBacktestDto {
  return {
    id: 82,
    name: 'grid-rule-monthly-p5-mom60-abs-score',
    status: 'COMPLETED',
    pending_control_action: 'NONE',
    task_state: '',
    has_stale_task_owner: false,
    start_date: '2024-01-01',
    end_date: '2026-10-20',
    rebalance_frequency: 'MONTHLY',
    max_positions: 5,
    momentum_lookback: 60,
    defensive_asset: '',
    absolute_momentum_filter: true,
    weighting: 'score',
    initial_capital: 480000,
    commission_rate: '0.0003',
    min_commission: '5',
    slippage: '0.001',
    liquidity_floor_multiple: '1',
    model_type: 'RULE',
    walk_forward_steps: 0,
    benchmark_code: 'H00905.CSI',
    total_return: '0.7756',
    annualized_return: '0.2014',
    max_drawdown: '-0.1998',
    sharpe_ratio: '0.9080',
    calmar_ratio: '1.0060',
    win_rate: '0.5625',
    turnover_annual: '3.1200',
    alpha_vs_benchmark: '0.1821',
    beta_vs_benchmark: '0.6070',
    information_ratio: '0.4580',
    benchmark_return: '0.3268',
    total_fees: '12345.67',
    parameters: { weighting: 'score' },
    error_message: '',
    started_at: '2026-10-20T09:00:00Z',
    completed_at: '2026-10-20T11:00:00Z',
    created_at: '2026-10-20T08:55:00Z',
    updated_at: '2026-10-20T11:00:00Z',
    ...overrides,
  }
}

// One terminal row whose metrics are computed, one RUNNING row whose only progress
// is the chunk cursor the engine writes into `report`.
function seedRows(): EtfBacktestDto[] {
  return [
    makeRun({}),
    makeRun({
      id: 83,
      name: 'grid-rule-weekly-p3-mom20-abs-equal',
      status: 'RUNNING',
      task_state: 'STARTED',
      rebalance_frequency: 'WEEKLY',
      max_positions: 3,
      momentum_lookback: 20,
      weighting: 'equal',
      total_return: null,
      annualized_return: null,
      max_drawdown: null,
      sharpe_ratio: null,
      calmar_ratio: null,
      win_rate: null,
      turnover_annual: null,
      alpha_vs_benchmark: null,
      beta_vs_benchmark: null,
      information_ratio: null,
      benchmark_return: null,
      total_fees: null,
      parameters: { weighting: 'equal' },
      completed_at: null,
      report: { runtime_state: { cursor: 120 }, status_note: 'chunked run in progress: 120/641 sessions' },
    }),
  ]
}

const curvePayload = {
  run: {
    id: 82,
    name: 'grid-rule-monthly-p5-mom60-abs-score',
    status: 'COMPLETED',
    start_date: '2024-01-01',
    end_date: '2026-10-20',
    initial_capital: 480000,
    model_type: 'RULE',
    benchmark_code: 'H00905.CSI',
  },
  series: [
    {
      key: 'selected_run',
      label: 'Strategy Equity',
      kind: 'backtest',
      total_return: 0.7756,
      max_drawdown: -0.1998,
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

// Two rankable money-market ETFs whose turnover differs by four orders of magnitude: one
// is the defensive sleeve everybody uses, the other is the trap the dropdown exists to
// expose. The endpoint sorts by descending turnover, so this is the order it renders in.
const defensiveAssets: EtfDefensiveAssetDto[] = [
  {
    ts_code: '511880.SH',
    name: 'Money Market ETF',
    avg_amount_20d: 27583000000,
    eligible: true,
    min_avg_amount_yuan: 30000000,
  },
  {
    ts_code: '511670.SH',
    name: 'Thin Money Market ETF',
    avg_amount_20d: 1000000,
    eligible: false,
    min_avg_amount_yuan: 30000000,
  },
]

/**
 * The rows this page reads, mutated in place by the action mocks.
 *
 * Button gating has to follow the same state machine the server enforces, so an
 * accepted pause is not a fixed string in these tests -- it flips the row to
 * `pending_control_action=PAUSE`, which is what disables Pause and Restart on the
 * next repaint. Each fetch hands out a copy, the way a refetch would.
 */
let rows: EtfBacktestDto[] = seedRows()

function row(id: number): EtfBacktestDto {
  const found = rows.find((item) => item.id === id)
  if (!found) throw new Error(`No seeded run with id ${id}`)
  return found
}

// The list serializer drops the three bulk fields, so anything read from `report`
// is unreachable until the row is retrieved by id.
function listProjection(run: EtfBacktestDto): EtfBacktestDto {
  const copy = { ...run }
  delete copy.report
  delete copy.equity_curve
  delete copy.trade_log
  return copy
}

function advanceCursor(id: number, cursor: number): void {
  const target = row(id)
  target.report = {
    ...target.report,
    runtime_state: { cursor },
    status_note: `chunked run in progress: ${cursor}/641 sessions`,
  }
}

interface PollTimer {
  id: number
  callback: () => void
  delay: number
  cleared: boolean
}

const pollTimers: PollTimer[] = []
let nextTimerId = 0

/**
 * Record the detail poll instead of faking the clock.
 *
 * `vi.useFakeTimers()` needs the `advanceTimers` config to let RTL's `waitFor` move
 * time, and this project's vitest has no globals and no such config, so a faked clock
 * would stall every assertion here. Spying on `window.setInterval` is enough: the page
 * only asks for one repeating timer, and the test fires its callback by hand.
 */
function installPollTimerSpies(): void {
  vi.spyOn(window, 'setInterval').mockImplementation((handler, timeout) => {
    const id = ++nextTimerId
    pollTimers.push({ id, callback: handler as unknown as () => void, delay: timeout ?? 0, cleared: false })
    // The recorded id is what the cleanup receives, so returning the real handle would
    // leave every timer looking live. Nothing goes on the clock: the test fires the
    // callback by hand.
    return id as unknown as ReturnType<typeof window.setInterval>
  })
  vi.spyOn(window, 'clearInterval').mockImplementation((handle) => {
    const timer = pollTimers.find((item) => item.id === handle)
    if (timer) timer.cleared = true
  })
}

function livePollTimers(): PollTimer[] {
  return pollTimers.filter((timer) => timer.delay === POLL_INTERVAL_MS && !timer.cleared)
}

async function firePoll(): Promise<void> {
  const timer = livePollTimers()[0]
  if (!timer) throw new Error('The detail panel scheduled no 5s poll')
  await act(async () => {
    timer.callback()
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 0)
    })
  })
}

async function renderWorkbench(): Promise<{ listCard: HTMLElement; createCard: HTMLElement }> {
  render(
    <I18nProvider>
      <MemoryRouter initialEntries={[ROUTE]}>
        <Routes>
          <Route path={ROUTE} element={<EtfBacktestWorkbenchPage />} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  )
  const createCard = await cardByTitle('Launch an ETF Backtest')
  const listCard = await cardByTitle('Backtest Runs')
  return { listCard, createCard }
}

// 'Status', 'Config' and 'Refresh' each appear in more than one card once a row is
// selected, so every assertion is scoped to the card that owns it.
async function cardByTitle(title: string | RegExp): Promise<HTMLElement> {
  const heading = await screen.findByRole('heading', { name: title })
  const card = heading.closest('.card')
  if (!card) {
    throw new Error(`No .card rendered for "${String(title)}"`)
  }
  return card as HTMLElement
}

async function rowById(id: number): Promise<HTMLElement> {
  const cell = await screen.findByText(`#${id}`)
  const tr = cell.closest('tr')
  if (!tr) {
    throw new Error(`No table row rendered for run #${id}`)
  }
  return tr as HTMLElement
}

describe('EtfBacktestWorkbenchPage', () => {
  beforeEach(() => {
    localStorage.setItem('finance_locale', 'en-US')
    resetQueryCache()
    Object.values(apiMocks).forEach((mock) => mock.mockReset())
    pollTimers.length = 0
    rows = seedRows()

    apiMocks.fetchEtfBacktests.mockImplementation(() =>
      Promise.resolve({ count: rows.length, next: null, previous: null, results: rows.map(listProjection) }),
    )
    apiMocks.fetchEtfBacktest.mockImplementation((runId: number) => Promise.resolve({ ...row(runId) }))
    apiMocks.fetchEtfComparisonCurve.mockImplementation(() => Promise.resolve(curvePayload))
    apiMocks.createEtfBacktest.mockImplementation(() => Promise.resolve(null))
    apiMocks.fetchEtfDefensiveAssets.mockResolvedValue(defensiveAssets)

    apiMocks.pauseEtfBacktest.mockImplementation((runId: number) => {
      const target = row(runId)
      if (target.status !== 'PENDING' && target.status !== 'RUNNING') {
        return Promise.reject(
          new ApiRequestError('Bad Request', 400, 'Only pending or running ETF backtests can be paused.'),
        )
      }
      if (target.status === 'PENDING' || target.has_stale_task_owner) {
        target.status = 'PAUSED'
        target.pending_control_action = 'NONE'
        target.task_state = ''
        return Promise.resolve({ id: runId, message: 'ETF backtest paused.' })
      }
      target.pending_control_action = 'PAUSE'
      return Promise.resolve({
        id: runId,
        message: 'ETF backtest pause requested. It will pause after the current chunk.',
      })
    })

    apiMocks.resumeEtfBacktest.mockImplementation((runId: number) => {
      const target = row(runId)
      if (target.status !== 'PAUSED') {
        return Promise.reject(
          new ApiRequestError('Bad Request', 400, 'Only paused ETF backtests can be resumed.'),
        )
      }
      target.status = 'PENDING'
      target.pending_control_action = 'NONE'
      return Promise.resolve({ id: runId, message: 'ETF backtest resume queued.' })
    })

    apiMocks.restartEtfBacktest.mockImplementation((runId: number) =>
      Promise.resolve({ id: runId, message: 'ETF backtest restart queued.' }),
    )

    // A live row keeps its DELETE intent until the engine reaches the boundary; a
    // terminal row is gone at once and the server answers 204 with no body.
    apiMocks.deleteEtfBacktest.mockImplementation((runId: number) => {
      const index = rows.findIndex((item) => item.id === runId)
      if (index < 0) return Promise.resolve(null)
      const [target] = rows.splice(index, 1)
      if (target && (target.status === 'RUNNING' || target.status === 'PENDING')) {
        rows.push({ ...target, pending_control_action: 'DELETE' })
        return Promise.resolve({
          id: runId,
          message: 'ETF backtest removal requested. It will be removed after the current chunk.',
        })
      }
      return Promise.resolve(null)
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders one row per run with percentages built from fractional metrics', async () => {
    const { listCard } = await renderWorkbench()

    const completed = await rowById(82)
    expect(within(completed).getByText('2026-10-20')).toBeInTheDocument()
    expect(within(completed).getByText('18.21%')).toBeInTheDocument()
    expect(within(completed).getByText('77.56%')).toBeInTheDocument()
    expect(within(completed).getByText('0.908')).toBeInTheDocument()
    expect(within(completed).getByText('-19.98%')).toBeInTheDocument()
    expect(within(completed).getByText('Completed')).toBeInTheDocument()
    expect(within(completed).getByText('RULE · MONTHLY · score')).toBeInTheDocument()

    // A run that has not finished has no metrics to report, only its status.
    const running = await rowById(83)
    expect(within(running).getAllByText('--').length).toBe(4)
    expect(within(running).getByText('Running')).toBeInTheDocument()
    expect(within(running).getByText('RULE · WEEKLY · equal')).toBeInTheDocument()

    // Progress lives in `report`, which the list serializer drops: the cursor only
    // ever reaches the page through retrieve, so the poll is not decorative.
    expect(within(running).queryByText(/chunked run in progress/)).not.toBeInTheDocument()
    expect(within(listCard).getByText('Page 1/1')).toBeInTheDocument()
  })

  it('sends the sort column to the server instead of re-ordering the current page', async () => {
    const user = userEvent.setup()
    const { listCard } = await renderWorkbench()
    await rowById(82)

    expect(apiMocks.fetchEtfBacktests).toHaveBeenLastCalledWith(
      expect.objectContaining({ sort: 'created', page: 1, limit: 20 }),
    )

    await user.click(within(listCard).getByRole('button', { name: 'Alpha' }))

    await waitFor(() => {
      expect(apiMocks.fetchEtfBacktests).toHaveBeenLastCalledWith(
        expect.objectContaining({ sort: 'alpha', page: 1, limit: 20 }),
      )
    })
    // The arrow marks the one column the server is ordering by right now.
    expect(within(listCard).getByRole('button', { name: 'Alpha ↓' })).toBeInTheDocument()
    expect(within(listCard).getByRole('button', { name: 'Created' })).toBeInTheDocument()
  })

  it('asks the server for one weighting when the filter changes', async () => {
    const user = userEvent.setup()
    const { listCard } = await renderWorkbench()
    await rowById(82)

    await user.selectOptions(within(listCard).getByLabelText('Weighting'), 'equal')

    await waitFor(() => {
      expect(apiMocks.fetchEtfBacktests).toHaveBeenLastCalledWith(expect.objectContaining({ weighting: 'equal' }))
    })
    // A new filter is a new query, so the cache key changes rather than the value.
    expect(apiMocks.fetchEtfBacktests).toHaveBeenCalledTimes(2)
  })

  it('posts only the writable subset and follows the row the server returned', async () => {
    const user = userEvent.setup()
    const { createCard } = await renderWorkbench()

    const created = makeRun({ id: 84, name: 'ui-rule-test', status: 'PENDING' })
    rows.push(created)
    apiMocks.createEtfBacktest.mockImplementation(() => Promise.resolve(listProjection(created)))

    await user.clear(within(createCard).getByLabelText('Name'))
    await user.type(within(createCard).getByLabelText('Name'), 'ui-rule-test')
    await user.type(within(createCard).getByLabelText('Initial Capital'), '480000')
    await user.click(within(createCard).getByRole('button', { name: 'Launch Backtest' }))

    await waitFor(() => {
      expect(apiMocks.createEtfBacktest).toHaveBeenCalledTimes(1)
    })
    const payload = apiMocks.createEtfBacktest.mock.calls[0]?.[0] as Record<string, unknown>
    // The fee knobs, `walk_forward_steps` and `benchmark_code` are read-only
    // server-side, and a blank optional field is omitted rather than sent as ''.
    expect(Object.keys(payload).sort()).toEqual([
      'absolute_momentum_filter',
      'end_date',
      'initial_capital',
      'max_positions',
      'momentum_lookback',
      'name',
      'rebalance_frequency',
      'start_date',
      'weighting',
    ])
    expect(payload.name).toBe('ui-rule-test')
    expect(payload.initial_capital).toBe(480000)
    expect(payload.defensive_asset).toBeUndefined()

    // 202 (new row) and 200 (duplicate fingerprint) are the same body, so the page
    // cannot claim either and says "accepted" instead.
    expect(
      await within(createCard).findByText(
        'Request accepted: a new row answers 202, a duplicate fingerprint answers 200 with the existing run.',
      ),
    ).toBeInTheDocument()

    // The returned row is selected without a second click, and its detail -- the
    // only place progress exists -- is fetched by id.
    await cardByTitle('Run Detail #84')
    await waitFor(() => {
      expect(apiMocks.fetchEtfBacktest).toHaveBeenCalledWith(84)
    })
  })

  it('shows the chunk cursor only after retrieve, then advances it on the 5s poll', async () => {
    installPollTimerSpies()
    const user = userEvent.setup()
    await renderWorkbench()

    await user.click(await rowById(83))
    const detail = await cardByTitle('Run Detail #83')

    expect(await within(detail).findByText(/chunked run in progress: 120\/641 sessions/)).toBeInTheDocument()
    expect(within(detail).getByText('Task State')).toBeInTheDocument()
    expect(within(detail).getByText('STARTED')).toBeInTheDocument()

    const scheduled = livePollTimers()
    expect(scheduled.length).toBe(1)
    expect(scheduled[0]?.delay).toBe(POLL_INTERVAL_MS)

    // The engine writes the next chunk server-side; the poll has to carry it in.
    advanceCursor(83, 240)
    await firePoll()
    expect(await within(detail).findByText(/chunked run in progress: 240\/641 sessions/)).toBeInTheDocument()

    // A terminal row has nothing left to advance, so its panel un-schedules the poll.
    await user.click(await rowById(82))
    const completedDetail = await cardByTitle('Run Detail #82')
    expect(await within(completedDetail).findByText('Completed')).toBeInTheDocument()
    await waitFor(() => {
      expect(livePollTimers().length).toBe(0)
    })
  })

  it('gates the lifecycle buttons the way the server does, from pause through resume', async () => {
    const user = userEvent.setup()
    const { listCard } = await renderWorkbench()

    await user.click(await rowById(83))
    const detail = await cardByTitle('Run Detail #83')
    await within(detail).findByText(/chunked run in progress: 120\/641 sessions/)

    // A RUNNING row with no pending intent: pause and restart are live, resume is not.
    expect(within(detail).getByRole('button', { name: 'Pause' })).toBeEnabled()
    expect(within(detail).getByRole('button', { name: 'Restart' })).toBeEnabled()
    expect(within(detail).getByRole('button', { name: 'Resume' })).toBeDisabled()

    await user.click(within(detail).getByRole('button', { name: 'Pause' }))

    // The server's own sentence is the only place the chunk-boundary delay is stated.
    expect(
      await within(listCard).findByText('ETF backtest pause requested. It will pause after the current chunk.'),
    ).toBeInTheDocument()

    // The intent landed on the row, so nothing may be asked of it until the boundary:
    // pause and restart go dead, delete stays available. The status line is one
    // composed string, so the whole line is matched rather than a fragment.
    expect(
      await within(detail).findByText(
        'Running (chunked run in progress: 120/641 sessions · A pause is pending and lands at the next chunk boundary.)',
      ),
    ).toBeInTheDocument()
    expect(within(detail).getByRole('button', { name: 'Pause' })).toBeDisabled()
    expect(within(detail).getByRole('button', { name: 'Restart' })).toBeDisabled()
    expect(within(detail).getByRole('button', { name: 'Delete' })).toBeEnabled()

    // The engine reaches the boundary, and the row is PAUSED with the cursor kept.
    const target = row(83)
    target.status = 'PAUSED'
    target.pending_control_action = 'NONE'
    target.task_state = ''
    await user.click(within(detail).getByRole('button', { name: 'Refresh' }))

    expect(await within(detail).findByText('Paused (chunked run in progress: 120/641 sessions)')).toBeInTheDocument()
    expect(within(detail).getByRole('button', { name: 'Resume' })).toBeEnabled()
    expect(within(detail).getByRole('button', { name: 'Pause' })).toBeDisabled()

    await user.click(within(detail).getByRole('button', { name: 'Resume' }))
    expect(await within(listCard).findByText('ETF backtest resume queued.')).toBeInTheDocument()
    expect(apiMocks.resumeEtfBacktest).toHaveBeenCalledWith(83)
  })

  it('surfaces the concurrency-cap sentence from the server verbatim', async () => {
    const capMessage =
      '2 ETF backtest(s) are already in flight, which is the cap (2). Wait for one to finish, pause it, or raise ETF_MAX_CONCURRENT_BACKTESTS.'
    apiMocks.createEtfBacktest.mockRejectedValue(new ApiRequestError('Too Many Requests', 429, capMessage))

    const user = userEvent.setup()
    const { createCard } = await renderWorkbench()

    await user.click(within(createCard).getByRole('button', { name: 'Launch Backtest' }))

    expect(await within(createCard).findByText(capMessage)).toBeInTheDocument()
    expect(within(createCard).queryByText(/Request accepted/)).not.toBeInTheDocument()
  })

  it('handles a 400 from an action, and clears the detail when a finished row is deleted', async () => {
    const user = userEvent.setup()
    const { listCard } = await renderWorkbench()

    // Resume is refused for a RUNNING row by the same rule the buttons implement.
    await user.click(await rowById(83))
    const runningDetail = await cardByTitle('Run Detail #83')
    await within(runningDetail).findByText(/chunked run in progress: 120\/641 sessions/)
    expect(within(runningDetail).getByRole('button', { name: 'Resume' })).toBeDisabled()
    apiMocks.resumeEtfBacktest.mockImplementation(() =>
      Promise.reject(new ApiRequestError('Bad Request', 400, 'Only paused ETF backtests can be resumed.')),
    )

    // A finished row answers 204 with no body, so the localised notice stands in.
    await user.click(await rowById(82))
    const detail = await cardByTitle('Run Detail #82')
    await user.click(within(detail).getByRole('button', { name: 'Delete' }))

    expect(await within(listCard).findByText('Removal handled.')).toBeInTheDocument()
    expect(await screen.findByText('Select a row to see its progress, metrics and equity curve.')).toBeInTheDocument()
    expect(screen.queryByText('#82')).not.toBeInTheDocument()
    await waitFor(() => {
      expect(apiMocks.fetchEtfBacktests).toHaveBeenCalledTimes(2)
    })
    expect(apiMocks.deleteEtfBacktest).toHaveBeenCalledWith(82)
  })

  it('offers each money-market ETF with its 20-day turnover so a thin one reads thin', async () => {
    const user = userEvent.setup()
    const { createCard } = await renderWorkbench()

    // The option label carries the code, the name and the scaled turnover, because the
    // raw yuan figure (27583000000) cannot be compared at a glance -- and 0.01亿/day is
    // precisely the defensive sleeve the dropdown exists to steer people away from.
    expect(
      await within(createCard).findByText('511880.SH · Money Market ETF · 20D turnover 275.83亿'),
    ).toBeInTheDocument()
    expect(
      within(createCard).getByText('511670.SH · Thin Money Market ETF · 20D turnover 0.01亿 · below liquidity floor'),
    ).toBeInTheDocument()

    const picker = within(createCard).getByLabelText('Defensive Asset')
    const customInput = within(createCard).getByLabelText('Custom Defensive Asset Code')

    // The blank default is still the default, so a form nobody touches sends no code at all.
    expect(picker).toHaveValue('')
    expect(customInput).toHaveValue('')

    await user.selectOptions(picker, '511880.SH')
    expect(customInput).toHaveValue('511880.SH')

    // The free-text hatch stays live: a code the universe endpoint does not rank is not a
    // reason the form should refuse to run a backtest with it.
    await user.clear(customInput)
    await user.type(customInput, '512090.SH')
    expect(picker).toHaveValue('__custom__')

    const created = makeRun({ id: 85, name: 'ui-rule-custom-defensive', status: 'PENDING' })
    rows.push(created)
    apiMocks.createEtfBacktest.mockImplementation(() => Promise.resolve(listProjection(created)))
    await user.click(within(createCard).getByRole('button', { name: 'Launch Backtest' }))

    await waitFor(() => {
      expect(apiMocks.createEtfBacktest).toHaveBeenCalledWith(
        expect.objectContaining({ defensive_asset: '512090.SH' }),
      )
    })
  })

  it('keeps the defensive code typeable when the money-market list cannot be loaded', async () => {
    apiMocks.fetchEtfDefensiveAssets.mockRejectedValue(new ApiRequestError('Not Found', 404))

    const user = userEvent.setup()
    const { createCard } = await renderWorkbench()

    expect(await within(createCard).findByText(/a custom code can still be typed/)).toBeInTheDocument()
    const customInput = within(createCard).getByLabelText('Custom Defensive Asset Code')
    await user.type(customInput, '511880.SH')
    expect(customInput).toHaveValue('511880.SH')
  })
})
