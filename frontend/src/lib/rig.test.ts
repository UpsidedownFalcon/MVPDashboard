// Rig-aware copy (PLAN_unilateral_devices §8). The bilateral line is the
// STAGE4 R1 literal and must stay byte-identical; the sleeve lines are the
// only computed ones, and none of them ever guesses a side.
import { describe, expect, it } from 'vitest'
import type { Unit } from './api'
import { SENSOR_SUMMARY_TEXT } from './config'
import {
  batteryTooltip,
  fullScaleText,
  instrumentedSides,
  isSleeveRig,
  RIG_COPY,
  sensorSummaryText,
  type RigDevice,
} from './rig'

const unit = (unit_id: string, side: Unit['side']): Unit => ({
  unit_id,
  side,
  accel_fs_g: 32,
  gyro_fs_dps: 4000,
  online: true,
  last_seen: null,
  soc: 80,
})

/** an explicitly bilateral rig, and one from an API that never sent `kind` */
const BILATERAL: RigDevice = { device_id: '30', kind: 'bilateral', units: [] }
const UNKNOWN_KIND: RigDevice = { device_id: '30' }
/** one sleeve, still waiting for an operator to say which leg (decision G) */
const SLEEVE_SIDELESS: RigDevice = {
  device_id: 'u30-0',
  kind: 'unilateral',
  units: [unit('u30-0', null)],
}
const SLEEVE_SIDED: RigDevice = {
  device_id: 'u30-0',
  kind: 'unilateral',
  units: [unit('u30-0', 'left')],
}
/** a paired rig: host keeps its id, the member joins it (decision H) */
const PAIRED: RigDevice = {
  device_id: 'u30-0',
  kind: 'unilateral',
  units: [unit('u30-0', 'left'), unit('u31-0', 'right')],
}

describe('isSleeveRig', () => {
  it('treats a missing kind as bilateral', () => {
    expect(isSleeveRig(UNKNOWN_KIND)).toBe(false)
    expect(isSleeveRig(BILATERAL)).toBe(false)
    expect(isSleeveRig(SLEEVE_SIDELESS)).toBe(true)
    expect(isSleeveRig(PAIRED)).toBe(true)
  })
})

describe('sensorSummaryText', () => {
  it('keeps the R1 literal for bilateral and unknown-kind rigs', () => {
    expect(sensorSummaryText(BILATERAL)).toBe(SENSOR_SUMMARY_TEXT)
    expect(sensorSummaryText(UNKNOWN_KIND)).toBe(SENSOR_SUMMARY_TEXT)
    expect(sensorSummaryText(BILATERAL)).toBe('4 sensors | 6400Hz logging')
  })

  it('says "side not set" for a sleeve with no side yet', () => {
    expect(sensorSummaryText(SLEEVE_SIDELESS)).toBe('2 sensors | side not set | 6400Hz logging')
    expect(sensorSummaryText(SLEEVE_SIDELESS)).toBe(RIG_COPY.summarySideNotSet)
  })

  it('says "one leg" for a sided single sleeve, never which leg', () => {
    expect(sensorSummaryText(SLEEVE_SIDED)).toBe('2 sensors | one leg | 6400Hz logging')
    expect(sensorSummaryText(SLEEVE_SIDED)).not.toMatch(/left|right/i)
  })

  it('counts both sleeves once paired', () => {
    expect(sensorSummaryText(PAIRED)).toBe('4 sensors | 2 sleeves | 6400Hz logging')
  })
})

describe('instrumentedSides', () => {
  it('gives both legs for a bilateral or unknown-kind rig', () => {
    expect(instrumentedSides(BILATERAL)).toEqual(['left', 'right'])
    expect(instrumentedSides(UNKNOWN_KIND)).toEqual(['left', 'right'])
  })

  it('gives nothing for a side-less sleeve and one leg for a sided one', () => {
    expect(instrumentedSides(SLEEVE_SIDELESS)).toEqual([])
    expect(instrumentedSides(SLEEVE_SIDED)).toEqual(['left'])
  })

  it('gives both legs for a paired rig, in a stable order', () => {
    expect(instrumentedSides(PAIRED)).toEqual(['left', 'right'])
    const swapped: RigDevice = { ...PAIRED, units: [unit('u31-0', 'right'), unit('u30-0', 'left')] }
    expect(instrumentedSides(swapped)).toEqual(['left', 'right'])
  })
})

describe('batteryTooltip', () => {
  it('keeps the two-MCU wording for a bilateral rig', () => {
    expect(batteryTooltip(BILATERAL, 42)).toBe('Battery 42% (lowest of the two leg sensors)')
    expect(batteryTooltip(UNKNOWN_KIND, 42)).toBe('Battery 42% (lowest of the two leg sensors)')
    expect(batteryTooltip(undefined, 42)).toBe('Battery 42% (lowest of the two leg sensors)')
  })

  it('names the sleeve a single reading came from', () => {
    expect(batteryTooltip(SLEEVE_SIDELESS, 42)).toBe('Battery 42% (sleeve)')
    expect(batteryTooltip(SLEEVE_SIDED, 7)).toBe('Battery 7% (sleeve)')
  })

  it('reports the lower of two sleeves once paired', () => {
    expect(batteryTooltip(PAIRED, 42)).toBe('Battery 42% (lowest of the two sleeves)')
  })
})

describe('fullScaleText', () => {
  it('reads as plain ASCII, defaults first', () => {
    expect(fullScaleText(32, 4000)).toBe('+-32 g | +-4000 dps')
    expect(fullScaleText(2, 125)).toBe('+-2 g | +-125 dps')
  })
})
