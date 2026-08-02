// One WebSocket for all devices; per-device rolling buffers of the last 60s.
// Charts render nulls as GAPS, never 0 (BACKEND_SCHEMA §2 / biomech SPEC §10).
// Unknown tick keys (e.g. "f" flags) are ignored.

import { useEffect, useRef, useState } from 'react'
import { fetchRecent } from './api'

export const BUFFER_S = 60
const SERIES = 7 // m1..m5, composite + leading time row

// uPlot AlignedData: [t, m1, m2, m3, m4, m5, composite]
export type LiveData = [number[], ...(number | null)[][]]

export interface DeviceBuffer {
  data: LiveData
  version: number
}

function emptyData(): LiveData {
  return [[], [], [], [], [], [], []] as LiveData
}

interface Tick {
  type: string
  t: string
  dev: string
  m: (number | null)[]
  c: number
  q: number
}

export function useLive(deviceIds: string[]): Record<string, DeviceBuffer> {
  const buffers = useRef<Record<string, LiveData>>({})
  const [snapshot, setSnapshot] = useState<Record<string, DeviceBuffer>>({})
  const backfilled = useRef<Set<string>>(new Set())

  const push = (dev: string, t: number, m: (number | null)[], c: number) => {
    const buf = (buffers.current[dev] ??= emptyData())
    buf[0].push(t)
    for (let i = 0; i < 5; i++) (buf[i + 1] as (number | null)[]).push(m[i] ?? null)
    ;(buf[6] as (number | null)[]).push(c)
    const cutoff = t - BUFFER_S
    while (buf[0].length && buf[0][0] < cutoff)
      for (let i = 0; i < SERIES; i++) buf[i].shift()
  }

  // backfill newly seen devices from /api/metrics/recent
  useEffect(() => {
    for (const dev of deviceIds) {
      if (backfilled.current.has(dev)) continue
      backfilled.current.add(dev)
      fetchRecent(dev, BUFFER_S)
        .then((recent) => {
          if (!recent.t0 || buffers.current[dev]?.[0].length) return
          const t0 = Date.parse(recent.t0) / 1000
          const buf = (buffers.current[dev] = emptyData())
          for (const row of recent.rows) {
            buf[0].push(t0 + (row[0] as number) / 1000)
            for (let i = 1; i <= 6; i++)
              (buf[i] as (number | null)[]).push((row[i] as number | null) ?? null)
          }
        })
        .catch(() => backfilled.current.delete(dev))
    }
  }, [deviceIds])

  // one WS + a 300ms repaint timer (crude but plenty for a disposable UI)
  useEffect(() => {
    let ws: WebSocket | null = null
    let closed = false
    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/ws/live`)
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data) as Tick
        if (msg.type !== 'tick') return
        push(msg.dev, Date.parse(msg.t) / 1000, msg.m, msg.c)
      }
      ws.onclose = () => {
        if (!closed) setTimeout(connect, 1000)
      }
    }
    connect()
    const timer = setInterval(() => {
      setSnapshot(
        Object.fromEntries(
          Object.entries(buffers.current).map(([dev, data]) => [
            dev,
            { data, version: data[0].length ? data[0][data[0].length - 1] : 0 },
          ]),
        ),
      )
    }, 300)
    return () => {
      closed = true
      clearInterval(timer)
      ws?.close()
    }
  }, [])

  return snapshot
}
