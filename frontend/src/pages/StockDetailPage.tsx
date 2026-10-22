import { useEffect, useMemo, useRef, useState } from 'react'
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
  type LightGBMPredictionStockDto,
  type PredictionStockDto,
} from '../lib/api'
import { cachedGet, seed } from '../lib/queryCache'
import { trackViewResource } from '../hooks/useApiResource'
import { useI18n } from '../useI18n'

/**
 * The five requests this page is made of, as one cache entry.
 *
 * Kept at module scope so the generation poll can name the payload it is merging
 * into, and so the effect below stays about rendering rather than about assembly.
 */
async function loadStockDetail(symbol: string) {
  const asset = await fetchAssetBySymbol(symbol)
  if (!asset) {
    return null
  }

  // All four only need asset.id / asset.symbol, so none has to wait for
  // another. OHLCV previously gated the rest behind up to three serial
  // page round trips.
  const [ohlcvResult, predictionResult, lightgbmResult, sentimentResult] = await Promise.allSettled([
    fetchOhlcvByAsset(asset.id, 3000),
    fetchPredictionBySymbol(asset.symbol),
    fetchLightGBMPredictionBySymbol(asset.symbol),
    fetchSentimentByAsset(asset.id),
  ])

  // allSettled would otherwise turn an OHLCV failure into a silently
  // empty chart; rethrow so the catch below reports it, as the serial
  // version did.
  if (ohlcvResult.status === 'rejected') {
    throw ohlcvResult.reason
  }

  return {
    asset,
    ohlcvRows: ohlcvResult.value,
    prediction: predictionResult.status === 'fulfilled' ? predictionResult.value : null,
    lightgbm: lightgbmResult.status === 'fulfilled' ? lightgbmResult.value : null,
    sentimentRows: sentimentResult.status === 'fulfilled' ? sentimentResult.value : [],
  }
}

type StockDetailPayload = Awaited<ReturnType<typeof loadStockDetail>>

/**
 * How long the page waits for a generation it queued itself.
 *
 * Five seconds is short enough that "will appear shortly" stays true and long
 * enough that a page someone is glancing at is not hammering the API. Twelve
 * ticks caps the wait at a minute: a sweep that outruns a minute should leave the
 * notice up rather than leave a background loop on the page.
 */
