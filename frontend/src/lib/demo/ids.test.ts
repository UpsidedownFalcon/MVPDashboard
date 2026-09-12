import { describe, expect, it } from 'vitest'
import { DEMO_COUNT, DEMO_PREFIX, demoRank, isDemoId } from './ids'

describe('demo ids', () => {
  it('recognises the prefix and nothing else', () => {
    expect(isDemoId('demo-1')).toBe(true)
    expect(isDemoId(`${DEMO_PREFIX}${DEMO_COUNT}`)).toBe(true)
    expect(isDemoId('30')).toBe(false)
    expect(isDemoId('demo')).toBe(false)
    expect(isDemoId('xdemo-1')).toBe(false)
  })

  it('ranks real devices first, then soldiers in scripted order', () => {
    expect(demoRank('30')).toBe(0)
    expect(demoRank('100')).toBe(0)
    expect(demoRank('demo-1')).toBe(1)
    expect(demoRank('demo-5')).toBe(5)
    expect(demoRank('demo-x')).toBe(99)
    expect(demoRank('demo-0')).toBe(99)
  })
})
