import { useMemo, useState } from 'react'
import {
  fetchDashboardStocks,
  type DashboardStockRowDto,
} from '../lib/api'
import { TableSkeleton } from '../components/layout/TableSkeleton'
import { useApiResource } from '../hooks/useApiResource'
import { useI18n } from '../useI18n'

function renderValue(value: unknown, t: (key: string) => string) {
  if (typeof value === 'boolean') {
    return value ? t('common.yes') : t('common.no')
  }
  if (value == null || value === '') {
    return '--'
  }
  if (typeof value === 'number') {
    return value.toFixed(2)
  }
  const numericValue = Number(value)
  if (!Number.isNaN(numericValue) && String(value).trim() !== '') {
    return numericValue.toFixed(2)
  }
  return String(value)
}

// Module scope so the "no rows yet" value keeps one identity; a fresh `[]` each
// render would recompute the memo that lists it as a dependency.
const EMPTY_ROWS: DashboardStockRowDto[] = []

export function IndicatorBoardPage() {
  const { t } = useI18n()
  const rowsResource = useApiResource<DashboardStockRowDto[]>(
    'indicator-board/stocks?h=7&n=120',
    () => fetchDashboardStocks({ predictionHorizon: 7, pageSize: 120 }),
  )
  const rows = rowsResource.data ?? EMPTY_ROWS
  const [sortField, setSortField] = useState<keyof DashboardStockRowDto>('composite_score')
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc')
  const [columnFilters, setColumnFilters] = useState<Record<string, string>>({})

  const tableColumns: Array<{ key: keyof DashboardStockRowDto; label: string }> = [
    { key: 'asset_symbol', label: t('screener.symbol') },
    { key: 'asset_name', label: t('screener.name') },
    { key: 'composite_score', label: t('screener.comp') },
    { key: 'bottom_probability_score', label: t('screener.bottomProb') },
    { key: 'fundamental_score', label: t('dash.fundamental') },
    { key: 'capital_flow_score', label: t('dash.capitalFlow') },
    { key: 'technical_score', label: t('dash.technical') },
    { key: 'factor_sentiment_score', label: t('dash.factorSentiment') },
    { key: 'rsi', label: 'RSI' },
    { key: 'macd', label: 'MACD' },
    { key: 'bb_upper', label: 'BB Upper' },
    { key: 'bb_lower', label: 'BB Lower' },
    { key: 'sma_60', label: 'SMA60' },
    { key: 'heuristic_trade_score', label: t('dash.heuristicTradeScore') },
    { key: 'heuristic_target_price', label: t('trade.targetPrice') },
    { key: 'heuristic_stop_loss_price', label: t('trade.stopLoss') },
    { key: 'heuristic_risk_reward_ratio', label: t('dash.heuristicRR') },
    { key: 'heuristic_suggested', label: t('dash.heuristicSuggested') },
    { key: 'lightgbm_trade_score', label: t('dash.lightgbmTradeScore') },
    { key: 'lightgbm_target_price', label: t('trade.targetPrice') },
    { key: 'lightgbm_stop_loss_price', label: t('trade.stopLoss') },
    { key: 'lightgbm_risk_reward_ratio', label: t('dash.lightgbmRR') },
    { key: 'lightgbm_suggested', label: t('dash.lightgbmSuggested') },
  ]

  const displayedRows = useMemo(() => {
    const matchesFilter = (value: unknown, filter: string) => String(value ?? '').toLowerCase().includes(filter.toLowerCase())
    const filtered = rows.filter((row) => (
      Object.entries(columnFilters).every(([key, value]) => !value || matchesFilter(row[key as keyof DashboardStockRowDto], value))
    ))

    return [...filtered].sort((left, right) => {
      const leftValue = left[sortField]
      const rightValue = right[sortField]
      if (leftValue == null && rightValue == null) return 0
      if (leftValue == null) return 1
      if (rightValue == null) return -1
      if (typeof leftValue === 'string' && typeof rightValue === 'string') {
        return sortDirection === 'asc' ? leftValue.localeCompare(rightValue) : rightValue.localeCompare(leftValue)
      }
      const leftNumber = Number(leftValue)
      const rightNumber = Number(rightValue)
      return sortDirection === 'asc' ? leftNumber - rightNumber : rightNumber - leftNumber
    })
  }, [columnFilters, rows, sortDirection, sortField])

  const handleSort = (field: keyof DashboardStockRowDto) => {
    if (sortField === field) {
      setSortDirection((value) => value === 'asc' ? 'desc' : 'asc')
      return
    }
    setSortField(field)
    setSortDirection('desc')
  }

  const setColumnFilter = (field: keyof DashboardStockRowDto, value: string) => {
    setColumnFilters((current) => ({
      ...current,
      [field]: value,
    }))
  }

  return (
    <section>
      <header className="page-header">
        <h2>{t('indicatorBoard.title')}</h2>
        <p>{t('indicatorBoard.desc')}</p>
      </header>

      {rowsResource.error && (
        <p className="status disconnected">
          {t('dash.loadError')}
        </p>
      )}

      <div className="card">
        <h3>{t('dash.allStocks')}</h3>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                {tableColumns.map((column) => (
                  <th key={column.key}>
                    <button type="button" className="table-sort-button" onClick={() => handleSort(column.key)}>
                      {column.label}
                      {sortField === column.key ? (sortDirection === 'asc' ? ' ↑' : ' ↓') : ''}
                    </button>
                  </th>
                ))}
              </tr>
              <tr>
                {tableColumns.map((column) => (
                  <th key={`${column.key}-filter`}>
                    <input
                      value={columnFilters[column.key] ?? ''}
                      onChange={(event) => setColumnFilter(column.key, event.target.value)}
                      placeholder={t('dash.filterColumn')}
                    />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {displayedRows.map((row) => (
                <tr key={row.asset_symbol}>
                  {tableColumns.map((column) => (
                    <td key={`${row.asset_symbol}-${column.key}`}>{renderValue(row[column.key], t)}</td>
                  ))}
                </tr>
              ))}
              {displayedRows.length === 0 && rowsResource.loading && (
                <tr>
                  <td colSpan={tableColumns.length}>
                    <TableSkeleton columns={tableColumns.length} rows={8} />
                  </td>
                </tr>
              )}
              {displayedRows.length === 0 && !rowsResource.loading && (
                <tr>
                  <td colSpan={tableColumns.length}>{t('common.noData')}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}