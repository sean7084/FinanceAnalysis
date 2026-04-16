import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { CandlestickChart } from '../components/charts/CandlestickChart'
import { ProbabilityChart } from '../components/charts/ProbabilityChart'
import {
  ApiRequestError,
  fetchAssetBySymbol,
  fetchAssets,
  fetchLightGBMPredictionBySymbol,
  fetchOhlcvByAsset,
  fetchPredictionBySymbol,
  fetchSentimentByAsset,
  hasAnyAuthCredential,
  type LightGBMPredictionStockDto,
  type PredictionStockDto,
} from '../lib/api'
import { useI18n } from '../i18n'

export function StockDetailPage() {
  const { t } = useI18n()
  const navigate = useNavigate()
  const { symbol = '600519' } = useParams()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [stockName, setStockName] = useState('')
  const [klineData, setKlineData] = useState<Array<{ time: string; open: number; high: number; low: number; close: number }>>([])
  const [predData, setPredData] = useState<Array<{ horizon: string; up: number; flat: number; down: number }>>([])
  const [heuristicPrediction, setHeuristicPrediction] = useState<PredictionStockDto | null>(null)
  const [lightgbmPrediction, setLightgbmPrediction] = useState<LightGBMPredictionStockDto | null>(null)
  const [sentimentLatest, setSentimentLatest] = useState<number | null>(null)
  const [assetOptions, setAssetOptions] = useState<Array<{ symbol: string; name: string }>>([])

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const assets = await fetchAssets(150)
        if (alive) {
          setAssetOptions(assets.map((x) => ({ symbol: x.symbol, name: x.name })))
        }
      } catch {
        if (alive) {
          setAssetOptions([])
        }
      }
    })()
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        setLoading(true)
        const asset = await fetchAssetBySymbol(symbol)
        if (!asset) {
          setError(t('stock.noAssetData'))
          setStockName('')
          setKlineData([])
          setPredData([])
          setHeuristicPrediction(null)
          setLightgbmPrediction(null)
          setSentimentLatest(null)
          return
        }

        const ohlcvRows = await fetchOhlcvByAsset(asset.id, 3000)
        const [predictionResult, lightgbmResult, sentimentResult] = await Promise.allSettled([
          fetchPredictionBySymbol(asset.symbol),
          fetchLightGBMPredictionBySymbol(asset.symbol),
          fetchSentimentByAsset(asset.id),
        ])

        if (!alive) {
          return
        }

        setStockName(asset.name)
        const points = [...ohlcvRows]
          .reverse()
          .map((row) => ({
            time: row.date,
            open: Number(row.open),
            high: Number(row.high),
            low: Number(row.low),
            close: Number(row.close),
          }))
        setKlineData(points)

        const prediction = predictionResult.status === 'fulfilled' ? predictionResult.value : null
        const lightgbm = lightgbmResult.status === 'fulfilled' ? lightgbmResult.value : null
        const sentimentRows = sentimentResult.status === 'fulfilled' ? sentimentResult.value : []

        setHeuristicPrediction(prediction)
        setLightgbmPrediction(lightgbm)
        setPredData(
          (prediction?.results ?? []).map((x) => ({
            horizon: `${x.horizon_days}D`,
            up: Number(x.up),
            flat: Number(x.flat),
            down: Number(x.down),
          })),
        )

        if (sentimentRows.length > 0) {
          setSentimentLatest(Number(sentimentRows[0].sentiment_score))
        }
        setError(null)
      } catch (err) {
        if (alive) {
          if (err instanceof ApiRequestError) {
            if (err.status === 401 || err.status === 403 || err.status === 429) {
              const detail = err.detail ? ` (${err.detail})` : ''
              setError(`${t('stock.loadError')}${detail}`)
            } else {
              setError(`${t('stock.loadError')} (HTTP ${err.status})`)
            }
          } else {
            setError(hasAnyAuthCredential() ? t('stock.loadError') : `${t('settings.desc')} (${t('nav.settings')})`)
          }
        }
      } finally {
        if (alive) {
          setLoading(false)
        }
      }
    })()

    return () => {
      alive = false
    }
  }, [symbol])

  const sentimentText = useMemo(() => {
    if (sentimentLatest === null) {
      return t('common.na')
    }
    return sentimentLatest.toFixed(3)
  }, [sentimentLatest, t])

  const comparisonRows = useMemo(() => {
    const comparison = new Map<number, {
      heuristicLabel: string
      heuristicConfidence: string
      heuristicUp: string
      lightgbmLabel: string
      lightgbmConfidence: string
      lightgbmUp: string
    }>()

    for (const result of heuristicPrediction?.results ?? []) {
      comparison.set(result.horizon_days, {
        heuristicLabel: result.predicted_label,
        heuristicConfidence: `${(Number(result.confidence) * 100).toFixed(1)}%`,
        heuristicUp: `${(Number(result.up) * 100).toFixed(1)}%`,
        lightgbmLabel: '--',
        lightgbmConfidence: '--',
        lightgbmUp: '--',
      })
    }

    for (const result of lightgbmPrediction?.results ?? []) {
      const existing = comparison.get(result.horizon_days) ?? {
        heuristicLabel: '--',
        heuristicConfidence: '--',
        heuristicUp: '--',
        lightgbmLabel: '--',
        lightgbmConfidence: '--',
        lightgbmUp: '--',
      }
      comparison.set(result.horizon_days, {
        ...existing,
        lightgbmLabel: result.predicted_label,
        lightgbmConfidence: `${(Number(result.confidence) * 100).toFixed(1)}%`,
        lightgbmUp: `${(Number(result.up) * 100).toFixed(1)}%`,
      })
    }

    return Array.from(comparison.entries())
      .sort((left, right) => left[0] - right[0])
      .map(([horizonDays, values]) => ({
        horizon: `${horizonDays}D`,
        ...values,
      }))
  }, [heuristicPrediction, lightgbmPrediction])

  const setupComparisonRows = useMemo(() => {
    const formatPrice = (value: number | string | null | undefined) => value != null ? Number(value).toFixed(2) : '--'
    const formatRatio = (value: number | string | null | undefined) => value != null ? Number(value).toFixed(2) : '--'
    const formatSuggested = (value: boolean | undefined) => value ? t('common.yes') : t('common.no')

    const comparison = new Map<number, {
      heuristicTargetPrice: string
      heuristicStopLossPrice: string
      heuristicRiskRewardRatio: string
      heuristicTradeScore: string
      heuristicSuggested: string
      lightgbmTargetPrice: string
      lightgbmStopLossPrice: string
      lightgbmRiskRewardRatio: string
      lightgbmTradeScore: string
      lightgbmSuggested: string
    }>()

    for (const result of heuristicPrediction?.results ?? []) {
      comparison.set(result.horizon_days, {
        heuristicTargetPrice: formatPrice(result.target_price),
        heuristicStopLossPrice: formatPrice(result.stop_loss_price),
        heuristicRiskRewardRatio: formatRatio(result.risk_reward_ratio),
        heuristicTradeScore: formatRatio(result.trade_score),
        heuristicSuggested: formatSuggested(result.suggested),
        lightgbmTargetPrice: '--',
        lightgbmStopLossPrice: '--',
        lightgbmRiskRewardRatio: '--',
        lightgbmTradeScore: '--',
        lightgbmSuggested: '--',
      })
    }

    for (const result of lightgbmPrediction?.results ?? []) {
      const existing = comparison.get(result.horizon_days) ?? {
        heuristicTargetPrice: '--',
        heuristicStopLossPrice: '--',
        heuristicRiskRewardRatio: '--',
        heuristicTradeScore: '--',
        heuristicSuggested: '--',
        lightgbmTargetPrice: '--',
        lightgbmStopLossPrice: '--',
        lightgbmRiskRewardRatio: '--',
        lightgbmTradeScore: '--',
        lightgbmSuggested: '--',
      }
      comparison.set(result.horizon_days, {
        ...existing,
        lightgbmTargetPrice: formatPrice(result.target_price),
        lightgbmStopLossPrice: formatPrice(result.stop_loss_price),
        lightgbmRiskRewardRatio: formatRatio(result.risk_reward_ratio),
        lightgbmTradeScore: formatRatio(result.trade_score),
        lightgbmSuggested: formatSuggested(result.suggested),
      })
    }

    return Array.from(comparison.entries())
      .sort((left, right) => left[0] - right[0])
      .map(([horizonDays, values]) => ({
        horizon: `${horizonDays}D`,
        ...values,
      }))
  }, [heuristicPrediction, lightgbmPrediction, t])

  return (
    <section>
      <header className="page-header">
        <h2>{t('stock.title')} · {symbol}{stockName ? ` (${stockName})` : ''}</h2>
        <p>{t('stock.desc')}</p>
      </header>
      <div className="card">
        <label htmlFor="stock-switch">{t('stock.selector')}</label>
        <select
          id="stock-switch"
          value={symbol}
          onChange={(e) => navigate(`/stock/${e.target.value}`)}
        >
          {assetOptions.length === 0 && <option value={symbol}>{symbol}</option>}
          {assetOptions.map((asset) => (
            <option key={asset.symbol} value={asset.symbol}>
              {asset.symbol} - {asset.name}
            </option>
          ))}
        </select>
      </div>
      {loading && <p className="status">{t('common.loading')}</p>}
      {error && <p className="status disconnected">{error}</p>}
      <div className="card">
        <strong>{t('stock.sentiment')}: </strong>
        <span>{sentimentText}</span>
      </div>
      <CandlestickChart data={klineData} />
      <ProbabilityChart title={t('stock.heuristicProbability')} data={predData} />
      <div className="card">
        <h3>{t('stock.tradeSignals')}</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>{t('models.horizon')}</th>
              <th>{t('comparison.heuristic')}</th>
              <th>{t('trade.targetPrice')}</th>
              <th>{t('trade.stopLoss')}</th>
              <th>{t('trade.rr')}</th>
              <th>{t('trade.tradeScore')}</th>
              <th>{t('trade.suggested')}</th>
              <th>{t('comparison.lightgbm')}</th>
              <th>{t('trade.targetPrice')}</th>
              <th>{t('trade.stopLoss')}</th>
              <th>{t('trade.rr')}</th>
              <th>{t('trade.tradeScore')}</th>
              <th>{t('trade.suggested')}</th>
            </tr>
          </thead>
          <tbody>
            {setupComparisonRows.map((row) => (
              <tr key={row.horizon}>
                <td>{row.horizon}</td>
                <td>{t('comparison.heuristic')}</td>
                <td>{row.heuristicTargetPrice}</td>
                <td>{row.heuristicStopLossPrice}</td>
                <td>{row.heuristicRiskRewardRatio}</td>
                <td>{row.heuristicTradeScore}</td>
                <td>{row.heuristicSuggested}</td>
                <td>{t('comparison.lightgbm')}</td>
                <td>{row.lightgbmTargetPrice}</td>
                <td>{row.lightgbmStopLossPrice}</td>
                <td>{row.lightgbmRiskRewardRatio}</td>
                <td>{row.lightgbmTradeScore}</td>
                <td>{row.lightgbmSuggested}</td>
              </tr>
            ))}
            {setupComparisonRows.length === 0 && !loading && (
              <tr>
                <td colSpan={13}>{t('common.noData')}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="card">
        <h3>{t('stock.modelComparison')}</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>{t('models.horizon')}</th>
              <th>{t('comparison.heuristic')}</th>
              <th>{t('models.confidence')}</th>
              <th>{t('comparison.upProbability')}</th>
              <th>{t('comparison.lightgbm')}</th>
              <th>{t('models.confidence')}</th>
              <th>{t('comparison.upProbability')}</th>
            </tr>
          </thead>
          <tbody>
            {comparisonRows.map((row) => (
              <tr key={row.horizon}>
                <td>{row.horizon}</td>
                <td>{row.heuristicLabel}</td>
                <td>{row.heuristicConfidence}</td>
                <td>{row.heuristicUp}</td>
                <td>{row.lightgbmLabel}</td>
                <td>{row.lightgbmConfidence}</td>
                <td>{row.lightgbmUp}</td>
              </tr>
            ))}
            {comparisonRows.length === 0 && !loading && (
              <tr>
                <td colSpan={7}>{t('common.noData')}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}
