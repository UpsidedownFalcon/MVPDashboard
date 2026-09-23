// Rig-aware copy (PLAN_unilateral_devices §8). A "rig" is what the dashboard
// keys a soldier by: either one bilateral unit (two leg MCUs, four sensors) or
// one/two knee sleeves, each a single unit with two sensors on ONE leg.
//
// `kind` and `units` are OPTIONAL on Device: anything that does not carry them
// (an older API, the demo layer, which stays bilateral by decision L) is
// treated as bilateral, and the bilateral wording is the R1 literal from
// config.ts, unchanged.
//
// Every user-facing string lives in RIG_COPY so lib/text.test.ts walks it for
// the plain-ASCII punctuation rule (no em/en dash, ellipsis or middle dot) and
// the "soldier, never athlete" rule.

import type { Device } from './api'
import { LOGGING_RATE_TEXT, SENSOR_SUMMARY_TEXT } from './config'

/** The slice of a device the rig helpers need. Everything is optional except
 *  the id, so a Pick of any Device (real, demo or partial) satisfies it. */
export type RigDevice = Pick<Device, 'device_id' | 'kind' | 'units'>

export type Side = 'left' | 'right'

export const SIDES: readonly Side[] = ['left', 'right']

/** Per-rig caches that a pairing, side or full-scale change invalidates: the
 *  backend resets the biomech session, so every stored view of it is stale.
 *  Shared by RigControls and the Sleeve storage save (PLAN_msd decision I). */
export const RIG_QUERY_KEYS = ['windows', 'history', 'forecasts', 'insights', 'advice-timeline'] as const

export const RIG_COPY = {
  /** bilateral and unknown-kind rigs keep the STAGE4 R1 literal verbatim */
  summaryBilateral: SENSOR_SUMMARY_TEXT,
  /** one sleeve whose side an operator has set */
  summaryOneSleeve: `2 sensors | one leg | ${LOGGING_RATE_TEXT}`,
  /** one sleeve with no side yet (decision G): never guess left or right */
  summarySideNotSet: `2 sensors | side not set | ${LOGGING_RATE_TEXT}`,
  /** a paired rig: two sleeves, one per leg */
  summaryTwoSleeves: `4 sensors | 2 sleeves | ${LOGGING_RATE_TEXT}`,

  /** battery tooltip tails, chosen by rig shape */
  batterySourceBilateral: 'lowest of the two leg sensors',
  batterySourceSleeve: 'sleeve',
  batterySourceTwoSleeves: 'lowest of the two sleeves',

  /** RigControls (device header) */
  sideNotSet: 'side not set',
  sideLeft: 'Left',
  sideRight: 'Right',
  sideGroupLabel: 'Leg this sleeve is on',
  scaleLabel: 'Full scale',
  scaleChange: 'change',
  scaleDone: 'done',
  accelGroupLabel: 'Accelerometer full scale',
  gyroGroupLabel: 'Gyroscope full scale',
  pairOpen: 'Pair with...',
  pairEmpty: 'No unpaired sleeve is streaming right now.',
  pairLoading: 'Loading sleeves...',
  pairJoinerSide: 'Leg for the joining sleeve',
  pairHostSide: 'Leg for this sleeve',
  pairSave: 'Save',
  pairCancel: 'Cancel',
  pairSameSide: 'Pick a different leg for each sleeve.',
  pairPickUnit: 'Pick a sleeve to pair with.',
  pairPickSide: 'Pick a leg for each sleeve.',
  unpair: 'Unpair',
  errorPrefix: 'Could not save',
  errorFallback: 'the change did not go through - try again.',
} as const

const SIDE_LABEL: Record<Side, string> = {
  left: RIG_COPY.sideLeft,
  right: RIG_COPY.sideRight,
}

export function sideLabel(side: Side | null | undefined): string {
  return side ? SIDE_LABEL[side] : RIG_COPY.sideNotSet
}

/** A sleeve rig is a unilateral one. Undefined kind = bilateral (see header). */
export function isSleeveRig(device: RigDevice | null | undefined): boolean {
  return device?.kind === 'unilateral'
}

/** Which legs actually carry sensors. A bilateral rig always carries both; a
 *  sleeve rig carries only the legs its units have been assigned to, so a
 *  side-less sleeve carries none (decision G: never guess a side). */
export function instrumentedSides(device: RigDevice | null | undefined): Side[] {
  if (!isSleeveRig(device)) return [...SIDES]
  const seen = new Set<Side>()
  for (const unit of device?.units ?? []) {
    if (unit.side) seen.add(unit.side)
  }
  return SIDES.filter((s) => seen.has(s))
}

/** The one static sensor line in the header and on overview cards. */
export function sensorSummaryText(device: RigDevice | null | undefined): string {
  if (!isSleeveRig(device)) return RIG_COPY.summaryBilateral
  const units = device?.units ?? []
  if (units.length >= 2) return RIG_COPY.summaryTwoSleeves
  if (units.length === 1 && units[0].side) return RIG_COPY.summaryOneSleeve
  return RIG_COPY.summarySideNotSet
}

/** Battery tooltip. The bilateral text is unchanged; a sleeve rig says which
 *  sleeve the reading came from, or that it is the lower of two. */
export function batteryTooltip(device: RigDevice | null | undefined, pct: number): string {
  const source = !isSleeveRig(device)
    ? RIG_COPY.batterySourceBilateral
    : (device?.units?.length ?? 0) >= 2
      ? RIG_COPY.batterySourceTwoSleeves
      : RIG_COPY.batterySourceSleeve
  return `Battery ${pct}% (${source})`
}

/** Full-scale readout for one sleeve, e.g. "+-32 g | +-4000 dps". Sleeve
 *  packets carry no scale, so this is configuration, not measurement. */
export function fullScaleText(accelFsG: number, gyroFsDps: number): string {
  return `+-${accelFsG} g | +-${gyroFsDps} dps`
}
