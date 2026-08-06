// The Insights tab — the advice timeline, as one chronological stack of cards.
//
// Since 2026-08-06 this renders /api/insights/timeline, not just the live
// /current view, so a page reload no longer wipes the history: cards cover the
// same PAST_WINDOWS as the historical metrics (live, then past 5m / 30m / 2h
// with the shipped config), newest at the top, oldest at the bottom, at most
// max_actions cards per time base. There is deliberately NO window toggle —
// the age is on the card: the top-right label names the card's time base, and
// the severity-coloured left edge fades with age (live = full strength,
// oldest = most faded), so recency reads at a glance without any control.
//
// Card anatomy (UIUX §4): the action is the FIRST line, large and bold, so the
// eye lands on what to do before anything else. Then blank vertical space — no
// separator rule — then the rationale as ordinary sentences, then the static
// coaching cue, then the Evidence expander.
//
// DATA: /api/insights/timeline is grouped, deduped, capped per bucket and
// ordered server-side — none of that is repeated here. Like /current it drops
// `context` and the long `rationale` inside group_actions(), so the event log
// /api/insights is fetched alongside and joined on (rule_id, created_at). That
// key is exact: both routes format timestamps through the same _iso(). The join
// only recovers evidence; it never re-orders, re-filters or re-groups anything.

import { useQuery } from '@tanstack/react-query'
import { fetchAdviceTimeline, fetchInsights, type AdviceAction, type Insight } from '../lib/api'
import { POLL_ADVICE_MS } from '../lib/config'
import { evidenceEntries, evidenceLabel, formatEvidence } from '../lib/evidence'
import { timeAgo, windowLabel } from '../lib/format'
import { SeverityChip } from './bits'

const SEVERITY_BORDER = {
  info: 'var(--series-m1)',
  warning: 'var(--status-warning)',
  alert: 'var(--status-critical)',
} as const

/** Left-edge strength per age bucket: full for live, fading toward the oldest.
 *  Computed from the bucket count so a PAST_WINDOWS change reshapes the ramp
 *  automatically; the floor keeps even the oldest edge legible. */
function ageFade(bucketIdx: number, bucketCount: number): number {
  if (bucketCount <= 1) return 100
  return Math.round(100 - (bucketIdx * 68) / (bucketCount - 1))
}

/** Event-log rows are the only source of `context` + the long `rationale`. */
const joinKey = (ruleId: string, createdAt: string) => `${ruleId}|${createdAt}`

function AdviceCard({
  action,
  events,
  ageLabel,
  fadePct,
}: {
  action: AdviceAction
  events: Map<string, Insight>
  /** the card's time base: "live" or "past 5m" etc. — shown top right */
  ageLabel: string
  /** left-edge strength 0-100 (100 = live) */
  fadePct: number
}) {
  // one entry per reason, in the server's order
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
  const edge = `color-mix(in srgb, ${SEVERITY_BORDER[action.severity]} ${fadePct}%, transparent)`

  return (
    <article className="insight" style={{ borderLeftColor: edge }}>
      <header className="insight-head">
        {/* Demo posture (2026-08-05): the unvalidated-metric chip is not
            rendered — `action.unvalidated` still arrives from the API for
            when real validation lands and the marker returns. */}
        <span className="insight-chips">
          <SeverityChip severity={action.severity} />
        </span>
        <time
          className="insight-time"
          dateTime={action.updated_at}
          title={`${action.updated_at} — updated ${timeAgo(action.updated_at)}`}
        >
          {ageLabel}
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
          <span className="insight-tip-label">Coaching cue</span>
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
    queryKey: ['advice-timeline', device],
    queryFn: () => fetchAdviceTimeline(device),
    refetchInterval: POLL_ADVICE_MS,
    placeholderData: (prev) => prev, // no skeleton flash on poll (UIUX §7)
  })
  // 100 rows covers the longest PAST_WINDOWS bucket at the shipped rule
  // cadence; a join miss falls back to the short reason text, which is a
  // complete sentence, so nothing breaks if a long session outruns it.
  const log = useQuery({
    queryKey: ['insights', device, 'evidence'],
    queryFn: () => fetchInsights(device, 100),
    refetchInterval: POLL_ADVICE_MS,
    placeholderData: (prev) => prev,
  })

  if (advice.isLoading) return <p className="notice">Loading insights…</p>
  if (advice.isError) return <p className="notice">Couldn't load insights — retrying…</p>

  const buckets = advice.data?.buckets ?? []
  const events = new Map<string, Insight>()
  for (const row of log.data ?? []) {
    events.set(joinKey(row.rule_id, row.created_at), row)
  }

  // One flat chronological stack: buckets arrive newest-first, cards
  // newest-first within each. The same action_id may recur across buckets (a
  // condition that kept firing), so the key is bucket-qualified.
  const cards = buckets.flatMap((bucket, i) =>
    bucket.actions.map((action) => ({
      action,
      key: `${bucket.window}:${action.action_id}`,
      ageLabel: bucket.window === 'live' ? 'live' : windowLabel(bucket.window),
      fadePct: ageFade(i, buckets.length),
    })),
  )

  // Empty is a NORMAL state — nothing to advise. Calm, never a warning.
  if (cards.length === 0) {
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
      {cards.map((c) => (
        <AdviceCard
          key={c.key}
          action={c.action}
          events={events}
          ageLabel={c.ageLabel}
          fadePct={c.fadePct}
        />
      ))}
    </div>
  )
}
