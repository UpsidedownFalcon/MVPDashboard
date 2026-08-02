// Crude, DISPOSABLE stage-2 dashboard (S2-T07): every feature visible, zero
// design effort. Replaced wholesale by the stage-3 product UI.

import { useMemo, useState } from 'react'
import {
  QueryClient, QueryClientProvider, useMutation, useQuery, useQueryClient,
} from '@tanstack/react-query'
import {
  fetchDevices, fetchForecasts, fetchInsights, fetchWindows, renameDevice,
} from './api'
import type { Device } from './api'
import LiveChart from './LiveChart'
import { useLive } from './useLive'
import type { DeviceBuffer } from './useLive'

const qc = new QueryClient()

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <h1>Injury-Risk Dashboard (stage-2 crude UI)</h1>
      <p style={{ maxWidth: 720 }}>
        Composite is a monitoring/triage aid, not an injury prediction — no
        composite score has passed prospective validation (biomech SPEC §2).
      </p>
      <Devices />
    </QueryClientProvider>
  )
}

function Devices() {
  const devices = useQuery({
    queryKey: ['devices'],
    queryFn: fetchDevices,
    refetchInterval: 10_000,
  })
  const ids = useMemo(
    () => (devices.data ?? []).map((d) => d.device_id),
    [devices.data],
  )
  const live = useLive(ids)
  if (devices.isLoading) return <p>loading devices…</p>
  if (devices.isError) return <p>failed to load devices: {String(devices.error)}</p>
  if (!devices.data!.length) return <p>no devices yet — start streaming.</p>
  return (
    <>
      {devices.data!.map((d) => (
        <DeviceSection key={d.device_id} device={d} buffer={live[d.device_id]} />
      ))}
    </>
  )
}

function DeviceSection({ device, buffer }: { device: Device; buffer?: DeviceBuffer }) {
  return (
    <section style={{ border: '1px solid #999', margin: 12, padding: 12 }}>
      <Header device={device} />
      <LiveChart buffer={buffer} />
      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
        <Windows dev={device.device_id} />
        <Forecast dev={device.device_id} />
        <Insights dev={device.device_id} />
      </div>
    </section>
  )
}

function Header({ device }: { device: Device }) {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(device.display_name)
  const rename = useMutation({
    mutationFn: () => renameDevice(device.device_id, name),
    onSuccess: () => {
      setEditing(false)
      queryClient.invalidateQueries({ queryKey: ['devices'] })
    },
  })
  return (
    <div>
      <b style={{ fontSize: '1.2em' }}>
        {editing ? (
          <>
            <input value={name} maxLength={64} onChange={(e) => setName(e.target.value)} />
            <button onClick={() => rename.mutate()} disabled={rename.isPending}>save</button>
            <button onClick={() => setEditing(false)}>cancel</button>
          </>
        ) : (
          <>
            {device.display_name}{' '}
            <button onClick={() => { setName(device.display_name); setEditing(true) }}>
              rename
            </button>
          </>
        )}
      </b>{' '}
      <span style={{ color: device.online ? 'green' : 'red' }}>
        {device.online ? 'ONLINE' : 'OFFLINE'}
      </span>{' '}
      | id {device.device_id} | quality{' '}
      {device.quality != null ? `${(device.quality * 100).toFixed(0)}%` : '—'} |{' '}
      {device.sensors.map((s) => `${s.limb} ${s.rate_hz.toFixed(0)}Hz`).join(', ') || 'no sensors'}
    </div>
  )
}

function Windows({ dev }: { dev: string }) {
  const q = useQuery({
    queryKey: ['windows', dev],
    queryFn: () => fetchWindows(dev),
    refetchInterval: 60_000,
  })
  const arrow = { up: '↑', down: '↓', flat: '→' }
  return (
    <div>
      <h3>Past windows</h3>
      {q.data?.windows.map((w) => (
        <div key={w.window} style={{ border: '1px dotted #aaa', padding: 6, marginBottom: 4 }}>
          <b>{w.window}</b> {arrow[w.trend]} composite{' '}
          {w.composite.avg != null
            ? `${w.composite.avg.toFixed(1)} (${w.composite.min?.toFixed(0)}–${w.composite.max?.toFixed(0)})`
            : 'no data'}
          {' '}| q {w.quality != null ? w.quality.toFixed(2) : '—'}
          <br />
          m1..m5: {w.m.map((v) => (v != null ? v.toFixed(0) : '·')).join(' / ')}
        </div>
      )) ?? (q.isLoading ? 'loading…' : 'unavailable')}
    </div>
  )
}

function Forecast({ dev }: { dev: string }) {
  const q = useQuery({
    queryKey: ['forecasts', dev],
    queryFn: () => fetchForecasts(dev),
    refetchInterval: 60_000,
    retry: false, // 404 = no forecast yet, not an error worth retrying
  })
  return (
    <div>
      <h3>Forecast (composite)</h3>
      {q.data ? (
        <>
          <small>
            {q.data.model_version} @ {new Date(q.data.made_at).toLocaleTimeString()}
          </small>
          <ul>
            {q.data.points.map((p) => (
              <li key={p.horizon}>
                +{p.horizon}: <b>{p.pred.toFixed(1)}</b>
                {p.ci_low != null && p.ci_high != null &&
                  ` (CI ${p.ci_low.toFixed(1)}–${p.ci_high.toFixed(1)})`}
              </li>
            ))}
          </ul>
        </>
      ) : q.isLoading ? 'loading…' : 'no forecast yet'}
    </div>
  )
}

function Insights({ dev }: { dev: string }) {
  const q = useQuery({
    queryKey: ['insights', dev],
    queryFn: () => fetchInsights(dev),
    refetchInterval: 30_000,
  })
  const color = { info: '#36c', warning: '#c80', alert: '#c22' }
  return (
    <div style={{ maxWidth: 420 }}>
      <h3>Insights</h3>
      {q.data?.length
        ? q.data.map((i) => (
            <div key={i.insight_id} style={{ marginBottom: 4 }}>
              <span style={{ color: color[i.severity], fontWeight: 'bold' }}>
                [{i.severity}]
              </span>{' '}
              {i.message}{' '}
              <small>({new Date(i.created_at).toLocaleTimeString()})</small>
            </div>
          ))
        : q.isLoading ? 'loading…' : 'none yet'}
    </div>
  )
}