export const GENERATION_POLL_MS = 5_000
const GENERATION_POLL_MAX_TICKS = 12

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
  const [assetSearch, setAssetSearch] = useState('')
  const detailRef = useRef<StockDetailPayload>(null)
  const [reloadToken, setReloadToken] = useState(0)

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const assets = await cachedGet('assets?limit=150', () => fetchAssets(150))
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

  // The one condition the poll needs, and the reason it is not derived inside the
  // notice below: the effect has to see it in its dependency array.
  const awaitingGeneration = useMemo(() => {
    const payloads = [heuristicPrediction, lightgbmPrediction]
    return payloads.some(
      (payload) => payload?.generation?.status === 'queued' && (payload?.results.length ?? 0) === 0,
    )
  }, [heuristicPrediction, lightgbmPrediction])

  // This page composes five requests, one of them up to 3000 OHLCV rows, and its
  // error copy depends on the HTTP status. So it calls `cachedGet` directly
  // instead of `useApiResource`: the composed payload is cached and shared with
  // a remount, while the status-specific messaging below stays untouched.
  // `trackViewResource` is the part of the hook it does need -- without it the
  // route has no time-to-data, and the latency harness cannot price it.
  //
  // `reloadToken` is the second entry point: a queued generation seeds this same
  // key and bumps the token, so the rows are derived here and nowhere else. The
  // re-read is a cache hit, not another five requests.
  useEffect(() => {
    let alive = true
    const release = trackViewResource(`stock/${symbol}/detail`)
    ;(async () => {
      try {
        setLoading(true)
        const payload = await cachedGet(`stock/${symbol}/detail`, () => loadStockDetail(symbol))

        if (!alive || !payload) {
          if (payload === null && alive) {
            setError(t('stock.noAssetData'))
            setStockName('')
            setKlineData([])
            setPredData([])
            setHeuristicPrediction(null)
            setLightgbmPrediction(null)
            setSentimentLatest(null)
            detailRef.current = null
          }
          return
        }

        detailRef.current = payload
        setStockName(payload.asset.name)
        const points = [...payload.ohlcvRows]
          .reverse()
          .map((row) => ({
            time: row.date,
            open: Number(row.open),
            high: Number(row.high),
            low: Number(row.low),
            close: Number(row.close),
          }))
        setKlineData(points)

        setHeuristicPrediction(payload.prediction)
        setLightgbmPrediction(payload.lightgbm)
        setPredData(
          (payload.prediction?.results ?? []).map((x) => ({
            horizon: `${x.horizon_days}D`,
            up: Number(x.up),
            flat: Number(x.flat),
            down: Number(x.down),
          })),
        )

        if (payload.sentimentRows.length > 0) {
          setSentimentLatest(Number(payload.sentimentRows[0].sentiment_score))
        }
        setError(null)
      } catch (err) {
        if (alive) {
          if (err instanceof ApiRequestError) {
            setError(`${t('stock.loadError')} (HTTP ${err.status})`)
          } else {
            setError(t('stock.loadError'))
          }
        }
      } finally {
        if (alive) {
          setLoading(false)
        }
        // Every exit from this effect's try/catch lands here, including the
        // unknown-symbol path that returns early. A key left pending would stall
        // the stamp for the whole route.
        release()
      }
    })()

    return () => {
      alive = false
      release()
    }
  }, [symbol, reloadToken])

  // "Generation is queued and will appear shortly" is a promise this page has to
  // keep: the reconcile task writes rows, it does not push them. While a queued
  // payload still has nothing to show, re-read the two prediction endpoints and
  // merge whatever landed back into the composed cache key, so the derivation
  // above stays the only place rows become state -- and so a remount inside the
  // TTL does not read the empty copy back.
  //
  // An 'unavailable' status is not polled: that means the broker is down, and
  // asking again is not the fix.
  useEffect(() => {
    if (!awaitingGeneration) {
      return undefined
    }
    let alive = true
    let ticks = 0
    const timer = window.setInterval(() => {
      if (!alive) {
        return
      }
      if (ticks >= GENERATION_POLL_MAX_TICKS) {
        window.clearInterval(timer)
        return
      }
      ticks += 1
      void Promise.allSettled([
        fetchPredictionBySymbol(symbol),
        fetchLightGBMPredictionBySymbol(symbol),
      ]).then(([heuristic, lightgbm]) => {
        if (!alive) {
          return
        }
        const hasRows = (result: PromiseSettledResult<PredictionStockDto | LightGBMPredictionStockDto | null>) =>
          result.status === 'fulfilled' && (result.value?.results.length ?? 0) > 0
        if (!hasRows(heuristic) && !hasRows(lightgbm)) {
          return
        }
        const detail = detailRef.current
        if (detail) {
          seed(`stock/${symbol}/detail`, {
            ...detail,
            prediction: heuristic.status === 'fulfilled' ? heuristic.value : detail.prediction,
            lightgbm: lightgbm.status === 'fulfilled' ? lightgbm.value : detail.lightgbm,
          })
        }
        setReloadToken((token) => token + 1)
      })
    }, GENERATION_POLL_MS)

    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [awaitingGeneration, symbol])

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

  const filteredAssetOptions = useMemo(() => {
    const keyword = assetSearch.trim().toLowerCase()
    if (!keyword) {
      return assetOptions
    }
    return assetOptions.filter((asset) => {
      const symbolMatch = asset.symbol.toLowerCase().includes(keyword)
      const nameMatch = asset.name.toLowerCase().includes(keyword)
      return symbolMatch || nameMatch
    })
  }, [assetOptions, assetSearch])

  // Only shown when there is nothing stored to display; a model family that has
  // its rows says nothing, because there is nothing the reader needs excusing for.
  const generationNotice = useMemo(() => {
    if (awaitingGeneration) {
      return t('stock.generating')
    }
    if ([heuristicPrediction, lightgbmPrediction].some(
      (payload) => payload?.generation?.status === 'unavailable' && (payload?.results.length ?? 0) === 0,
    )) {
      return t('stock.generationUnavailable')
    }
    return null
  }, [awaitingGeneration, heuristicPrediction, lightgbmPrediction, t])

  return (
    <section>
      <header className="page-header">
        <h2>{t('stock.title')} · {symbol}{stockName ? ` (${stockName})` : ''}</h2>
        <p>{t('stock.desc')}</p>
      </header>
      <div className="card">
        <label htmlFor="stock-switch">{t('stock.selector')}</label>
        <input
          id="stock-search"
          value={assetSearch}
          onChange={(event) => setAssetSearch(event.target.value)}
          placeholder={t('stock.searchPlaceholder')}
          aria-label={t('stock.searchLabel')}
        />
        <select
          id="stock-switch"
          value={symbol}
          onChange={(e) => navigate(`/stock/${e.target.value}`)}
        >
          {filteredAssetOptions.length === 0 && <option value={symbol}>{symbol}</option>}
          {filteredAssetOptions.map((asset) => (
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
      {generationNotice && <p className="status">{generationNotice}</p>}
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
