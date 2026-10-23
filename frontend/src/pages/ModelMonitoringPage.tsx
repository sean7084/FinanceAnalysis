import { useMemo } from 'react'
import {
  fetchEnsembleWeights,
  fetchLightGBMFeatureImportanceTrends,
  fetchLightGBMModels,
  fetchModelVersions,
  type FeatureImportanceTrendGroupDto,
  type EnsembleWeightSnapshotDto,
  type LightGBMModelArtifactDto,
  type ModelVersionDto,
} from '../lib/api'
import { TableSkeleton } from '../components/layout/TableSkeleton'
import { useApiResource } from '../hooks/useApiResource'
import { useI18n } from '../useI18n'

interface ModelMonitoringData {
  modelVersions: ModelVersionDto[]
  lightgbmModels: LightGBMModelArtifactDto[]
  ensembleWeights: EnsembleWeightSnapshotDto[]
  featureTrends: FeatureImportanceTrendGroupDto[]
}

// Module scope so the "no rows yet" value keeps one identity, and so the label map
// is one object rather than a literal rebuilt per render.
const EMPTY_MONITORING: ModelMonitoringData = {
  modelVersions: [],
  lightgbmModels: [],
  ensembleWeights: [],
  featureTrends: [],
}

// `model_type` is a Django enum, so the page would otherwise show `ETF_LIGHTGBM`
// to a user who has never seen that string anywhere else in the app.
const MODEL_TYPE_LABELS: Record<string, string> = {
  HEURISTIC: 'Heuristic',
  LIGHTGBM: 'LightGBM',
  LSTM: 'LSTM',
  ENSEMBLE: 'Ensemble',
  ETF_RULE: 'ETF Rule Engine',
  ETF_LIGHTGBM: 'ETF LightGBM',
  ETF_LSTM: 'ETF LSTM',
  ETF_ENSEMBLE: 'ETF Ensemble',
}

const ETF_MODEL_TYPES = new Set(['ETF_RULE', 'ETF_LIGHTGBM', 'ETF_LSTM', 'ETF_ENSEMBLE'])

function modelTypeLabel(value: string): string {
  return MODEL_TYPE_LABELS[value] ?? value
}

function isEtfModelType(value: string): boolean {
  return ETF_MODEL_TYPES.has(value)
}

// `deployment_note` lives in the JSON `metadata` column, so its type is whatever
// the writer put there; anything that is not a string is not a note.
function deploymentNoteOf(row: ModelVersionDto): string {
  const note = row.metadata['deployment_note']
  return typeof note === 'string' ? note : ''
}

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
  // One resource whose loader keeps the four calls in flight together: they all
  // describe the same thing (current model state), so they share one cache key
  // and one revalidation instead of four independent page-level effects.
  const resource = useApiResource<ModelMonitoringData>('models/monitoring', async () => {
    const [versions, models, weights, trends] = await Promise.all([
      fetchModelVersions(undefined, 20),
      fetchLightGBMModels(20),
      fetchEnsembleWeights(20),
      fetchLightGBMFeatureImportanceTrends(undefined, 5, 5),
    ])
    return {
      modelVersions: versions,
      lightgbmModels: models,
      ensembleWeights: weights,
      featureTrends: trends.results,
    }
  })

  const { modelVersions, lightgbmModels, ensembleWeights, featureTrends } = resource.data ?? EMPTY_MONITORING

  // The ETF rows are already in this response -- ModelVersion is one table for both
  // products -- so the card below is a filter, not a second request.
  const etfVersions = modelVersions.filter((row) => isEtfModelType(row.model_type))

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

      {resource.loading && <TableSkeleton columns={5} rows={4} />}
      {resource.error && (
        <p className="status disconnected">
          {t('models.loadError')}
        </p>
      )}

      <div className="card">
        <h3>{t('models.etfRankers')}</h3>
        <p className="subtitle">{t('models.etfRankersDesc')}</p>
        {etfVersions.map((row) => {
          const note = deploymentNoteOf(row)
          return (
            <article className="runner-card" key={row.id}>
              <h3>
                {modelTypeLabel(row.model_type)} · {row.version}
                {' · '}
                <span className={row.is_active ? 'status connected' : 'status disconnected'}>
                  {row.is_active ? t('models.active') : t('models.inactive')}
                </span>
              </h3>
              {/* The note is the whole reason this card exists: it is the honest
                  record of why a READY ranker is not the one producing signals. */}
              {note && <p className="status disconnected">{note}</p>}
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{t('models.status')}</th>
                      <th>{t('models.window')}</th>
                      <th>{t('models.metric')}</th>
                      <th>{t('models.metricValue')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(row.metrics).map(([metric, value], index) => (
                      <tr key={metric}>
                        <td>{index === 0 ? row.status : ''}</td>
                        <td>
                          {index === 0
                            ? `${row.training_window_start ?? '--'} → ${row.training_window_end ?? '--'}`
                            : ''}
                        </td>
                        <td>{metric}</td>
                        <td>{String(value)}</td>
                      </tr>
                    ))}
                    {Object.keys(row.metrics).length === 0 && (
                      <tr>
                        <td colSpan={4}>{t('common.noData')}</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </article>
          )
        })}
        {etfVersions.length === 0 && !resource.loading && (
          <p className="subtitle">{t('models.etfRankersEmpty')}</p>
        )}
      </div>

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
                <td>{modelTypeLabel(row.model_type)}</td>
                <td>{row.version}{row.is_active ? ` · ${t('models.active')}` : ''}</td>
                <td>{row.status}</td>
                <td>{row.training_window_start ?? '--'} → {row.training_window_end ?? '--'}</td>
                <td>{row.feature_schema.length}</td>
              </tr>
            ))}
            {modelVersions.length === 0 && !resource.loading && (
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
            {lightgbmModels.length === 0 && !resource.loading && (
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
            {featureTrendRows.length === 0 && !resource.loading && (
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
            {ensembleWeights.length === 0 && !resource.loading && (
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