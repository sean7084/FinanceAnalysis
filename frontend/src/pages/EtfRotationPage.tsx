import { useState } from 'react'
import {
  fetchEtfBacktests,
  fetchEtfClosedPositions,
  fetchEtfComparisonCurve,
  fetchEtfLatestSignal,
  fetchEtfOpenPositions,
  fetchEtfSignalFreshness,
  fetchEtfSignals,
  fetchEtfUniverse,
  fmtPct,
  type EtfBacktestDto,
  type EtfComparisonPayloadDto,
  type EtfPositionDto,
  type EtfRankingDto,
  type EtfSignalDto,
  type EtfSignalFreshnessDto,
  type EtfUniverseDto,
} from '../lib/api'
import { BacktestComparisonChart } from '../components/charts/BacktestComparisonChart'
import { TableSkeleton } from '../components/layout/TableSkeleton'
import { useApiResource } from '../hooks/useApiResource'
import { useI18n } from '../useI18n'

type EtfTab = 'signal' | 'universe'
type Translate = (key: string) => string

// Module scope so the "nothing loaded yet" values keep one identity; a fresh `[]`
// per render would recompute any memo that lists one as a dependency.
const EMPTY_SIGNAL: EtfSignalDto | null = null
const EMPTY_FRESHNESS: EtfSignalFreshnessDto | null = null
const EMPTY_POSITIONS: EtfPositionDto[] = []
const EMPTY_RUNS: EtfBacktestDto[] = []
const EMPTY_RANKINGS: EtfRankingDto[] = []
const EMPTY_UNIVERSE_ROWS: EtfUniverseDto[] = []
const EMPTY_SIGNALS: EtfSignalDto[] = []
const EMPTY_OBJECT: Record<string, unknown> = {}

function fmtNumber(value: unknown, digits = 4): string {
  if (value === null || value === undefined || value === '') {
    return '--'
  }
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toFixed(digits) : String(value)
}

/**
 * A snapshot timestamp rendered for a human, in the browser's zone.
 *
 * `date` is the data cut the signal was computed from; `created_at` is when the row was
 * written. Only the first is a trading session, so the two get different labels and
 * different formats -- an ISO date stays an ISO date, a timestamp becomes local.
 */
function fmtDateTime(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') {
    return '--'
  }
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString()
}

/** Walk a dict-by-dict path, stopping at the first segment that is not an object. */
function readPath(source: unknown, path: readonly string[]): unknown {
  let cursor: unknown = source
  for (const segment of path) {
    if (typeof cursor !== 'object' || cursor === null) {
      return undefined
    }
    cursor = (cursor as Record<string, unknown>)[segment]
  }
  return cursor
}

function firstPresent(...values: unknown[]): unknown {
  return values.find((value) => value !== undefined && value !== null)
}

/**
 * A strategy knob rendered as text, whatever its type.
 *
 * The panel iterates `Object.entries(metadata.config)`, so a knob the backend adds later
 * arrives here with no frontend change and no label of its own -- which is why booleans
 * and numbers are formatted by type rather than by a per-key switch.
 */
function fmtParamValue(value: unknown, t: Translate): string {
  if (value === null || value === undefined || value === '') {
    return '--'
  }
  if (typeof value === 'boolean') {
    return value ? t('common.yes') : t('common.no')
  }
  if (typeof value === 'number') {
    return Number.isInteger(value) ? String(value) : fmtNumber(value)
  }
  if (typeof value === 'string') {
    return value
  }
  if (Array.isArray(value)) {
    return value.length ? value.map((item) => fmtParamValue(item, t)).join(', ') : '--'
  }
  try {
    // An empty dict means the backend stamped nothing yet, which reads as "--" rather
    // than as the braces the operator would otherwise try to interpret.
    return Object.keys(value as object).length ? JSON.stringify(value) : '--'
  } catch {
    return '--'
  }
}

