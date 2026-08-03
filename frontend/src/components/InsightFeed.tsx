// Action-first insight feed (UIUX §4 tab 1): imperative ACTION headline in
// primary ink (color lives in the chip + left border, never the text),
// rationale beneath, evidence expander with the values that fired the rule.
//
// Evidence keys are translated to plain language — `z` and `sd` are jargon a
// trainer should never have to decode (z renders as "vs their normal range").
// `context.unvalidated === true` (movement_quality: m4/m5) gets a visible
// marker: those metrics have NO real-world validation (SPEC §11.1), and that
// must be apparent at a glance, not buried in the rationale prose.

import { useQuery } from '@tanstack/react-query'
import { FlaskConical } from 'lucide-react'
import { fetchInsights } from '../lib/api'
import { POLL_INSIGHTS_MS } from '../lib/config'
import { horizonLabel, pct, timeAgo } from '../lib/format'
import { SeverityChip } from './bits'

const SEVERITY_BORDER = {
  info: 'var(--series-m1)',
  warning: 'var(--status-warning)',
  alert: 'var(--status-critical)',
} as const

/** Keys consumed elsewhere on the card — never listed in the expander. */
const HIDDEN_KEYS = new Set(['severity', 'unvalidated'])

/** Plain-language labels; anything unlisted falls back to the raw key. */
const EVIDENCE_LABELS: Record<string, string> = {
  metric: 'metric id',
  metric_name: 'metric',
  window: 'window',
  baseline_window: 'baseline window',
  value: 'value',
  baseline: 'their baseline',
  sd: 'their usual spread',
  z: 'vs their normal range',
  quality: 'link quality',
  baseline_quality: 'baseline link quality',
  coverage: 'window coverage',
  horizon: 'horizon',
  projected: 'projected',
  settles_at: 'settles at',
  threshold: 'threshold',
  composite_avg: 'risk average',
  pred: 'projected',
  trend: 'trend',
  ratio: 'change',
}

/** Preferred display order; unknown keys follow, in server order. */
const EVIDENCE_ORDER = [
  'metric_name', 'window', 'value', 'baseline', 'baseline_window', 'z',
  'threshold', 'horizon', 'projected', 'settles_at', 'coverage', 'quality',
  'baseline_quality', 'trend', 'sd', 'metric',
]

function formatEvidence(key: string, v: unknown): string {
  if (v == null) return '—'
  switch (key) {
    case 'z': {
      const z = Number(v)
      // "sd" spelled out as spread: ±N.N× their usual spread
      return `${z >= 0 ? '+' : '−'}${Math.abs(z).toFixed(1)}× their usual spread`
    }
    case 'quality':
    case 'baseline_quality':
    case 'coverage':
      return pct(Number(v))
    case 'ratio':
      return `${Math.round(Number(v) * 100)}% of baseline`
    case 'horizon':
      return horizonLabel(String(v))
    default:
      return typeof v === 'number' ? v.toFixed(1) : String(v)
  }
}

function evidenceEntries(context: Record<string, unknown>): [string, unknown][] {
  const keys = Object.keys(context).filter((k) => !HIDDEN_KEYS.has(k))
  keys.sort((a, b) => {
    const ia = EVIDENCE_ORDER.indexOf(a)
    const ib = EVIDENCE_ORDER.indexOf(b)
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib)
  })
  return keys.map((k) => [k, context[k]])
}

export default function InsightFeed({ device }: { device: string }) {
  const query = useQuery({
    queryKey: ['insights', device],
    queryFn: () => fetchInsights(device, 20),
    refetchInterval: POLL_INSIGHTS_MS,
  })

  if (query.isLoading) return <p className="notice">Loading insights…</p>
  if (query.isError) return <p className="notice">Couldn't load insights — retrying…</p>

  return (
    // aria-live: newly polled alerts are announced without stealing focus
    // (UIUX §12)
    <div className="insight-feed" aria-live="polite">
      {!query.data?.length && (
        <p className="notice">No insights yet — all metrics in normal range.</p>
      )}
      {query.data?.map((i) => {
        const unvalidated = i.context?.unvalidated === true
        return (
          <article
            key={i.insight_id}
            className={`insight ${unvalidated ? 'insight-unvalidated' : ''}`}
            style={{ borderLeftColor: SEVERITY_BORDER[i.severity] }}
          >
            <header className="insight-head">
              <span className="insight-chips">
                <SeverityChip severity={i.severity} />
                {unvalidated && (
                  <span
                    className="chip flag flag-muted"
                    title="Movement Control / L-R Balance have no real-world validation — synthetic test fixtures only (biomech SPEC §11.1). Treat as a prompt to look, not a finding."
                  >
                    <FlaskConical aria-hidden /> unvalidated metric
                  </span>
                )}
              </span>
              <time className="insight-time" dateTime={i.created_at} title={i.created_at}>
                {timeAgo(i.created_at)}
              </time>
            </header>
            <h3 className="insight-action">{i.action ?? i.message}</h3>
            {i.rationale ? (
              <p className="insight-rationale">{i.rationale}</p>
            ) : (
              i.action && <p className="insight-rationale">{i.message}</p>
            )}
            {i.context && evidenceEntries(i.context).length > 0 && (
              <details className="insight-evidence">
                <summary>Evidence</summary>
                <dl>
                  {evidenceEntries(i.context).map(([k, v]) => (
                    <div key={k}>
                      <dt>{EVIDENCE_LABELS[k] ?? k.replace(/_/g, ' ')}</dt>
                      <dd>{formatEvidence(k, v)}</dd>
                    </div>
                  ))}
                </dl>
              </details>
            )}
          </article>
        )
      })}
    </div>
  )
}
