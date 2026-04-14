import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { CandlestickChart } from '../components/charts/CandlestickChart'
import { ProbabilityChart } from '../components/charts/ProbabilityChart'
import {
  ApiRequestError,
  fetchAssetBySymbol,
  fetchAssets,
  fetchOhlcvByAsset,
  fetchPredictionBySymbol,
  fetchSentimentByAsset,
  hasAnyAuthCredential,
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
          setSentimentLatest(null)
          return
        }

        const ohlcvRows = await fetchOhlcvByAsset(asset.id, 3000)
        const [predictionResult, sentimentResult] = await Promise.allSettled([
          fetchPredictionBySymbol(asset.symbol),
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
        const sentimentRows = sentimentResult.status === 'fulfilled' ? sentimentResult.value : []

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
      <ProbabilityChart title={t('chart.probability')} data={predData} />
    </section>
  )
}
