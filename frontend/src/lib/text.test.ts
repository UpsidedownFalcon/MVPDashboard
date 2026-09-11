// Guard for the plain-ASCII punctuation rule (STAGE4 R4): no em dash, en dash,
// ellipsis or middle dot may reach the UI through the exported string tables.
import { describe, expect, it } from 'vitest'
import { COMPOSITE, FLAG_META, METRICS, RISK_BAND_META } from './metrics'

const FORBIDDEN = /[—–…·]/

function strings(value: unknown, path = 'root', out: [string, string][] = []): [string, string][] {
  if (typeof value === 'string') out.push([path, value])
  else if (Array.isArray(value)) value.forEach((v, i) => strings(v, `${path}[${i}]`, out))
  else if (value && typeof value === 'object') {
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) strings(v, `${path}.${k}`, out)
  }
  return out
}

describe('user-facing string tables', () => {
  it.each([
    ['METRICS', METRICS],
    ['COMPOSITE', COMPOSITE],
    ['FLAG_META', FLAG_META],
    ['RISK_BAND_META', RISK_BAND_META],
  ])('%s carries no em/en dash, ellipsis or middle dot', (_name, table) => {
    const offenders = strings(table).filter(([, s]) => FORBIDDEN.test(s))
    expect(offenders).toEqual([])
  })

  it('uses soldier wording, never athlete', () => {
    const offenders = strings([METRICS, COMPOSITE, FLAG_META]).filter(([, s]) => /athlete/i.test(s))
    expect(offenders).toEqual([])
  })
})
