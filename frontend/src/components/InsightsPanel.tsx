// The Insights tab — the advice standing right now, as cards.
//
// Card anatomy (UIUX §4): the action is the FIRST line, large and bold, so the
// eye lands on what to do before anything else. Then blank vertical space — no
// separator rule — then the rationale as ordinary sentences, then the static
// coaching cue, then the Evidence expander.
//
// DATA: /api/insights/current is the state view (grouped, deduped, ranked and
// capped server-side — none of that is repeated here). It deliberately drops
// `context` and the long `rationale` inside group_actions(), so the event log
// /api/insights is fetched alongside and joined on (rule_id, created_at). That
// key is exact: both routes format timestamps through the same _iso(). The join
// only recovers evidence; it never re-orders, re-filters or re-groups anything.

import { useQuery } from '@tanstack/react-query'
import { FlaskConical } from 'lucide-react'
import { fetchCurrentAdvice, fetchInsights, type AdviceAction, type Insight } from '../lib/api'
import { POLL_ADVICE_MS } from '../lib/config'
import { evidenceEntries, evidenceLabel, formatEvidence } from '../lib/evidence'
import { timeAgo } from '../lib/format'
import { SeverityChip } from './bits'

const SEVERITY_BORDER = {
  info: 'var(--series-m1)',
  warning: 'var(--status-warning)',
  alert: 'var(--status-critical)',
} as const

/** Event-log rows are the only source of `context` + the long `rationale`. */
const joinKey = (ruleId: string, createdAt: string) => `${ruleId}|${createdAt}`

function AdviceCard({
  action,
  events,
}: {
  action: AdviceAction
  events: Map<string, Insight>
}) {
  // one entry per reason, in the server's severity order
  const detail = action.reasons.map((r) => {
    const event = events.get(joinKey(r.rule_id, r.created_at))
    return {
      reason: r,
      // long form when the join resolves; the short reason is already a
      // complete sentence, so it is a clean fallback
      text: event?.rationale ?? r.text,
      context: event?.context ?? null,
    }
  })
  const evidence = detail.filter((d) => evidenceEntries(d.context).length > 0)

  return (
    <article className="insight" style={{ borderLeftColor: SEVERITY_BORDER[action.severity] }}>
      <header className="insight-head">
        <span className="insight-chips">
          <SeverityChip severity={action.severity} />
          {action.unvalidated && (
            <span
              className="chip flag flag-muted"
              title="Every reason behind this comes from Movement Control / L-R Balance, which have no real-world validation — synthetic fixtures only (biomech SPEC §11.1). Treat as a prompt to look, not a finding."
            >
              <FlaskConical aria-hidden /> unvalidated metric
            </span>
          )}
        </span>
        <time className="insight-time" dateTime={action.updated_at} title={action.updated_at}>
          updated {timeAgo(action.updated_at)}
        </time>
      </header>

      {/* first line of the card: what to do */}
      <h3 className="insight-action">{action.action}</h3>

      {/* blank space, then the why — no separator rule between them */}
      <div className="insight-rationale">
        {detail.map((d) => (
          <p key={joinKey(d.reason.rule_id, d.reason.created_at)}>{d.text}</p>
        ))}
      </div>

      {action.tip && (
        <div className="insight-tip">
          <span className="insight-tip-label">General cue — not measured</span>
          <p>{action.tip}</p>
        </div>
      )}

      {evidence.length > 0 && (
        <details className="insight-evidence">
          <summary>Evidence</summary>
          {evidence.map((d) => (
            <dl key={joinKey(d.reason.rule_id, d.reason.created_at)}>
              {evidence.length > 1 && (
                <div className="evidence-group">
                  <dt className="evidence-group-label">{d.reason.rule_id.replace(/_/g, ' ')}</dt>
                  <dd />
                </div>
              )}
              {evidenceEntries(d.context).map(([k, v]) => (
                <div key={k}>
                  <dt>{evidenceLabel(k)}</dt>
                  <dd>{formatEvidence(k, v)}</dd>
                </div>
              ))}
            </dl>
          ))}
        </details>
      )}
    </article>
  )
}

export default function InsightsPanel({ device }: { device: string }) {
  const advice = useQuery({
    queryKey: ['advice', device],
    queryFn: () => fetchCurrentAdvice(device),
    refetchInterval: POLL_ADVICE_MS,
    placeholderData: (prev) => prev, // no skeleton flash on poll (UIUX §7)
  })
  // 50 rows comfortably covers the 150s hold window at an 8-rule catalogue
  const log = useQuery({
    queryKey: ['insights', device, 'evidence'],
    queryFn: () => fetchInsights(device, 50),
    refetchInterval: POLL_ADVICE_MS,
    placeholderData: (prev) => prev,
  })

  if (advice.isLoading) return <p className="notice">Loading insights…</p>
  if (advice.isError) return <p className="notice">Couldn't load insights — retrying…</p>

  const actions = advice.data?.actions ?? []
  const events = new Map<string, Insight>()
  for (const row of log.data ?? []) {
    events.set(joinKey(row.rule_id, row.created_at), row)
  }

  // Empty is a NORMAL state — nothing to advise. Calm, never a warning.
  if (actions.length === 0) {
    return (
      <div className="advice advice-empty">
        <p className="advice-empty-title">Nothing to flag right now</p>
        <p className="advice-empty-sub">
          Advice appears here within about a minute of something worth acting on.
        </p>
      </div>
    )
  }

  return (
    <div className="insight-feed" aria-live="polite">
      {actions.map((a) => (
        <AdviceCard key={a.action_id} action={a} events={events} />
      ))}
    </div>
  )
}
