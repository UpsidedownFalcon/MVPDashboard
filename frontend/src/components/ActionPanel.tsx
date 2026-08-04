// Current advice panel — GET /api/insights/current (BACKEND_SCHEMA §3,
// ANALYTICS §4.6–4.7). This is the STATE view: what to do right now.
//
// Layout is the point: every imperative headline at the top in large type, a
// clear gap, then the supporting reasons as smaller bullets beneath. The
// reasons are ONE short sentence each, sized to be read in full — there is
// deliberately no expander, no "show more", no <details> in this panel. If a
// bullet ever doesn't fit, the layout is what changes.
//
// The backend has already deduped, ranked by severity and capped at
// max_actions, so nothing here re-sorts or re-filters: duplicating that logic
// would drift from it. Bullets are flattened in the order given
// (actions.flatMap), which keeps each action's reasons adjacent.

import { useQuery } from '@tanstack/react-query'
import { FlaskConical } from 'lucide-react'
import { fetchCurrentAdvice, type AdviceReason } from '../lib/api'
import { POLL_ADVICE_MS } from '../lib/config'
import { timeAgo } from '../lib/format'
import type { Severity } from '../lib/metrics'
import { SeverityChip } from './bits'

const SEVERITY_VAR: Record<Severity, string> = {
  info: '--series-m1',
  warning: '--status-warning',
  alert: '--status-critical',
}

/** Bullets carry their own severity on the marker — colour never touches the
 *  sentence itself (same convention as InsightFeed). */
function ReasonBullet({ reason }: { reason: AdviceReason }) {
  return (
    <li className="advice-reason">
      <span
        className="advice-bullet"
        style={{ background: `var(${SEVERITY_VAR[reason.severity]})` }}
        aria-hidden
      />
      <span className="advice-reason-text">
        {reason.text}
        {reason.unvalidated && (
          <span
            className="advice-reason-unvalidated"
            title="From Movement Control / L-R Balance — no real-world validation yet (biomech SPEC §11.1)"
          >
            <FlaskConical aria-hidden /> unvalidated
          </span>
        )}
      </span>
    </li>
  )
}

export default function ActionPanel({ device }: { device: string }) {
  const query = useQuery({
    queryKey: ['advice', device],
    queryFn: () => fetchCurrentAdvice(device),
    refetchInterval: POLL_ADVICE_MS,
    // hold the previous render across polls — no skeleton flash (UIUX §7)
    placeholderData: (prev) => prev,
  })

  if (query.isLoading) return <p className="notice">Loading advice…</p>
  if (query.isError) return <p className="notice">Couldn't load advice — retrying…</p>

  const actions = query.data?.actions ?? []

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

  const reasons = actions.flatMap((a) => a.reasons)

  return (
    <div className="advice" aria-live="polite">
      <div className="advice-actions">
        {actions.map((a) => (
          <div key={a.action_id} className="advice-action">
            <div className="advice-action-head">
              {/* headline verbatim — never re-worded or appended to */}
              <h2 className="advice-headline">{a.action}</h2>
              <SeverityChip severity={a.severity} />
              {a.unvalidated && (
                <span
                  className="chip flag flag-muted"
                  title="Every reason behind this comes from Movement Control / L-R Balance, which have no real-world validation — synthetic fixtures only (biomech SPEC §11.1). Treat as a prompt to look, not a finding."
                >
                  <FlaskConical aria-hidden /> unvalidated metric
                </span>
              )}
            </div>
            <div className="advice-updated">updated {timeAgo(a.updated_at)}</div>
          </div>
        ))}
      </div>

      <ul className="advice-reasons">
        {reasons.map((r) => (
          <ReasonBullet key={`${r.rule_id}-${r.created_at}`} reason={r} />
        ))}
      </ul>
    </div>
  )
}
