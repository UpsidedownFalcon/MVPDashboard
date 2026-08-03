// History tab (UIUX §4 tab 2): six small-multiple bar charts (composite first)
// over GET /api/metrics/history. One period selector scopes all charts; the
// composite chart carries a min–max whisker; empty buckets are GAPS. A table
// toggle keeps every value reachable without hover.

import { useQuery } from '@tanstack/react-query'
import type { EChartsOption } from 'echarts'
import { AlertTriangle, Table2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { fetchHistory, type History, type WindowEntry } from '../lib/api'
import { HISTORY_MAX_BUCKETS, POLL_HISTORY_MS } from '../lib/config'
import { evenBucketCount, metricValue, pct, shortClock, windowLabel } from '../lib/format'
import { COMPOSITE, METRICS, type MetricMeta } from '../lib/metrics'
import EChart from './EChart'

/** Below this share of the window actually containing data, the averages are
 *  not comparable to a fully covered window — say so, never silently. */
const LOW_COVERAGE = 0.5

const AXIS_INK = '#898781'
const GRIDLINE = 'rgba(255,255,255,0.06)'

function chartOption(
  history: History,
  meta: MetricMeta,
  metricIdx: number | 'composite',
): EChartsOption {
  const labels = history.buckets.map((b) => (b ? shortClock(b.t) : '·'))
  const values = history.buckets.map((b) => {
    if (!b) return null
    return metricIdx === 'composite' ? b.composite.avg : b.m[metricIdx]
  })
  const whisker =
    metricIdx === 'composite'
      ? history.buckets.map((b, i) =>
          b && b.composite.min != null && b.composite.max != null
            ? [i, b.composite.min, b.composite.max]
            : null,
        )
      : []

  const option: EChartsOption = {
    animation: false,
    grid: { left: 30, right: 6, top: 8, bottom: 20 },
    xAxis: {
      type: 'category',
      data: labels,
      axisLine: { lineStyle: { color: GRIDLINE } },
      axisTick: { show: false },
      axisLabel: { color: AXIS_INK, fontSize: 10, interval: 'auto' },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 100,
      interval: 25,
      axisLabel: { color: AXIS_INK, fontSize: 10 },
      splitLine: { lineStyle: { color: GRIDLINE } },
    },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1b1b1b',
      borderColor: 'rgba(255,255,255,0.14)',
      textStyle: { color: '#ffffff', fontSize: 12 },
      formatter: (params) => {
        const p = (Array.isArray(params) ? params[0] : params) as {
          dataIndex: number
        }
        const b = history.buckets[p.dataIndex]
        if (!b) return 'no data'
        const v = metricIdx === 'composite' ? b.composite.avg : b.m[metricIdx]
        const extra =
          metricIdx === 'composite' && b.composite.min != null
            ? `<br/>range ${metricValue(b.composite.min)}–${metricValue(b.composite.max)}`
            : ''
        return `${shortClock(b.t)}<br/><b>${metricValue(v, 1)}</b>${extra}<br/>quality ${
          b.quality != null ? Math.round(b.quality * 100) + '%' : '—'
        }`
      },
    },
    series: [
      {
        type: 'bar',
        data: values,
        itemStyle: { color: meta.color, borderRadius: [4, 4, 0, 0] },
        barMaxWidth: 24,
        barCategoryGap: '25%',
        // a measured ZERO draws a hairline bar; only null (no data) is a gap.
        // Matters since the m3 re-anchor: short/light sessions legitimately
        // read 0 and must not look like missing data (SPEC §4.1).
        barMinHeight: 2,
      },
      ...(whisker.length
        ? [
            {
              type: 'custom',
              renderItem: (
                _params: unknown,
                api: {
                  value: (i: number) => unknown
                  coord: (v: [number, number]) => [number, number]
                },
              ) => {
                const i = Number(api.value(0))
                const lo = api.coord([i, Number(api.value(1))])
                const hi = api.coord([i, Number(api.value(2))])
                return {
                  type: 'line',
                  shape: { x1: hi[0], y1: hi[1], x2: lo[0], y2: lo[1] },
                  style: { stroke: 'rgba(255,255,255,0.45)', lineWidth: 1.5 },
                }
              },
              data: whisker.filter(Boolean) as [number, number, number][],
              z: 3,
              silent: true,
            },
          ]
        : []),
    ] as EChartsOption['series'],
  }
  return option
}

