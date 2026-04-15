import { useEffect, useMemo, useState } from 'react'
import {
  fetchEnsembleWeights,
  fetchLightGBMFeatureImportanceTrends,
  fetchLightGBMModels,
  fetchLightGBMPredictions,
  fetchModelVersions,
  type FeatureImportanceTrendGroupDto,
  hasAnyAuthCredential,
  type EnsembleWeightSnapshotDto,
  type LightGBMModelArtifactDto,
  type LightGBMPredictionDto,
  type ModelVersionDto,
} from '../lib/api'
import { useI18n } from '../i18n'

function topFeatureSummary(featureImportance: Record<string, number>) {
  const entries = Object.entries(featureImportance)
    .sort((left, right) => right[1] - left[1])
    .slice(0, 3)

  if (entries.length === 0) {
    return '--'
  }

  return entries.map(([name, value]) => `${name} (${value.toFixed(2)})`).join(', ')
}

export function ModelMonitoringPage() {
  const { t } = useI18n()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [modelVersions, setModelVersions] = useState<ModelVersionDto[]>([])
  const [lightgbmModels, setLightgbmModels] = useState<LightGBMModelArtifactDto[]>([])
  const [lightgbmPredictions, setLightgbmPredictions] = useState<LightGBMPredictionDto[]>([])
  const [ensembleWeights, setEnsembleWeights] = useState<EnsembleWeightSnapshotDto[]>([])
  const [featureTrends, setFeatureTrends] = useState<FeatureImportanceTrendGroupDto[]>([])

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        setLoading(true)
        const [versions, models, predictions, weights, trends] = await Promise.all([
          fetchModelVersions(undefined, 20),
          fetchLightGBMModels(20),
          fetchLightGBMPredictions(40),
          fetchEnsembleWeights(20),
          fetchLightGBMFeatureImportanceTrends(undefined, 5, 5),
        ])

        if (!alive) {
          return
        }

        setModelVersions(versions)
        setLightgbmModels(models)
        setLightgbmPredictions(predictions)
        setEnsembleWeights(weights)
        setFeatureTrends(trends.results)
        setError(null)
      } catch {
        if (alive) {
          setError(hasAnyAuthCredential() ? t('models.loadError') : `${t('settings.desc')} (${t('nav.settings')})`)
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
  }, [])

  const comparisonRows = useMemo(() => {
    return lightgbmPredictions.slice(0, 12).map((row) => ({
      key: `${row.asset_symbol}-${row.date}-${row.horizon_days}`,
      asset: `${row.asset_symbol} ${row.asset_name}`,
      date: row.date,
      horizon: row.horizon_days,
      confidence: Number(row.confidence).toFixed(3),
      label: row.predicted_label,
      topFeatures: topFeatureSummary(row.feature_snapshot),
    }))
  }, [lightgbmPredictions])

  const featureTrendRows = useMemo(() => {
    return featureTrends.flatMap((group) => {
      return group.feature_trends.map((trend) => {
        const latestSnapshot = trend.snapshots[trend.snapshots.length - 1]
        return {
          key: `${group.horizon_days}-${trend.feature_name}`,
          horizon: `${group.horizon_days}D`,
          feature: trend.feature_name,
          modelVersion: latestSnapshot?.model_version ?? '--',
          importance: latestSnapshot ? Number(latestSnapshot.importance_score).toFixed(3) : '--',
          rank: latestSnapshot?.importance_rank ?? '--',
          trainedAt: latestSnapshot?.trained_at?.slice(0, 10) ?? '--',
        }
      })
    })
  }, [featureTrends])

  return (
    <section>
      <header className="page-header">
        <h2>{t('models.title')}</h2>
        <p>{t('models.desc')}</p>
      </header>

      {loading && <p className="status">{t('common.loading')}</p>}
      {error && <p className="status disconnected">{error}</p>}

      <div className="card">
        <h3>{t('models.versions')}</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>{t('models.modelType')}</th>
              <th>{t('models.version')}</th>
              <th>{t('models.status')}</th>
              <th>{t('models.window')}</th>
              <th>{t('models.featureCount')}</th>
            </tr>
          </thead>
          <tbody>
            {modelVersions.map((row) => (
              <tr key={`${row.model_type}-${row.version}`}>
                <td>{row.model_type}</td>
                <td>{row.version}{row.is_active ? ` · ${t('models.active')}` : ''}</td>
                <td>{row.status}</td>
                <td>{row.training_window_start ?? '--'} → {row.training_window_end ?? '--'}</td>
                <td>{row.feature_schema.length}</td>
              </tr>
            ))}
            {modelVersions.length === 0 && !loading && (
              <tr>
                <td colSpan={5}>{t('common.noData')}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h3>{t('models.lightgbmArtifacts')}</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>{t('models.horizon')}</th>
              <th>{t('models.version')}</th>
              <th>{t('models.accuracy')}</th>
              <th>{t('models.featureCount')}</th>
              <th>{t('models.topFeatures')}</th>
            </tr>
          </thead>
          <tbody>
            {lightgbmModels.map((row) => (
              <tr key={row.id}>
                <td>{row.horizon_days}D</td>
                <td>{row.version}{row.is_active ? ` · ${t('models.active')}` : ''}</td>
                <td>{Number(row.metrics_json.accuracy ?? 0).toFixed(3)}</td>
                <td>{row.feature_names.length}</td>
                <td>{topFeatureSummary(row.feature_importance)}</td>
              </tr>
            ))}
            {lightgbmModels.length === 0 && !loading && (
              <tr>
                <td colSpan={5}>{t('common.noData')}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h3>{t('models.featureTrends')}</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>{t('models.horizon')}</th>
              <th>{t('models.feature')}</th>
              <th>{t('models.model')}</th>
              <th>{t('models.importance')}</th>
              <th>{t('models.rank')}</th>
              <th>{t('models.trainedAt')}</th>
            </tr>
          </thead>
          <tbody>
            {featureTrendRows.map((row) => (
              <tr key={row.key}>
                <td>{row.horizon}</td>
                <td>{row.feature}</td>
                <td>{row.modelVersion}</td>
                <td>{row.importance}</td>
                <td>{row.rank}</td>
                <td>{row.trainedAt}</td>
              </tr>
            ))}
            {featureTrendRows.length === 0 && !loading && (
              <tr>
                <td colSpan={6}>{t('common.noData')}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h3>{t('models.predictionComparison')}</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>{t('models.asset')}</th>
              <th>{t('models.date')}</th>
              <th>{t('models.horizon')}</th>
              <th>{t('models.confidence')}</th>
              <th>{t('models.label')}</th>
              <th>{t('models.topFeatures')}</th>
            </tr>
          </thead>
          <tbody>
            {comparisonRows.map((row) => (
              <tr key={row.key}>
                <td>{row.asset}</td>
                <td>{row.date}</td>
                <td>{row.horizon}D</td>
                <td>{row.confidence}</td>
                <td>{row.label}</td>
                <td>{row.topFeatures}</td>
              </tr>
            ))}
            {comparisonRows.length === 0 && !loading && (
              <tr>
                <td colSpan={6}>{t('common.noData')}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h3>{t('models.ensembleWeights')}</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>{t('models.date')}</th>
              <th>{t('models.lightgbmWeight')}</th>
              <th>{t('models.heuristicWeight')}</th>
              <th>{t('models.lstmWeight')}</th>
              <th>{t('models.basisMetrics')}</th>
            </tr>
          </thead>
          <tbody>
            {ensembleWeights.map((row) => (
              <tr key={row.id}>
                <td>{row.date}</td>
                <td>{Number(row.lightgbm_weight).toFixed(4)}</td>
                <td>{Number(row.heuristic_weight).toFixed(4)}</td>
                <td>{Number(row.lstm_weight).toFixed(4)}</td>
                <td>{Object.entries(row.basis_metrics).map(([key, value]) => `${key}: ${Number(value).toFixed(3)}`).join(', ') || '--'}</td>
              </tr>
            ))}
            {ensembleWeights.length === 0 && !loading && (
              <tr>
                <td colSpan={5}>{t('common.noData')}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}