// Sleeve rig controls in the device header (PLAN_unilateral_devices §8, UIUX §4
// amendment). Only a unilateral rig renders this - a bilateral unit has fixed
// sides and compile-time full-scale constants, and a demo soldier is bilateral
// by decision L, so Device.tsx mounts this for neither.
//
// Per sleeve: which leg it is on, and its full scale (+-32 g / +-4000 dps by
// default - the packet carries no scale, so this is configuration the
// dashboard owns). Plus pairing: one sleeve can take a second one in, which
// makes a single soldier with four sensors and a working L/R balance.
//
// Every mutation here hard-resets the soldier's biomech session on the backend
// (decision N), so the stakes are higher than a rename: errors are shown
// inline rather than swallowed, and success invalidates the rig's cached
// windows, history, projections and advice as well as the registry - for the
// other unit's rig too when pairing or unpairing.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleDashed, Link2, SlidersHorizontal, Unlink } from 'lucide-react'
import { useState, type MouseEvent } from 'react'
import { ApiError, fetchUnits, pairUnit, patchUnit, unpairUnit, type Unit } from '../lib/api'
import { ACCEL_FS_ALLOWED_G, GYRO_FS_ALLOWED_DPS } from '../lib/config'
import { fullScaleText, RIG_COPY, sideLabel, SIDES, type RigDevice, type Side } from '../lib/rig'

/** Per-rig caches that a pairing, side or full-scale change invalidates: the
 *  backend resets the biomech session, so every stored view of it is stale. */
const RIG_QUERY_KEYS = ['windows', 'history', 'forecasts', 'insights', 'advice-timeline'] as const

/** The header sits inside click-through surfaces elsewhere; keep every press
 *  local, exactly as RenameInline does. */
const stop = (e: MouseEvent) => e.stopPropagation()

