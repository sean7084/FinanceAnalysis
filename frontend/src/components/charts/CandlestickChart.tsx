import { useEffect, useRef } from 'react'
import { ColorType, createChart, type IChartApi } from 'lightweight-charts'
import { useI18n } from '../../i18n'

interface CandlestickPoint {
  time: string
  open: number
  high: number
  low: number
  close: number
}

interface CandlestickChartProps {
  data: CandlestickPoint[]
}

export function CandlestickChart({ data }: CandlestickChartProps) {
  const { t } = useI18n()
  const hostRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    if (!hostRef.current) return

    const chart = createChart(hostRef.current, {
      width: hostRef.current.clientWidth,
      height: 360,
      layout: {
        background: { type: ColorType.Solid, color: '#0f1a28' },
        textColor: '#d9e4ef',
      },
      grid: {
        vertLines: { color: 'rgba(217,228,239,0.08)' },
        horzLines: { color: 'rgba(217,228,239,0.08)' },
      },
    })

    const series = chart.addCandlestickSeries({
      upColor: '#22c76f',
      downColor: '#ff5e5e',
      borderVisible: false,
      wickUpColor: '#22c76f',
      wickDownColor: '#ff5e5e',
    })

    series.setData(data)
    chart.timeScale().fitContent()
    chartRef.current = chart

    const handleResize = () => {
      if (!hostRef.current || !chartRef.current) return
      chartRef.current.applyOptions({ width: hostRef.current.clientWidth })
    }

    window.addEventListener('resize', handleResize)
    return () => {
      window.removeEventListener('resize', handleResize)
      chart.remove()
    }
  }, [data])

  return (
    <div className="card chart-card">
      <h3>{t('chart.kline')}</h3>
      {data.length === 0 && <p className="status">{t('chart.noKline')}</p>}
      <div ref={hostRef} />
    </div>
  )
}
