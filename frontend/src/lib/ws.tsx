// Live data layer (UIUX §5): ONE reconnecting WebSocket for the whole app,
// per-device rolling buffers (60s @ 60Hz), REST backfill-then-splice, hidden-
// tab pause + re-backfill, and a connection-state signal for the header dot.
//
// Charts read buffers via `getBuffer()` + their own rAF loop — no React state
// churn at 60Hz. Numeric readouts use the 250ms `latest` snapshot.

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { authExpired, fetchRecent } from './api'
import {
  BACKFILL_S,
  LIVE_BUFFER_S,
  WS_BACKOFF_MAX_MS,
  WS_BACKOFF_MIN_MS,
  WS_CLOSE_UNAUTHORIZED,
} from './config'

/** uPlot AlignedData layout: [t, m1..m5, composite]. */
export type LiveData = [number[], ...(number | null)[][]]
const SERIES = 7

export type ConnState = 'connected' | 'reconnecting'

export interface DeviceLatest {
  t: number
  m: (number | null)[]
  c: number
  q: number
  flags: string[]
}

export interface StatusEvent {
  online: boolean
  last_seen: string
}

interface LiveContextValue {
  conn: ConnState
  /** 250ms snapshot of each device's newest tick. */
  latest: Record<string, DeviceLatest>
  /** Latest WS status event per device (online/offline transitions). */
  status: Record<string, StatusEvent>
  /** Stable accessor for a device's rolling buffer (chart rAF loops). */
  getBuffer: (dev: string) => LiveData | undefined
}

const LiveContext = createContext<LiveContextValue | null>(null)

interface TickMsg {
  type: 'tick'
  t: string
  dev: string
  m: (number | null)[]
  c: number
  q: number
  f?: string[] | null
}

interface StatusMsg {
  type: 'status'
  dev: string
  online: boolean
  last_seen: string
}

function emptyData(): LiveData {
  return [[], [], [], [], [], [], []] as LiveData
}

export function LiveProvider({ children }: { children: ReactNode }) {
  const buffers = useRef<Record<string, LiveData>>({})
  const flags = useRef<Record<string, string[]>>({})
  const quality = useRef<Record<string, number>>({})
  const backfilled = useRef<Set<string>>(new Set())
  const paused = useRef(false)
  const [conn, setConn] = useState<ConnState>('reconnecting')
  const [latest, setLatest] = useState<Record<string, DeviceLatest>>({})
  const [status, setStatus] = useState<Record<string, StatusEvent>>({})

  const getBuffer = useMemo(
    () => (dev: string) => buffers.current[dev],
    [],
  )

  useEffect(() => {
    let ws: WebSocket | null = null
    let closed = false
    let backoff = WS_BACKOFF_MIN_MS
    let reconnectTimer: number | undefined

    const push = (dev: string, t: number, m: (number | null)[], c: number) => {
      const buf = (buffers.current[dev] ??= emptyData())
      const times = buf[0]
      // guard against out-of-order duplicates after a backfill splice
      if (times.length && t <= times[times.length - 1]) return
      times.push(t)
      for (let i = 0; i < 5; i++) (buf[i + 1] as (number | null)[]).push(m[i] ?? null)
      ;(buf[6] as (number | null)[]).push(c)
      const cutoff = t - LIVE_BUFFER_S
      while (times.length && times[0] < cutoff) {
        for (let i = 0; i < SERIES; i++) buf[i].shift()
      }
    }

    const backfill = async (dev: string) => {
      try {
        const recent = await fetchRecent(dev, BACKFILL_S)
        if (!recent.t0) return
        const t0 = Date.parse(recent.t0) / 1000
        const buf = (buffers.current[dev] ??= emptyData())
        const firstLive = buf[0].length ? buf[0][0] : Infinity
        const pre = emptyData()
        for (const row of recent.rows) {
          const t = t0 + (row[0] as number) / 1000
          if (t >= firstLive) break
          pre[0].push(t)
          for (let i = 1; i <= 6; i++)
            (pre[i] as (number | null)[]).push((row[i] as number | null) ?? null)
        }
        // splice: backfill rows strictly before the first live row
        for (let i = 0; i < SERIES; i++) {
          ;(buf[i] as (number | null)[]).unshift(...(pre[i] as (number | null)[]))
        }
      } catch {
        backfilled.current.delete(dev) // retry on next sighting
      }
    }

    const rebackfillAll = () => {
      // reconnect / tab-return: drop stale buffers, refill the last 30s
      buffers.current = {}
      const devs = [...backfilled.current]
      backfilled.current = new Set(devs)
      devs.forEach((dev) => void backfill(dev))
    }

    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/ws/live`)
      ws.onopen = () => {
        backoff = WS_BACKOFF_MIN_MS
        setConn('connected')
        rebackfillAll()
      }
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data) as TickMsg | StatusMsg
        if (msg.type === 'status') {
          setStatus((prev) => ({
            ...prev,
            [msg.dev]: { online: msg.online, last_seen: msg.last_seen },
          }))
          return
        }
        if (msg.type !== 'tick') return
        flags.current[msg.dev] = msg.f ?? []
        quality.current[msg.dev] = msg.q
        if (!backfilled.current.has(msg.dev)) {
          backfilled.current.add(msg.dev)
          void backfill(msg.dev)
        }
        if (paused.current) return // hidden tab: newest-only via flags/latest
        push(msg.dev, Date.parse(msg.t) / 1000, msg.m, msg.c)
      }
      ws.onclose = (ev) => {
        if (ev.code === WS_CLOSE_UNAUTHORIZED) {
          authExpired()
          return
        }
        if (closed) return
        setConn('reconnecting')
        reconnectTimer = window.setTimeout(connect, backoff)
        backoff = Math.min(backoff * 2, WS_BACKOFF_MAX_MS)
      }
    }
    connect()

    const onVisibility = () => {
      paused.current = document.hidden
      if (!document.hidden) rebackfillAll()
    }
    document.addEventListener('visibilitychange', onVisibility)

    // 250ms numeric snapshot (readouts, sparkline version checks)
    const timer = window.setInterval(() => {
      const snap: Record<string, DeviceLatest> = {}
      for (const [dev, buf] of Object.entries(buffers.current)) {
        const n = buf[0].length
        if (!n) continue
        snap[dev] = {
          t: buf[0][n - 1],
          m: [1, 2, 3, 4, 5].map((i) => (buf[i] as (number | null)[])[n - 1]),
          c: (buf[6] as number[])[n - 1],
          q: quality.current[dev] ?? 1,
          flags: flags.current[dev] ?? [],
        }
      }
      setLatest(snap)
    }, 250)

    return () => {
      closed = true
      window.clearInterval(timer)
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer)
      document.removeEventListener('visibilitychange', onVisibility)
      ws?.close()
    }
  }, [])

  const value = useMemo(
    () => ({ conn, latest, status, getBuffer }),
    [conn, latest, status, getBuffer],
  )
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>
}

export function useLive(): LiveContextValue {
  const ctx = useContext(LiveContext)
  if (!ctx) throw new Error('useLive outside LiveProvider')
  return ctx
}
