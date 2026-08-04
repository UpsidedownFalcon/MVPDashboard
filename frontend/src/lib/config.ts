// Frontend constants (UIUX §13). Anything the backend owns comes from the API
// at runtime (window/horizon labels are read from responses, never hardcoded).

/** Devices silent longer than this vanish from panels + sidebar (user decision
 *  2026-08-02; UIUX §3). The 2s online/offline badge flip still shows first. */
export const OFFLINE_HIDE_MS = 10_000

/** Rolling live buffer per device (seconds @ 60Hz). */
export const LIVE_BUFFER_S = 60

/** REST backfill span on mount / reconnect / tab-return (UIUX §5). */
export const BACKFILL_S = 30

/** Render delay absorbing network jitter for a smooth live line (UIUX §5). */
export const RENDER_DELAY_S = 0.25

/** Poll cadences (UIUX §13). */
export const POLL_DEVICES_MS = 10_000
export const POLL_FORECASTS_MS = 60_000
export const POLL_HISTORY_MS = 60_000
export const POLL_INSIGHTS_MS = 30_000
/** GET /api/insights/current — the live advice panel. Deliberately separate
 *  from POLL_INSIGHTS_MS (which other panels share): the backend re-evaluates
 *  rules every INSIGHT_INTERVAL_S = 15 s, so polling the state view at 30 s
 *  would throw away most of that responsiveness (docs/ANALYTICS.md §4.6). */
export const POLL_ADVICE_MS = 10_000

/** WS reconnect backoff (UIUX §5). */
export const WS_BACKOFF_MIN_MS = 1_000
export const WS_BACKOFF_MAX_MS = 10_000

/** WS close code meaning "session expired" (BACKEND_SCHEMA §3). */
export const WS_CLOSE_UNAUTHORIZED = 4401

/** Max buckets requested from /api/metrics/history. The actual count per
 *  window comes from lib/format's evenBucketCount() so spans divide the
 *  window exactly (no density artifacts, no label drift). */
export const HISTORY_MAX_BUCKETS = 30
