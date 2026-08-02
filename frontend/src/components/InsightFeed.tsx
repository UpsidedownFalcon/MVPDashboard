// Action-first insight feed (UIUX §4 tab 1): imperative ACTION headline in
// primary ink (color lives in the chip + left border, never the text),
// rationale beneath, evidence expander with the values that fired the rule.

import { useQuery } from '@tanstack/react-query'
import { fetchInsights } from '../lib/api'
import { POLL_INSIGHTS_MS } from '../lib/config'
import { timeAgo } from '../lib/format'
import { SeverityChip } from './bits'

const SEVERITY_BORDER = {
  info: 'var(--series-m1)',
  warning: 'var(--status-warning)',
  alert: 'var(--status-critical)',
} as const

export default function InsightFeed({ device }: { device: string }) {
  const query = useQuery({
    queryKey: ['insights', device],
    queryFn: () => fetchInsights(device, 20),
    refetchInterval: POLL_INSIGHTS_MS,
  })

  if (query.isLoading) return <p className="notice">Loading insights…</p>
  if (query.isError) return <p className="notice">Couldn't load insights — retrying…</p>
  if (!query.data?.length) {
    return <p className="notice">No insights yet — all metrics in normal range.</p>
  }

  return (
    <div className="insight-feed">
      {query.data.map((i) => (
        <article
          key={i.insight_id}
          className="insight"
          style={{ borderLeftColor: SEVERITY_BORDER[i.severity] }}
        >
          <header className="insight-head">
            <SeverityChip severity={i.severity} />
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
          {i.context && Object.keys(i.context).length > 0 && (
            <details className="insight-evidence">
              <summary>Evidence</summary>
              <dl>
                {Object.entries(i.context).map(([k, v]) => (
                  <div key={k}>
                    <dt>{k.replace(/_/g, ' ')}</dt>
                    <dd>{String(v)}</dd>
                  </div>
                ))}
              </dl>
            </details>
          )}
        </article>
      ))}
    </div>
  )
}
