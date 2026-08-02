// uPlot wrapper: m1..m5 + composite on a fixed 0-100 axis, nulls = gaps.

import { useEffect, useRef } from 'react'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import type { DeviceBuffer } from './useLive'
import { BUFFER_S } from './useLive'

const COLORS = ['#888', '#c96', '#699', '#969', '#396', '#36c']
const LABELS = ['m1 Impact', 'm2 Loading Rate', 'm3 Accum. Load',
  'm4 Movement Ctrl', 'm5 L/R Balance', 'composite (Injury Risk)']

export default function LiveChart({ buffer }: { buffer?: DeviceBuffer }) {
  const el = useRef<HTMLDivElement>(null)
  const plot = useRef<uPlot | null>(null)

  useEffect(() => {
    if (!el.current) return
    const series: uPlot.Series[] = [
      {},
      ...LABELS.map((label, i) => ({
        label,
        stroke: i === 5 ? '#e33' : COLORS[i],
        width: i === 5 ? 2 : 1,
        spanGaps: false, // nulls must render as gaps, never interpolated/zero
      })),
    ]
    plot.current = new uPlot(
      {
        width: 900,
        height: 240,
        series,
        scales: {
          x: { time: true },
          y: { range: [0, 100] }, // SPEC: all metrics 0-100
        },
        axes: [{}, { label: '0-100' }],
      },
      [[], [], [], [], [], [], []],
      el.current,
    )
    return () => {
      plot.current?.destroy()
      plot.current = null
    }
  }, [])

  useEffect(() => {
    if (!plot.current || !buffer) return
    const now = Date.now() / 1000
    plot.current.setData(buffer.data as uPlot.AlignedData, false)
    plot.current.setScale('x', { min: now - BUFFER_S, max: now })
  }, [buffer])

  return <div ref={el} />
}