export default function HistoryBars({
  device,
  windows,
}: {
  device: string
  windows: WindowEntry[]
}) {
  const labels = windows.map((w) => w.window)
  const [window, setWindow] = useState(labels[0])
  const [showTable, setShowTable] = useState(false)

  const query = useQuery({
    queryKey: ['history', device, window],
    queryFn: () => fetchHistory(device, window, evenBucketCount(window, HISTORY_MAX_BUCKETS)),
    refetchInterval: POLL_HISTORY_MS,
    enabled: !!window,
  })

  const history = query.data
  const allMeta = useMemo(() => [COMPOSITE, ...METRICS], [])
  const nonEmpty = history?.buckets.filter(Boolean).length ?? 0
  const entry = windows.find((w) => w.window === window)
  const coverage = entry?.coverage ?? null

  return (
    <div className="history">
      <div className="history-controls">
        <div className="segmented" role="group" aria-label="History period">
          {labels.map((w) => (
            <button
              key={w}
              className={`segment ${w === window ? 'is-active' : ''}`}
              aria-pressed={w === window}
              onClick={() => setWindow(w)}
            >
              {windowLabel(w)}
            </button>
          ))}
        </div>
        {coverage != null && coverage < LOW_COVERAGE && (
          <span
            className="chip flag flag-warning"
            title={`Only ${pct(coverage)} of ${windowLabel(window)} has data — these averages are not comparable to a fully covered window`}
          >
            <AlertTriangle aria-hidden /> partial · {pct(coverage)} of window
          </span>
        )}
        <button
          className={`segment table-toggle ${showTable ? 'is-active' : ''}`}
          aria-pressed={showTable}
          onClick={() => setShowTable((s) => !s)}
          title="Table view"
        >
          <Table2 size={14} aria-hidden /> table
        </button>
      </div>

      {query.isLoading && <p className="notice">Loading history…</p>}
      {query.isError && <p className="notice">Couldn't load history — retrying…</p>}

      {history && nonEmpty === 0 && (
        <p className="notice">collecting… no data yet in {windowLabel(window)}</p>
      )}

      {history && nonEmpty > 0 && !showTable && (
        <div className="history-grid">
          {allMeta.map((m) => {
            const idx = m.id === 'composite' ? ('composite' as const) : METRICS.indexOf(m as (typeof METRICS)[number])
            const latestBucket = [...history.buckets].reverse().find(Boolean)
            const latestVal = latestBucket
              ? idx === 'composite'
                ? latestBucket.composite.avg
                : latestBucket.m[idx as number]
              : null
            return (
              <div key={m.id} className="history-cell">
                <div className="history-cell-head" title={m.tooltip}>
                  <span className="swatch" style={{ background: m.color }} aria-hidden />
                  {m.label}
                  <span className="history-cell-val">{metricValue(latestVal)}</span>
                </div>
                <EChart
                  option={chartOption(history, m, idx)}
                  height={110}
                  ariaLabel={`${m.label} history, ${windowLabel(window)}`}
                />
              </div>
            )
          })}
        </div>
      )}

      {history && nonEmpty > 0 && showTable && (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>time</th>
                <th>{COMPOSITE.short}</th>
                {METRICS.map((m) => (
                  <th key={m.id}>{m.short}</th>
                ))}
                <th>quality</th>
              </tr>
            </thead>
            <tbody>
              {history.buckets.map(
                (b, i) =>
                  b && (
                    <tr key={i}>
                      <td>{shortClock(b.t)}</td>
                      <td>{metricValue(b.composite.avg, 1)}</td>
                      {b.m.map((v, j) => (
                        <td key={j}>{metricValue(v, 1)}</td>
                      ))}
                      <td>{b.quality != null ? `${Math.round(b.quality * 100)}%` : '—'}</td>
                    </tr>
                  ),
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