function ParamTable({
  caption,
  rows,
}: {
  caption: string
  rows: Array<{ key: string; label: string; value: string }>
}) {
  const { t } = useI18n()

  return (
    <div className="table-wrap">
      <table className="data-table">
        <caption className="subtitle">{caption}</caption>
        <thead>
          <tr>
            <th>{t('etf.dash.colParam')}</th>
            <th>{t('etf.dash.colValue')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <td>{row.label}</td>
              <td>{row.value}</td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={2}>{t('common.noData')}</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

interface SignalCardProps {
  signal: EtfSignalDto
  freshness: EtfSignalFreshnessDto | null
}

function SignalCard({ signal, freshness }: SignalCardProps) {
  const { t } = useI18n()
  const rankings = signal.rankings ?? EMPTY_RANKINGS

  // One composed sentence in one text node, so the warning reads as a single line in
  // either locale instead of a word-order-dependent pile of fragments.
  const staleMessage = freshness?.is_stale
    ? [
        `${t('etf.dash.staleWarning')} ${fmtParamValue(freshness.lag_sessions, t)} ${t('etf.dash.staleSessions')}`,
        `${t('etf.dash.asOfDate')} ${freshness.signal_date ?? signal.date}`,
        `${t('etf.dash.latestSnapshot')} ${freshness.latest_snapshot_date ?? '--'}`,
      ].join(' · ')
    : null
  const feedStale = freshness?.feed_stale ?? null
  const feedMessage = feedStale
    ? [
        t('etf.dash.feedStaleWarning'),
        feedStale.reason_code,
        fmtDateTime(feedStale.created_at),
      ].join(' · ')
    : null
  const attempt = freshness?.last_attempt ?? null
  const attemptFailed = attempt !== null && attempt.outcome !== 'OK'
  const attemptMessage = attemptFailed
    ? [
        t('etf.dash.attemptWarning'),
        attempt.outcome,
        attempt.reason_code,
        fmtDateTime(attempt.created_at),
      ].filter(Boolean).join(' · ')
    : null

  return (
    <div className="card">
      <h3>{t('etf.dash.signalTitle')}</h3>
      {staleMessage ? <p className="status disconnected">{staleMessage}</p> : null}
      {feedMessage ? <p className="status disconnected">{feedMessage}</p> : null}
      {attemptMessage ? <p className="status disconnected">{attemptMessage}</p> : null}
      <div className="metric-grid">
        <article className="metric-card">
          {/* The as-of date is the data cut, not a generation time: a signal built from a
              2026-10-20 snapshot says nothing about when the row was written. */}
          <span>{t('etf.dash.asOfDate')}</span>
          <strong>{signal.date}</strong>
        </article>
        <article className="metric-card">
          <span>{t('etf.dash.generatedAt')}</span>
          <strong>{fmtDateTime(signal.created_at)}</strong>
        </article>
        <article className="metric-card">
          <span>{t('etf.dash.updatedAt')}</span>
          <strong>{fmtDateTime(signal.updated_at)}</strong>
        </article>
        <article className="metric-card">
          <span>{t('etf.dash.signalModel')}</span>
          <strong>{signal.model_type}</strong>
        </article>
        <article className="metric-card">
          <span>{t('etf.dash.signalPositions')}</span>
          <strong>{signal.max_positions}</strong>
        </article>
        <article className="metric-card">
          <span>{t('etf.dash.defensive')}</span>
          <strong>{fmtPct(signal.defensive_allocation)}</strong>
          <span className="subtitle">{signal.defensive_asset ?? '--'}</span>
        </article>
        <article className="metric-card">
          <span>{t('etf.dash.benchmark20d')}</span>
          <strong>{fmtPct(signal.benchmark_return_20d)}</strong>
        </article>
      </div>

      <div className="table-wrap">
        <table className="data-table">
          <caption className="subtitle">{t('etf.dash.selectedTitle')}</caption>
          <thead>
            <tr>
              <th>{t('etf.dash.colEtf')}</th>
              <th>{t('etf.dash.colWeight')}</th>
              <th>{t('etf.dash.colReason')}</th>
            </tr>
          </thead>
          <tbody>
            {signal.selected_etfs.map((entry) => (
              <tr key={entry.ts_code}>
                <td>{entry.name} <span className="subtitle">{entry.ts_code}</span></td>
                <td>{fmtPct(entry.weight)}</td>
                <td>{entry.reason}</td>
              </tr>
            ))}
            {signal.selected_etfs.length === 0 && (
              <tr>
                <td colSpan={3}>{t('common.noData')}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {rankings.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <caption className="subtitle">{t('etf.dash.rankingsTitle')}</caption>
            <thead>
              <tr>
                <th>#</th>
                <th>{t('etf.dash.colEtf')}</th>
                <th>{t('etf.dash.colScore')}</th>
                <th>{t('etf.dash.colCategory')}</th>
              </tr>
            </thead>
            <tbody>
              {rankings.map((entry) => (
                <tr key={entry.ts_code}>
                  <td>{entry.rank}</td>
                  <td>{entry.name} <span className="subtitle">{entry.ts_code}</span></td>
                  <td>{fmtNumber(entry.score)}</td>
                  <td>{entry.category}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

/**
 * The model and every concrete knob behind the signal on screen.
 *
 * `metadata.config` is rendered from `Object.entries`, not a hardcoded list: the backend
 * is still adding knobs (`dedup_track_index`, `max_per_category`, `kelly_fraction`), and
 * an operator reading the dashboard has to see the ones that exist today plus the ones
 * that arrive next week, without this file changing.
 */
function ModelParamsCard({ signal }: { signal: EtfSignalDto }) {
  const { t } = useI18n()
  const metadata = signal.metadata ?? EMPTY_OBJECT
  const config = (readPath(metadata, ['config']) ?? EMPTY_OBJECT) as Record<string, unknown>
  const ruleWeights = (readPath(metadata, ['scoring_metadata', 'rule_weights']) ?? EMPTY_OBJECT) as Record<string, unknown>

  const configRows = Object.entries(config).map(([key, value]) => ({
    key,
    label: key,
    value: fmtParamValue(value, t),
  }))

  const detailRows = [
    {
      key: 'momentum_column',
      label: t('etf.dash.momentumColumn'),
      value: fmtParamValue(firstPresent(readPath(metadata, ['scoring_metadata', 'momentum_column']), metadata['momentum_column']), t),
    },
    {
      key: 'relative_strength_column',
      label: t('etf.dash.relativeStrengthColumn'),
      value: fmtParamValue(readPath(metadata, ['scoring_metadata', 'relative_strength_column']), t),
    },
    {
      key: 'available_momentum_lookbacks',
      label: t('etf.dash.availableLookbacks'),
      value: fmtParamValue(readPath(metadata, ['scoring_metadata', 'available_momentum_lookbacks']), t),
    },
    {
      key: 'weight_momentum',
      label: t('etf.dash.weightMomentum'),
      value: fmtParamValue(ruleWeights['momentum'], t),
    },
    {
      key: 'weight_relative_strength',
      label: t('etf.dash.weightRelativeStrength'),
      value: fmtParamValue(ruleWeights['relative_strength'], t),
    },
    {
      key: 'weight_volatility_penalty',
      label: t('etf.dash.weightVolatilityPenalty'),
      value: fmtParamValue(ruleWeights['volatility_penalty'], t),
    },
    {
      key: 'weight_method',
      label: t('etf.dash.weightMethod'),
      value: fmtParamValue(firstPresent(metadata['weight_method'], config['weighting'], metadata['weighting']), t),
    },
    {
      key: 'qualified_count',
      label: t('etf.dash.qualifiedCount'),
      value: fmtParamValue(metadata['qualified_count'], t),
    },
    {
      key: 'absolute_momentum_excluded_count',
      label: t('etf.dash.absExcludedCount'),
      value: fmtParamValue(metadata['absolute_momentum_excluded_count'], t),
    },
    {
      key: 'defensive_reason',
      label: t('etf.dash.defensiveReason'),
      value: fmtParamValue(metadata['defensive_reason'], t),
    },
    {
      key: 'model_versions',
      label: t('etf.dash.modelVersions'),
      value: fmtParamValue(firstPresent(metadata['model_versions'], signal.model_version), t),
    },
  ]

  return (
    <div className="card">
      <h3>{t('etf.dash.paramsTitle')}</h3>
      <p className="subtitle">{t('etf.dash.paramsDesc')}</p>
      <div className="metric-grid">
        <article className="metric-card">
          <span>{t('etf.dash.modelType')}</span>
          <strong>{String(firstPresent(metadata['model_type'], signal.model_type) ?? '--')}</strong>
        </article>
        <article className="metric-card">
          <span>{t('etf.dash.modelVersion')}</span>
          <strong>{fmtParamValue(signal.model_version, t)}</strong>
        </article>
      </div>
      <ParamTable caption={t('etf.dash.configTitle')} rows={configRows} />
      <ParamTable caption={t('etf.dash.scoringTitle')} rows={detailRows} />
    </div>
  )
}

function PositionsCard({ positions }: { positions: EtfPositionDto[] }) {
  const { t } = useI18n()

  return (
    <div className="card">
      <h3>{t('etf.dash.positionsTitle')}</h3>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>{t('etf.dash.colEtf')}</th>
              <th>{t('etf.dash.colEntryDate')}</th>
              <th>{t('etf.dash.colEntryPrice')}</th>
              <th>{t('etf.dash.colWeight')}</th>
              <th>{t('etf.dash.colLivePrice')}</th>
              <th>{t('etf.dash.colUnrealizedPnl')}</th>
              <th>{t('etf.dash.colSignalDate')}</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((row) => {
              // The daily ledger stamps ``live_mark`` on every leg it can price; a leg
              // opened after the last beat has none, and ``--`` is the honest answer.
              const liveMark = readPath(row.metadata, ['live_mark'])
              return (
                <tr key={row.id}>
                  <td>{row.etf_name} <span className="subtitle">{row.etf_ts_code}</span></td>
                  <td>{row.entry_date}</td>
                  <td>{fmtNumber(row.entry_price, 3)}</td>
                  <td>{fmtPct(row.weight)}</td>
                  <td>{fmtNumber(readPath(liveMark, ['price']), 4)}</td>
                  <td>{fmtNumber(readPath(liveMark, ['unrealized_pnl']), 2)}</td>
                  <td>{row.signal_date ?? '--'}</td>
                </tr>
              )
            })}
            {positions.length === 0 && (
              <tr>
                <td colSpan={7}>{t('etf.dash.positionsEmpty')}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/**
 * The live simulated book: what the signals told us, and what the daily ledger realised.
 *
 * Signals and closed legs are separate endpoints on purpose. The signal list says what the
 * strategy wanted; the CLOSED rows are the only source of realised pnl, so the summary here
 * is computed from them alone and never from OPEN marks.
 */
function LiveLedgerCard({
  signals,
  closedPositions,
}: {
  signals: EtfSignalDto[]
  closedPositions: EtfPositionDto[]
}) {
  const { t } = useI18n()
  const priced = closedPositions.filter(
    (row) => row.pnl !== null && row.pnl !== undefined && Number.isFinite(Number(row.pnl)),
  )
  // A leg booked without shares closes with a price return and no yuan pnl. Summing the
  // empty set would draw 0.00 and read as break-even, so "unknown" stays unknown.
  const realisedPnl = priced.length
    ? priced.reduce((total, row) => total + Number(row.pnl), 0)
    : null
  const wins = priced.filter((row) => Number(row.pnl) > 0).length
  const winRate = priced.length ? wins / priced.length : null

  return (
    <div className="card">
      <h3>{t('etf.dash.ledgerTitle')}</h3>
      <p className="subtitle">{t('etf.dash.ledgerDesc')}</p>
      <div className="metric-grid">
        <article className="metric-card">
          <span>{t('etf.dash.realisedPnl')}</span>
          <strong>{fmtNumber(realisedPnl, 2)}</strong>
        </article>
        <article className="metric-card">
          <span>{t('etf.dash.winRate')}</span>
          <strong>{fmtPct(winRate)}</strong>
        </article>
        <article className="metric-card">
          <span>{t('etf.dash.closedCount')}</span>
          <strong>{closedPositions.length}</strong>
        </article>
      </div>

      <div className="table-wrap">
        <table className="data-table">
          <caption className="subtitle">{t('etf.dash.ledgerClosedTitle')}</caption>
          <thead>
            <tr>
              <th>{t('etf.dash.colEtf')}</th>
              <th>{t('etf.dash.colEntryDate')}</th>
              <th>{t('etf.dash.colExitDate')}</th>
              <th>{t('etf.dash.colPnl')}</th>
              <th>{t('etf.dash.colReturn')}</th>
              <th>{t('etf.dash.colExitReason')}</th>
            </tr>
          </thead>
          <tbody>
            {closedPositions.map((row) => (
              <tr key={row.id}>
                <td>{row.etf_name} <span className="subtitle">{row.etf_ts_code}</span></td>
                <td>{row.entry_date}</td>
                <td>{row.exit_date ?? '--'}</td>
                <td>{fmtNumber(row.pnl, 2)}</td>
                <td>{fmtPct(row.return_pct)}</td>
                <td>{row.exit_reason || '--'}</td>
              </tr>
            ))}
            {closedPositions.length === 0 && (
              <tr>
                <td colSpan={6}>{t('etf.dash.ledgerEmpty')}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="table-wrap">
        <table className="data-table">
          <caption className="subtitle">{t('etf.dash.ledgerSignalsTitle')}</caption>
          <thead>
            <tr>
              <th>{t('etf.dash.signalDate')}</th>
              <th>{t('etf.dash.colSignalType')}</th>
              <th>{t('etf.dash.signalModel')}</th>
              <th>{t('etf.dash.ledgerSelectedCount')}</th>
              <th>{t('etf.dash.defensive')}</th>
            </tr>
          </thead>
          <tbody>
            {signals.map((row) => (
              <tr key={row.id}>
                <td>{row.date}</td>
                <td>{row.signal_type}</td>
                <td>{row.model_type}</td>
                <td>{row.selected_etfs.length}</td>
                <td>{fmtPct(row.defensive_allocation)}</td>
              </tr>
            ))}
            {signals.length === 0 && (
              <tr>
                <td colSpan={5}>{t('common.noData')}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function ComparisonCard({ runs }: { runs: EtfBacktestDto[] }) {
  const { t } = useI18n()
  // Null means "whatever the leaderboard puts first", so the chart has a curve on
  // the first paint instead of waiting for a click; picking a run only overrides it.
  const [pickedRunId, setPickedRunId] = useState<number | null>(null)
  const activeRun = runs.find((run) => run.id === pickedRunId) ?? runs[0] ?? null
  const activeRunId = activeRun?.id ?? null

  const curveResource = useApiResource<EtfComparisonPayloadDto | null>(
    `etf/backtests/${activeRunId}/comparison_curve`,
    () => (activeRunId === null ? Promise.resolve(null) : fetchEtfComparisonCurve(activeRunId)),
  )

  if (runs.length === 0) {
    return (
      <div className="card">
        <h3>{t('etf.dash.curveTitle')}</h3>
        <p className="status">{t('etf.dash.curveEmpty')}</p>
      </div>
    )
  }

  return (
    <div className="card">
      <h3>{t('etf.dash.curveTitle')}</h3>
      <p className="subtitle">{t('etf.dash.curveDesc')}</p>
      <div className="runner-weekday-chips">
        {runs.map((run) => (
          <button
            key={run.id}
            type="button"
            className="chip"
            disabled={activeRunId === run.id}
            onClick={() => setPickedRunId(run.id)}
          >
            #{run.id} {run.name} · {fmtPct(run.alpha_vs_benchmark)}
          </button>
        ))}
      </div>
      <BacktestComparisonChart
        payload={curveResource.data}
        loading={curveResource.loading}
        error={curveResource.error}
        unavailableMessage={t('etf.dash.curveEmpty')}
      />
    </div>
  )
}

function UniverseCard() {
  const { t } = useI18n()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [rankableOnly, setRankableOnly] = useState(true)
  const [draftSearch, setDraftSearch] = useState('')
  const [search, setSearch] = useState('')

  // The key spells out every filter, so a filter change is a new cache entry and
  // `deps` would only restate what the key already encodes.
  const resource = useApiResource(
    `etf/universe?p=${page}&n=${pageSize}&rankable=${rankableOnly ? 1 : 0}&q=${search}`,
    () => fetchEtfUniverse({ page, pageSize, search, isRankable: rankableOnly }),
  )
  const payload = resource.data
  const rows = payload?.results ?? EMPTY_UNIVERSE_ROWS
  const totalPages = Math.max(1, Math.ceil((payload?.count ?? 0) / pageSize))
  const clampedPage = Math.min(page, totalPages)

  return (
    <div className="card">
      <h3>{t('etf.dash.universeTitle')}</h3>
      <div className="runner-weekday-chips">
        <input
          value={draftSearch}
          placeholder={t('etf.dash.searchPlaceholder')}
          onChange={(event) => setDraftSearch(event.target.value)}
        />
        <button
          type="button"
          className="chip"
          onClick={() => {
            setSearch(draftSearch.trim())
            setPage(1)
          }}
        >
          {t('etf.dash.apply')}
        </button>
        <button
          type="button"
          className="chip"
          disabled={!rankableOnly}
          onClick={() => {
            setRankableOnly(false)
            setPage(1)
          }}
        >
          {t('etf.dash.showAll')}
        </button>
        <button
          type="button"
          className="chip"
          disabled={rankableOnly}
          onClick={() => {
            setRankableOnly(true)
            setPage(1)
          }}
        >
          {t('etf.dash.rankableOnly')}
        </button>
      </div>

      {resource.error && <p className="status disconnected">{t('etf.dash.loadError')}</p>}

      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>{t('etf.dash.colEtf')}</th>
              <th>{t('etf.dash.colCategory')}</th>
              <th>{t('etf.dash.colBenchmark')}</th>
              <th>{t('etf.dash.colListDate')}</th>
              <th>{t('etf.dash.colRankable')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.name} <span className="subtitle">{row.ts_code}</span></td>
                <td>{row.category}{row.sub_category ? ` / ${row.sub_category}` : ''}</td>
                <td>{row.benchmark || '--'}</td>
                <td>{row.effective_list_date ?? row.list_date ?? '--'}</td>
                <td>{row.is_rankable ? t('common.yes') : t('common.no')}</td>
              </tr>
            ))}
            {rows.length === 0 && resource.loading && (
              <tr>
                <td colSpan={5}><TableSkeleton columns={5} rows={6} /></td>
              </tr>
            )}
            {rows.length === 0 && !resource.loading && (
              <tr>
                <td colSpan={5}>{t('common.noData')}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="table-pagination">
        <label>
          {t('etf.dash.entriesPerPage')}
          <select
            value={pageSize}
            onChange={(event) => {
              setPageSize(Number(event.target.value))
              setPage(1)
            }}
          >
            <option value={10}>10</option>
            <option value={20}>20</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </label>
        <button type="button" disabled={clampedPage <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>
          {t('etf.dash.prev')}
        </button>
        <span>{t('etf.dash.page')} {clampedPage}/{totalPages}</span>
        <button
          type="button"
          disabled={clampedPage >= totalPages}
          onClick={() => setPage((value) => Math.min(totalPages, value + 1))}
        >
          {t('etf.dash.next')}
        </button>
      </div>
    </div>
  )
}

export function EtfRotationPage() {
  const { t } = useI18n()
  const [tab, setTab] = useState<EtfTab>('signal')

  const signalResource = useApiResource<EtfSignalDto | null>('etf/signals/latest', () => fetchEtfLatestSignal())
  // Freshness is a second, cheap endpoint because the signal row alone cannot say how far
  // its as-of date trails the newest eligible feature snapshot.
  const freshnessResource = useApiResource<EtfSignalFreshnessDto | null>(
    'etf/signals/freshness',
    () => fetchEtfSignalFreshness(),
  )
  const positionsResource = useApiResource<EtfPositionDto[]>('etf/positions/open', () => fetchEtfOpenPositions(50))
  // The ledger's two reads: what the strategy recently said, and the only rows that carry
  // realised pnl. Neither is derived from the other, so both are fetched independently.
  const signalHistoryResource = useApiResource<EtfSignalDto[]>('etf/signals?limit=10', () => fetchEtfSignals(10))
  const closedPositionsResource = useApiResource<EtfPositionDto[]>(
    'etf/positions/closed',
    () => fetchEtfClosedPositions(20),
  )
  // `sort=alpha` is server-side, and the leaderboard is a page of it rather than a
  // re-sort of a truncated page -- an aborted run with no metric must not head it.
  const runsResource = useApiResource('etf/backtests?sort=alpha&n=5', () => fetchEtfBacktests({ limit: 5, sort: 'alpha' }))

  const signal = signalResource.data ?? EMPTY_SIGNAL
  const freshness = freshnessResource.data ?? EMPTY_FRESHNESS
  const positions = positionsResource.data ?? EMPTY_POSITIONS
  const signalHistory = signalHistoryResource.data ?? EMPTY_SIGNALS
  const closedPositions = closedPositionsResource.data ?? EMPTY_POSITIONS
  const runs = runsResource.data?.results ?? EMPTY_RUNS

  return (
    <section>
      <header className="page-header">
        <h2>{t('etf.dash.title')}</h2>
        <p>{t('etf.dash.desc')}</p>
      </header>

      <div className="runner-weekday-chips">
        <button
          type="button"
          className="chip"
          disabled={tab === 'signal'}
          onClick={() => setTab('signal')}
        >
          {t('etf.dash.tabSignal')}
        </button>
        <button
          type="button"
          className="chip"
          disabled={tab === 'universe'}
          onClick={() => setTab('universe')}
        >
          {t('etf.dash.tabUniverse')}
        </button>
      </div>

      {(signalResource.error
        || positionsResource.error
        || signalHistoryResource.error
        || closedPositionsResource.error
        || runsResource.error) && (
        <p className="status disconnected">{t('etf.dash.loadError')}</p>
      )}

      {tab === 'signal' && (
        <>
          {signalResource.loading && <TableSkeleton columns={3} rows={4} />}
          {!signalResource.loading && signal === null && (
            <div className="card">
              <h3>{t('etf.dash.signalTitle')}</h3>
              <p className="status">{t('etf.dash.signalEmpty')}</p>
            </div>
          )}
          {signal !== null && <SignalCard signal={signal} freshness={freshness} />}
          {signal !== null && <ModelParamsCard signal={signal} />}
          {positionsResource.loading && <TableSkeleton columns={7} rows={3} />}
          {!positionsResource.loading && <PositionsCard positions={positions} />}
          {(signalHistoryResource.loading || closedPositionsResource.loading) && (
            <TableSkeleton columns={6} rows={3} />
          )}
          {!signalHistoryResource.loading && !closedPositionsResource.loading && (
            <LiveLedgerCard signals={signalHistory} closedPositions={closedPositions} />
          )}
          {runsResource.loading && <TableSkeleton columns={4} rows={2} />}
          {!runsResource.loading && <ComparisonCard runs={runs} />}
        </>
      )}

      {tab === 'universe' && <UniverseCard />}
    </section>
  )
}
