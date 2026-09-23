import { describe, expect, it } from 'vitest'
import { formatBytes, formatEta, formatRate, percent } from './format'

describe('storage number formatting', () => {
  it('formats byte counts in decimal units with one decimal', () => {
    expect(formatBytes(400)).toBe('400 B')
    expect(formatBytes(12_300)).toBe('12.3 KB')
    expect(formatBytes(512 * 1024 * 1024)).toBe('536.9 MB')
    expect(formatBytes(2 * 1024 ** 3)).toBe('2.1 GB')
  })

  it('formats the card read rate in MB/s', () => {
    expect(formatRate(1_000_000)).toBe('1.0 MB/s')
    expect(formatRate(0)).toBe('0.0 MB/s')
  })

  it('formats an ETA as seconds, minutes or hours and minutes, never negative', () => {
    expect(formatEta(45_000)).toBe('45 s')
    expect(formatEta(537_000)).toBe('9 min')
    expect(formatEta(65 * 60_000)).toBe('1 h 5 min')
    expect(formatEta(-5)).toBe('0 s')
  })

  it('clamps percent to 0..100 and reads 0 for an unknown total', () => {
    expect(percent(512, 4608)).toBe(11)
    expect(percent(5000, 4608)).toBe(100)
    expect(percent(10, 0)).toBe(0)
  })
})
