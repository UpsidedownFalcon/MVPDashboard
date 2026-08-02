// Canvas live charts (UIUX §5): uPlot, rolling window, rAF-driven, ~250ms
// render delay, nulls as gaps. One component serves the big composite chart,
// the stacked primitives, and the panel sparkline (axes off).

import { useEffect, useRef } from 'react'
import uPlot from 'uplot'
import { RENDER_DELAY_S } from '../lib/config'
import type { LiveData } from '../lib/ws'

interface Props {
  /** stable accessor into the LiveProvider buffer. */
  getData: () => LiveData | undefined
  /** which series to draw: buffer index 1..5 = m1..m5, 6 = composite. */
  seriesIdx: number
  color: string
  height: number
  /** visible window in seconds. */
  windowS: number
  axes?: boolean
  /** freeze the window (offline overlay handles messaging). */
  frozen?: boolean
  /** faint hairlines at the risk-band thresholds (composite charts). */
  bandLines?: boolean
}

const GRID = { stroke: 'rgba(255,255,255,0.06)', width: 1 }
const TICK = { stroke: 'rgba(255,255,255,0.06)', width: 1 }
const AXIS_FONT = '11px Inter, sans-serif'

export default function LiveChart({
  getData,
  seriesIdx,
  color,
  height,
  windowS,
  axes = false,
  frozen = false,
  bandLines = false,
}: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const plotRef = useRef<uPlot | null>(null)
  const frozenRef = useRef(frozen)
  frozenRef.current = frozen

  useEffect(() => {
    const host = hostRef.current
    if (!host) return

    const opts: uPlot.Options = {
      width: host.clientWidth || 300,
      height,
      pxAlign: false,
      scales: {
        x: { time: false },
        y: { range: [0, 100] },
      },
      series: [
        {},
        {
          stroke: color,
          width: 2,
          spanGaps: false, // nulls are GAPS, never interpolated (SPEC §9)
          points: { show: false },
          fill: `color-mix(in srgb, ${color} 10%, transparent)`,
        },
      ],
      axes: [
        {
          show: axes,
          stroke: '#898781',
          font: AXIS_FONT,
          grid: { show: false },
          ticks: TICK,
          values: (_u, ticks) =>
            ticks.map((t) => new Date(t * 1000).toLocaleTimeString([], { minute: '2-digit', second: '2-digit' })),
        },
        {
          show: axes,
          stroke: '#898781',
          font: AXIS_FONT,
          size: 32,
          grid: GRID,
          ticks: TICK,
          splits: () => [0, 25, 50, 75, 100],
        },
      ],
      legend: { show: false },
      cursor: { show: false },
      hooks: bandLines
        ? {
            drawAxes: [
              (u) => {
                const ctx = u.ctx
                ctx.save()
                for (const yVal of [30, 60, 80]) {
                  const y = u.valToPos(yVal, 'y', true)
                  ctx.strokeStyle = 'rgba(255,255,255,0.09)'
                  ctx.lineWidth = 1
                  ctx.beginPath()
                  ctx.moveTo(u.bbox.left, y)
                  ctx.lineTo(u.bbox.left + u.bbox.width, y)
                  ctx.stroke()
                }
                ctx.restore()
              },
            ],
          }
        : undefined,
    }

    const empty: uPlot.AlignedData = [[], []]
    const plot = new uPlot(opts, empty, host)
    plotRef.current = plot

    const ro = new ResizeObserver(() => {
      plot.setSize({ width: host.clientWidth || 300, height })
    })
    ro.observe(host)

    let raf = 0
    let lastDrawnT = -1
    const tick = () => {
      raf = requestAnimationFrame(tick)
      if (document.hidden) return // hidden tab: pause rendering (UIUX §5)
      const buf = getData()
      if (!buf || !buf[0].length) return
      const times = buf[0]
      const newestT = times[times.length - 1]
      // frozen (offline): window pins to the last data; live: slides with
      // wall-clock minus the smoothing delay (UIUX §5)
      const maxX = frozenRef.current
        ? newestT
        : Math.max(newestT, Date.now() / 1000 - RENDER_DELAY_S)
      const hasNew = newestT !== lastDrawnT
      plot.batch(() => {
        if (hasNew) {
          lastDrawnT = newestT
          plot.setData([times, buf[seriesIdx] as (number | null)[]], false)
        }
        plot.setScale('x', { min: maxX - windowS, max: maxX })
      })
    }
    raf = requestAnimationFrame(tick)

    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      plot.destroy()
      plotRef.current = null
    }
  }, [getData, seriesIdx, color, height, windowS, axes, bandLines])

  return <div ref={hostRef} className="livechart" style={{ height }} />
}
