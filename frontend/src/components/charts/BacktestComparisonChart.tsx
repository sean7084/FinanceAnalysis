import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { useI18n } from '../../i18n'
import type { BacktestComparisonPayloadDto, BacktestComparisonSeriesDto } from '../../lib/api'

interface BacktestComparisonChartProps {
  payload: BacktestComparisonPayloadDto | null
  loading: boolean
  error: string | null
  unavailableMessage?: string | null
}

const SERIES_COLORS: Record<string, string> = {
  selected_run: '#35c96b',
  compare_run: '#f3b54a',
  csi300: '#59a8ff',
  csia500: '#ff7d6c',
}

const EXTRA_SERIES_COLORS = ['#6fd3c1', '#f28b6d', '#8f9bff', '#d8b962', '#f07cb2', '#68c4ff']

type ChartRow = {
  date: string
  [seriesKey: string]: string | number | null
}

function formatValue(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '--'
  }
  return value.toLocaleString(undefined, { maximumFractionDigits: 0 })
}

function formatPercent(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '--'
  }
  return `${(value * 100).toFixed(2)}%`
}

function buildRows(seriesList: BacktestComparisonSeriesDto[], metric: 'value' | 'drawdown'): ChartRow[] {
  const rowsByDate = new Map<string, ChartRow>()

  for (const series of seriesList) {
    for (const point of series.points) {
      const existing = rowsByDate.get(point.date) ?? { date: point.date }
      existing[series.key] = point[metric]
      rowsByDate.set(point.date, existing)
    }
  }

  return Array.from(rowsByDate.values()).sort((left, right) => left.date.localeCompare(right.date))
}

function hashSeriesKey(seriesKey: string): number {
  let hash = 0
  for (let index = 0; index < seriesKey.length; index += 1) {
    hash = (hash * 31 + seriesKey.charCodeAt(index)) >>> 0
  }
  return hash
}

function lineColor(seriesKey: string): string {
  return SERIES_COLORS[seriesKey] ?? EXTRA_SERIES_COLORS[hashSeriesKey(seriesKey) % EXTRA_SERIES_COLORS.length]
}

function ComparisonSummaryCard({ series }: { series: BacktestComparisonSeriesDto }) {
  return (
    <article className="metric-card comparison-series-card">
      <span className="comparison-series-label">
        <i className="comparison-series-swatch" style={{ backgroundColor: lineColor(series.key) }} aria-hidden="true" />
        {series.label}
      </span>
      <strong>{formatPercent(series.total_return)}</strong>
      <span>{formatPercent(series.max_drawdown)}</span>
    </article>
  )
}

export function BacktestComparisonChart({ payload, loading, error, unavailableMessage }: BacktestComparisonChartProps) {
  const { t } = useI18n()

  const visibleSeries = payload?.series.filter((series) => series.points.length > 0) ?? []
  const equityRows = buildRows(visibleSeries, 'value')
  const drawdownRows = buildRows(visibleSeries, 'drawdown')
  const compareTargetLabel = payload?.compare_target?.name
    ? `#${payload.compare_target.id} ${payload.compare_target.name}`
    : null
  const fallbackMessage = unavailableMessage || payload?.message || t('backtest.comparisonUnavailable')

  return (
    <div className="card chart-card">
      <div className="comparison-header">
        <div>
          <h3>{t('backtest.comparisonTitle')}</h3>
          <p className="subtitle">{t('backtest.comparisonDesc')}</p>
        </div>
        <div className="comparison-meta">
          <span>{t('backtest.compareTarget')}</span>
          <strong>{compareTargetLabel ?? t('backtest.compareTargetNone')}</strong>
        </div>
      </div>

      {loading ? <p className="status">{t('backtest.comparisonLoading')}</p> : null}
      {!loading && error ? <p className="status disconnected">{error}</p> : null}
      {!loading && !error && visibleSeries.length === 0 ? <p className="status">{fallbackMessage}</p> : null}

      {!loading && !error && visibleSeries.length > 0 ? (
        <>
          <div className="comparison-series-grid">
            {visibleSeries.map((series) => (
              <ComparisonSummaryCard key={series.key} series={series} />
            ))}
          </div>

          <div className="comparison-chart-block">
            <h4>{t('backtest.comparisonEquityTitle')}</h4>
            <ResponsiveContainer width="100%" height={320}>
              <LineChart data={equityRows}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" />
                <XAxis dataKey="date" stroke="#9eb2c8" minTickGap={36} />
                <YAxis stroke="#9eb2c8" tickFormatter={(value) => formatValue(Number(value))} width={90} />
                <Tooltip formatter={(value) => formatValue(typeof value === 'number' ? value : Number(value))} />
                <Legend />
                {visibleSeries.map((series) => (
                  <Line
                    key={series.key}
                    type="monotone"
                    dataKey={series.key}
                    name={series.label}
                    stroke={lineColor(series.key)}
                    strokeWidth={2.4}
                    dot={false}
                    connectNulls
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="comparison-chart-block comparison-chart-block-secondary">
            <h4>{t('backtest.comparisonDrawdownTitle')}</h4>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={drawdownRows}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" />
                <XAxis dataKey="date" stroke="#9eb2c8" minTickGap={36} />
                <YAxis stroke="#9eb2c8" tickFormatter={(value) => formatPercent(Number(value))} width={80} />
                <Tooltip formatter={(value) => formatPercent(typeof value === 'number' ? value : Number(value))} />
                <Legend />
                {visibleSeries.map((series) => (
                  <Line
                    key={series.key}
                    type="monotone"
                    dataKey={series.key}
                    name={series.label}
                    stroke={lineColor(series.key)}
                    strokeWidth={2}
                    dot={false}
                    connectNulls
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        </>
      ) : null}
    </div>
  )
}