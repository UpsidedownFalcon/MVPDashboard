// Small shared display atoms: status badge, quality meter, sensor dots,
// flag chips, severity chip, inline rename. All follow UIUX §6 — color never
// carries meaning alone (icon/word always present).

import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  CircleAlert,
  Info,
  Pencil,
} from 'lucide-react'
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { renameDevice, type Device, type Sensor } from '../lib/api'
import { pct } from '../lib/format'
import { FLAG_META, qualityBand, type Severity } from '../lib/metrics'

export function StatusBadge({ online }: { online: boolean }) {
  return (
    <span className={`chip status-badge ${online ? 'is-on' : 'is-off'}`}>
      <span className={`dot ${online ? 'dot-on' : 'dot-off'}`} aria-hidden />
      {online ? 'online' : 'offline'}
    </span>
  )
}

const QUALITY_VAR = {
  good: '--status-good',
  warning: '--status-warning',
  critical: '--status-critical',
} as const

export function QualityMeter({ quality }: { quality: number | null }) {
  if (quality == null) {
    return <span className="quality quality-none">quality —</span>
  }
  const band = qualityBand(quality)
  const color = `var(${QUALITY_VAR[band]})`
  const bars = Math.round(Math.min(1, Math.max(0, quality)) * 5)
  return (
    <span
      className="quality"
      title={`Data quality ${pct(quality)} — share of expected sensor samples arriving`}
    >
      <span className="quality-bars" aria-hidden>
        {[0, 1, 2, 3, 4].map((i) => (
          <span
            key={i}
            className="quality-bar"
            style={{
              background: i < bars ? color : `color-mix(in srgb, ${color} 20%, transparent)`,
            }}
          />
        ))}
      </span>
      <span className="quality-num">{pct(quality)}</span>
    </span>
  )
}

/** Per-sensor liveness micro-dots, sorted-limb order (UIUX §3/§4). */
export function SensorDots({
  sensors,
  detailed = false,
}: {
  sensors: Sensor[]
  detailed?: boolean
}) {
  const now = Date.now()
  const sorted = [...sensors].sort((a, b) => a.limb.localeCompare(b.limb))
  return (
    <span className={`sensors ${detailed ? 'sensors-detailed' : ''}`}>
      {sorted.map((s) => {
        const seen = s.last_seen ? now - Date.parse(s.last_seen) : Infinity
        const state = seen <= 10_000 ? 'good' : s.last_seen ? 'warning' : 'critical'
        const label = s.limb.replace('_', ' ')
        return (
          <span
            key={`${s.source_id}:${s.sensor_id}`}
            className="sensor"
            title={`${label} · ${s.rate_hz.toFixed(0)} Hz · ${
              s.last_seen ? `seen ${Math.round(seen / 1000)}s ago` : 'never streamed'
            }`}
          >
            <span className="dot" style={{ background: `var(${QUALITY_VAR[state]})` }} aria-hidden />
            {detailed && (
              <span className="sensor-label">
                {label} <span className="sensor-rate">{s.rate_hz.toFixed(0)}Hz</span>
              </span>
            )}
          </span>
        )
      })}
    </span>
  )
}

const SEVERITY_META: Record<Severity, { label: string; cssVar: string; Icon: typeof Info }> = {
  info: { label: 'info', cssVar: '--series-m1', Icon: Info },
  warning: { label: 'warning', cssVar: '--status-warning', Icon: AlertTriangle },
  alert: { label: 'alert', cssVar: '--status-critical', Icon: CircleAlert },
}

export function SeverityChip({ severity }: { severity: Severity }) {
  const { label, cssVar, Icon } = SEVERITY_META[severity]
  return (
    <span
      className="chip severity-chip"
      style={{
        color: `var(${cssVar})`,
        borderColor: `color-mix(in srgb, var(${cssVar}) 35%, transparent)`,
        background: `color-mix(in srgb, var(${cssVar}) 10%, transparent)`,
      }}
    >
      <Icon aria-hidden />
      {label}
    </span>
  )
}

/** Biomech flag chips. warming_up must never look like degraded_sensors
 *  (UIUX §6): weights style alert/warning strongly, muted stays quiet. */
export function FlagChips({ flags }: { flags: string[] }) {
  if (!flags.length) return null
  return (
    <span className="flags">
      {flags.map((f) => {
        const meta = FLAG_META[f] ?? { weight: 'muted' as const, label: f, hint: f }
        return (
          <span key={f} className={`chip flag flag-${meta.weight}`} title={meta.hint}>
            {meta.weight === 'alert' && <CircleAlert aria-hidden />}
            {meta.weight === 'warning' && <AlertTriangle aria-hidden />}
            {meta.label}
          </span>
        )
      })}
    </span>
  )
}

/** Inline rename (UIUX §3): click ✏ → input, Enter saves, Esc cancels,
 *  optimistic update + rollback. */
export function RenameInline({ device }: { device: Device }) {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(device.display_name)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editing) inputRef.current?.select()
  }, [editing])

  const rename = useMutation({
    mutationFn: (display_name: string) => renameDevice(device.device_id, display_name),
    onMutate: async (display_name) => {
      await queryClient.cancelQueries({ queryKey: ['devices'] })
      const prev = queryClient.getQueryData<Device[]>(['devices'])
      queryClient.setQueryData<Device[]>(['devices'], (old) =>
        old?.map((d) => (d.device_id === device.device_id ? { ...d, display_name } : d)),
      )
      return { prev }
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev) queryClient.setQueryData(['devices'], ctx.prev)
    },
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ['devices'] }),
  })

  const commit = () => {
    const trimmed = name.trim()
    setEditing(false)
    if (trimmed && trimmed !== device.display_name) rename.mutate(trimmed)
    else setName(device.display_name)
  }

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') commit()
    if (e.key === 'Escape') {
      setName(device.display_name)
      setEditing(false)
    }
  }

  if (editing) {
    return (
      <input
        ref={inputRef}
        className="rename-input"
        value={name}
        maxLength={64}
        onChange={(e) => setName(e.target.value)}
        onBlur={commit}
        onKeyDown={onKey}
        onClick={(e) => e.stopPropagation()}
        aria-label="Athlete name"
      />
    )
  }
  return (
    <span className="rename">
      <span className="rename-name">{device.display_name}</span>
      <button
        className="rename-btn"
        title="Rename"
        aria-label={`Rename ${device.display_name}`}
        onClick={(e) => {
          e.preventDefault()
          e.stopPropagation()
          setName(device.display_name)
          setEditing(true)
        }}
      >
        <Pencil size={13} aria-hidden />
      </button>
    </span>
  )
}
