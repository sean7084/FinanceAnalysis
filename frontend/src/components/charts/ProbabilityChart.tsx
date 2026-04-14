import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { useI18n } from '../../i18n'

interface ProbabilityChartProps {
  title?: string
  data: Array<{ horizon: string; up: number; flat: number; down: number }>
}

export function ProbabilityChart({ title = 'Multi-Horizon Probabilities', data }: ProbabilityChartProps) {
  const { t } = useI18n()

  return (
    <div className="card chart-card">
      <h3>{title || t('chart.probability')}</h3>
      {data.length === 0 && <p className="status">{t('chart.noProb')}</p>}
      <ResponsiveContainer width="100%" height={280}>
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
          <XAxis dataKey="horizon" stroke="#9eb2c8" />
          <YAxis stroke="#9eb2c8" domain={[0, 1]} />
          <Tooltip />
          <Bar dataKey="up" stackId="a" fill="#18b26a" />
          <Bar dataKey="flat" stackId="a" fill="#e3ae3b" />
          <Bar dataKey="down" stackId="a" fill="#e05a5a" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