export default function RigControls({ device }: { device: RigDevice }) {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [scaleFor, setScaleFor] = useState<string | null>(null)
  const [pairing, setPairing] = useState(false)
  const [joiner, setJoiner] = useState<string | null>(null)
  const [joinerSide, setJoinerSide] = useState<Side | null>(null)
  const [hostSide, setHostSide] = useState<Side | null>(null)

  const units = device.units ?? []
  // the host keeps the rig's id and history (decision H); anything else in the
  // rig is a member, and unpairing releases it
  const host = units.find((u) => u.unit_id === device.device_id)
  const member = units.find((u) => u.unit_id !== device.device_id)

  const invalidate = (rigIds: string[]) => {
    void queryClient.invalidateQueries({ queryKey: ['devices'] })
    void queryClient.invalidateQueries({ queryKey: ['units'] })
    for (const rig of rigIds) {
      for (const key of RIG_QUERY_KEYS) {
        void queryClient.invalidateQueries({ queryKey: [key, rig] })
      }
    }
  }

  const fail = (e: unknown) =>
    setError(
      `${RIG_COPY.errorPrefix} - ${e instanceof ApiError ? e.message : RIG_COPY.errorFallback}`,
    )

  const closePair = () => {
    setPairing(false)
    setJoiner(null)
    setJoinerSide(null)
    setHostSide(null)
  }

  const settings = useMutation({
    mutationFn: ({
      unitId,
      body,
    }: {
      unitId: string
      body: { side?: Side; accel_fs_g?: number; gyro_fs_dps?: number }
    }) => patchUnit(unitId, body),
    onMutate: () => setError(null),
    onSuccess: () => invalidate([device.device_id]),
    onError: fail,
  })

  const pair = useMutation({
    mutationFn: (body: { unit_id: string; side: Side; host_side?: Side }) =>
      pairUnit(device.device_id, body),
    onMutate: () => setError(null),
    onSuccess: (_data, body) => {
      closePair()
      // the joiner's own rig disappears from the list, so its caches go too
      invalidate([device.device_id, body.unit_id])
    },
    onError: fail,
  })

  const unpair = useMutation({
    mutationFn: (unitId: string) => unpairUnit(unitId),
    onMutate: () => setError(null),
    onSuccess: (_data, unitId) => invalidate([device.device_id, unitId]),
    onError: fail,
  })

  // candidates: every sleeve that is streaming, unpaired and not already ours
  const unitsQuery = useQuery({
    queryKey: ['units'],
    queryFn: () => fetchUnits(device.device_id),
    enabled: pairing,
  })
  const own = new Set(units.map((u) => u.unit_id))
  const candidates = (unitsQuery.data ?? []).filter((u) => !own.has(u.unit_id) && !u.paired)

  const busy = settings.isPending || pair.isPending || unpair.isPending

  const submitPair = () => {
    if (!joiner) {
      setError(RIG_COPY.pairPickUnit)
      return
    }
    // the host keeps the side it already has; only an unsided host needs one
    const hostChoice = host?.side ?? hostSide
    if (!joinerSide || !hostChoice) {
      setError(RIG_COPY.pairPickSide)
      return
    }
    if (joinerSide === hostChoice) {
      setError(RIG_COPY.pairSameSide)
      return
    }
    pair.mutate({
      unit_id: joiner,
      side: joinerSide,
      host_side: host?.side ? undefined : hostChoice,
    })
  }

  const sideGroup = (
    label: string,
    current: Side | null,
    onPick: (side: Side) => void,
  ) => (
    <div className="segmented" role="group" aria-label={label}>
      {SIDES.map((s) => (
        <button
          key={s}
          type="button"
          className={`segment ${current === s ? 'is-active' : ''}`}
          aria-pressed={current === s}
          disabled={busy}
          onClick={(e) => {
            stop(e)
            onPick(s)
          }}
        >
          {sideLabel(s)}
        </button>
      ))}
    </div>
  )

  const unitRow = (unit: Unit) => {
    const open = scaleFor === unit.unit_id
    return (
      <div className="rig-unit" key={unit.unit_id}>
        <span className="rig-unit-id">{unit.unit_id}</span>
        {sideGroup(`${RIG_COPY.sideGroupLabel} | ${unit.unit_id}`, unit.side, (side) =>
          settings.mutate({ unitId: unit.unit_id, body: { side } }),
        )}
        {unit.side == null && (
          <span className="chip flag flag-muted">
            <CircleDashed aria-hidden />
            {RIG_COPY.sideNotSet}
          </span>
        )}
        <span className="rig-scale">
          <span className="rig-scale-label">{RIG_COPY.scaleLabel}</span>
          {fullScaleText(unit.accel_fs_g, unit.gyro_fs_dps)}
        </span>
        <button
          type="button"
          className={`segment rig-scale-btn ${open ? 'is-active' : ''}`}
          aria-expanded={open}
          onClick={(e) => {
            stop(e)
            setScaleFor(open ? null : unit.unit_id)
          }}
        >
          <SlidersHorizontal size={13} aria-hidden />
          {open ? RIG_COPY.scaleDone : RIG_COPY.scaleChange}
        </button>
        {open && (
          <div className="rig-scale-edit">
            <div
              className="segmented"
              role="group"
              aria-label={`${RIG_COPY.accelGroupLabel} | ${unit.unit_id}`}
            >
              {ACCEL_FS_ALLOWED_G.map((g) => (
                <button
                  key={g}
                  type="button"
                  className={`segment ${unit.accel_fs_g === g ? 'is-active' : ''}`}
                  aria-pressed={unit.accel_fs_g === g}
                  disabled={busy}
                  onClick={(e) => {
                    stop(e)
                    settings.mutate({ unitId: unit.unit_id, body: { accel_fs_g: g } })
                  }}
                >
                  {g} g
                </button>
              ))}
            </div>
            <div
              className="segmented"
              role="group"
              aria-label={`${RIG_COPY.gyroGroupLabel} | ${unit.unit_id}`}
            >
              {GYRO_FS_ALLOWED_DPS.map((d) => (
                <button
                  key={d}
                  type="button"
                  className={`segment ${unit.gyro_fs_dps === d ? 'is-active' : ''}`}
                  aria-pressed={unit.gyro_fs_dps === d}
                  disabled={busy}
                  onClick={(e) => {
                    stop(e)
                    settings.mutate({ unitId: unit.unit_id, body: { gyro_fs_dps: d } })
                  }}
                >
                  {d} dps
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="rig-controls" onClick={stop}>
      {units.map(unitRow)}

      {units.length === 1 && !pairing && (
        <button
          type="button"
          className="segment rig-action"
          disabled={busy}
          onClick={(e) => {
            stop(e)
            setError(null)
            setPairing(true)
          }}
        >
          <Link2 size={13} aria-hidden />
          {RIG_COPY.pairOpen}
        </button>
      )}

      {pairing && (
        <form
          className="rig-pair"
          onSubmit={(e) => {
            e.preventDefault()
            submitPair()
          }}
        >
          {unitsQuery.isLoading && <span className="notice">{RIG_COPY.pairLoading}</span>}
          {!unitsQuery.isLoading && candidates.length === 0 && (
            <span className="notice">{RIG_COPY.pairEmpty}</span>
          )}
          {candidates.length > 0 && (
            <div className="segmented" role="group" aria-label={RIG_COPY.pairOpen}>
              {candidates.map((u) => (
                <button
                  key={u.unit_id}
                  type="button"
                  className={`segment ${joiner === u.unit_id ? 'is-active' : ''}`}
                  aria-pressed={joiner === u.unit_id}
                  onClick={(e) => {
                    stop(e)
                    setJoiner(u.unit_id)
                  }}
                >
                  {u.unit_id} | {u.rig_display_name ?? u.unit_id}
                </button>
              ))}
            </div>
          )}
          {joiner && sideGroup(RIG_COPY.pairJoinerSide, joinerSide, setJoinerSide)}
          {joiner && !host?.side && sideGroup(RIG_COPY.pairHostSide, hostSide, setHostSide)}
          <button type="submit" className="segment rig-action" disabled={busy}>
            {RIG_COPY.pairSave}
          </button>
          <button
            type="button"
            className="insight-decide-cancel"
            onClick={(e) => {
              stop(e)
              setError(null)
              closePair()
            }}
          >
            {RIG_COPY.pairCancel}
          </button>
        </form>
      )}

      {member && (
        <button
          type="button"
          className="segment rig-action"
          disabled={busy}
          onClick={(e) => {
            stop(e)
            unpair.mutate(member.unit_id)
          }}
        >
          <Unlink size={13} aria-hidden />
          {RIG_COPY.unpair}
        </button>
      )}

      {error && <p className="rig-error notice">{error}</p>}
    </div>
  )
}
