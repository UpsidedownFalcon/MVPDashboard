// Thin ECharts host: transparent background, resize-aware, disposes cleanly.

import * as echarts from 'echarts'
import { useEffect, useRef } from 'react'

export default function EChart({
  option,
  height,
  ariaLabel,
}: {
  option: echarts.EChartsOption
  height: number
  ariaLabel?: string
}) {
  const hostRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const chart = echarts.init(host, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    const ro = new ResizeObserver(() => chart.resize())
    ro.observe(host)
    return () => {
      ro.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    chartRef.current?.setOption(option, { notMerge: true })
  }, [option])

  return (
    <div
      ref={hostRef}
      style={{ height, width: '100%' }}
      role="img"
      aria-label={ariaLabel}
    />
  )
}
