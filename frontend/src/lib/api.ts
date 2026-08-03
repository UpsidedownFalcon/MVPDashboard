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
