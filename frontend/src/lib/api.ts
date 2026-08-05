// Typed fetch layer for every BACKEND_SCHEMA §3 route. Any 401 outside /login
// redirects to /login (APPFLOW §1.1) — the httpOnly session cookie rides along
// automatically on same-origin requests.

import type { Severity } from './metrics'

export interface Sensor {
  source_id: number
  sensor_id: number
  limb: string
  rate_hz: number
  last_seen: string | null
}

export interface Device {
  device_id: string
  display_name: string
  online: boolean
  last_seen: string | null
  quality: number | null
  /** Battery state of charge 0–100, already the LOWEST across the device's two
   *  leg MCUs (a dying unit must not hide behind a healthy one). `null` until a
   *  datagram has been seen. */
  soc: number | null
  sensors: Sensor[]
}

export interface WindowEntry {
  window: string
  from: string
  m: (number | null)[]
  /** within-window std dev of m1..m5 (additive, 2026-08-03). */
  sd: (number | null)[]
  composite: { avg: number | null; min: number | null; max: number | null; sd: number | null }
  quality: number | null
  /** observed rows ÷ expected rows for the window, 0..1. A "past 1h" average
   *  built from 4 minutes of streaming is not comparable to a full hour —
   *  surface this, never silently. */
  coverage: number | null
  trend: 'up' | 'down' | 'flat'
}

export interface HistoryBucket {
  t: string
  m: (number | null)[]
  composite: { avg: number | null; min: number | null; max: number | null }
  quality: number | null
}

export interface History {
  device_id: string
  window: string
  from: string
  bucket_s: number
  buckets: (HistoryBucket | null)[]
}

export interface ForecastPoint {
  horizon: string
  target_time: string
  pred: number
  ci_low: number | null
  ci_high: number | null
}

export interface Forecasts {
  made_at: string
  model_version: string
  /** True while the forecast is still bootstrapping from raw buckets
   *  (`trend-ols-boot-1`). Both models produce a genuine statistical prediction
   *  interval, so the band label is unaffected — but an early projection must
   *  not look as settled as a mature one. */
  provisional?: boolean
  /** Horizon set is NOT fixed: it starts 1m/2m and becomes 10m/30m/1h. Always
   *  read horizons from these points; never assume a count. */
  points: ForecastPoint[]
}

export interface Insight {
  insight_id: number
  created_at: string
  device_id: string
  severity: Severity
  rule_id: string
  message: string
  context: Record<string, unknown> | null
  action: string | null
  rationale: string | null
}

/** One supporting reason behind an action. `text` is a complete short sentence
 *  sized to render in full as a bullet — never truncate it, never hide it
 *  behind an expander (BACKEND_SCHEMA §3). */
export interface AdviceReason {
  rule_id: string
  severity: Severity
  text: string
  unvalidated: boolean
  created_at: string
}

/** One imperative headline. `action` is rendered verbatim — never re-worded
 *  or appended to. `unvalidated` is true only when EVERY reason behind it
 *  comes from m4/m5; demo posture (2026-08-05) means the UI currently does
 *  not render the marker. */
export interface AdviceAction {
  action_id: string
  action: string
  /** Static coaching cue from the action catalogue — the SAME text every time
   *  this action fires and NOT derived from this athlete's data. Rendered
   *  under the "Coaching cue" label. */
  tip: string | null
  severity: Severity
  updated_at: string
  unvalidated: boolean
  reasons: AdviceReason[]
}

/** GET /api/insights/current — the STATE view: the advice standing right now.
 *  `actions` is 0..max_actions, ordered strongest severity first; the backend
 *  has already deduped, ranked and capped, so the client must not repeat any
 *  of that. An empty `actions` array is a normal, calm state. */
export interface CurrentAdvice {
  device_id: string
  generated_at: string
  hold_s: number
  max_actions: number
  actions: AdviceAction[]
}

export interface Recent {
  device_id: string
  t0: string | null
  rows: [number, ...(number | null)[]][]
}

export interface Me {
  username: string
  role: string
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

/** Hard redirect to /login (full reload clears all client state). Exported for
 *  the WS layer, which signals expiry via close code 4401. */
export function authExpired(): void {
  if (!location.pathname.startsWith('/login')) {
    location.assign('/login')
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init)
  if (resp.status === 401 && !url.startsWith('/api/auth/login')) {
    authExpired()
  }
  if (!resp.ok) {
    let detail = `${resp.status} ${url}`
    try {
      const body = (await resp.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(resp.status, detail)
  }
  return resp.json() as Promise<T>
}

const post = <T,>(url: string, body?: unknown): Promise<T> =>
  request<T>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })

export const fetchDevices = () => request<Device[]>('/api/devices')
export const fetchWindows = (dev: string) =>
  request<{ windows: WindowEntry[] }>(`/api/metrics/windows?device=${dev}`)
export const fetchHistory = (dev: string, window: string, buckets: number) =>
  request<History>(
    `/api/metrics/history?device=${dev}&window=${encodeURIComponent(window)}&buckets=${buckets}`,
  )
export const fetchForecasts = (dev: string) =>
  request<Forecasts>(`/api/forecasts/latest?device=${dev}`)
export const fetchInsights = (dev?: string, limit = 20) =>
  request<Insight[]>(
    dev ? `/api/insights?device=${dev}&limit=${limit}` : `/api/insights?limit=${limit}`,
  )
/** device is REQUIRED — advice is per athlete. */
export const fetchCurrentAdvice = (dev: string) =>
  request<CurrentAdvice>(`/api/insights/current?device=${dev}`)
export const fetchRecent = (dev: string, seconds: number) =>
  request<Recent>(`/api/metrics/recent?device=${dev}&seconds=${seconds}`)

export const renameDevice = (dev: string, display_name: string) =>
  request<Device>(`/api/devices/${dev}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ display_name }),
  })

export const login = (username: string, password: string) =>
  post<Me>('/api/auth/login', { username, password })
export const logout = () => post<Record<string, never>>('/api/auth/logout')
export const fetchMe = () => request<Me>('/api/auth/me')
