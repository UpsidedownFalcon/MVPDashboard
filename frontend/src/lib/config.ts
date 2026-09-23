// Frontend constants (UIUX §13). Anything the backend owns comes from the API
// at runtime (window/horizon labels are read from responses, never hardcoded).

/** A silence longer than this counts as a REAL absence — the calibration
 *  badge's re-arm threshold (CalibrationBadge.tsx). It no longer hides
 *  devices anywhere: offline athletes stay in the sidebar and on the grid
 *  with their stored data (user decision 2026-08-06, reversing 2026-08-02). */
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

/** Static sensor line shown in place of the quality meter + per-sensor rates
 *  (STAGE4 R1, user decision 2026-09-12). Deliberately a LITERAL for BILATERAL
 *  rigs (and the demo soldiers): it is not computed from the device and does
 *  not change with sensor count or rate. Sleeve rigs re-open R1 by the user
 *  decision of 2026-09-23 - their line is computed in lib/rig.ts. */
export const SENSOR_SUMMARY_TEXT = '4 sensors | 6400Hz logging'

/** Aggregate on-device logging rate, the tail of every sensor summary line.
 *  lib/rig.ts composes the sleeve variants from it. */
export const LOGGING_RATE_TEXT = '6400Hz logging'

/** Full-scale ranges a knee sleeve accepts (firmware imu_fs_valid(); mirrored
 *  in backend/common/kinds.py). The dashboard offers exactly these and nothing
 *  else - an out-of-set value is rejected by the API with 422. */
export const ACCEL_FS_ALLOWED_G = [2, 4, 8, 16, 32] as const
export const GYRO_FS_ALLOWED_DPS = [125, 250, 500, 1000, 2000, 4000] as const
