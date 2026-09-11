import { describe, expect, it } from 'vitest'
import {
  boundedMetricValue,
  durationToSeconds,
  evenBucketCount,
  horizonLabel,
  metricValue,
  pct,
  windowLabel,
} from './format'

describe('format placeholders', () => {
  it('renders a missing metric value as the placeholder', () => {
    expect(metricValue(null)).toBe('—')
    expect(metricValue(undefined)).toBe('—')
    expect(pct(null)).toBe('—')
  })

  it('formats present values', () => {
    expect(metricValue(42.4)).toBe('42')
    expect(metricValue(42.44, 1)).toBe('42.4')
    expect(pct(0.955)).toBe('96%')
  })

  it('prefixes a saturation lower bound', () => {
    expect(boundedMetricValue('m1', 30, ['saturated'])).toBe('≥ 30')
    expect(boundedMetricValue('m3', 30, ['saturated'])).toBe('30')
    expect(boundedMetricValue('m1', null, ['saturated'])).toBe(metricValue(null))
  })
})

describe('labels and durations', () => {
  it('prettifies config labels without hardcoding durations', () => {
    expect(horizonLabel('10m')).toBe('+10m')
    expect(windowLabel('2h')).toBe('past 2h')
  })

  it('parses the backend duration syntax', () => {
    expect(durationToSeconds('30s')).toBe(30)
    expect(durationToSeconds('5m')).toBe(300)
    expect(durationToSeconds('2h')).toBe(7200)
    expect(durationToSeconds('1d')).toBe(86400)
    expect(durationToSeconds('nope')).toBe(0)
  })

  it('chooses bucket counts that divide the window exactly', () => {
    expect(evenBucketCount('5m')).toBe(30)
    expect(evenBucketCount('30m')).toBe(30)
    expect(evenBucketCount('2h')).toBe(30)
    expect(300 % evenBucketCount('5m')).toBe(0)
  })
})
