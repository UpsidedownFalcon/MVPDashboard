import { describe, expect, it } from 'vitest'
import { crc32Final, crc32Init, crc32Of, crc32Update } from './crc32'

const ascii = (s: string) => new TextEncoder().encode(s)

describe('crc32', () => {
  it('matches the IEEE / zlib check vectors', () => {
    expect(crc32Of(new Uint8Array(0))).toBe(0)
    expect(crc32Of(ascii('123456789'))).toBe(0xcbf43926)
    expect(crc32Of(ascii('a'))).toBe(0xe8b7be43)
  })

  it('returns unsigned 32-bit values', () => {
    const c = crc32Of(ascii('The quick brown fox jumps over the lazy dog'))
    expect(c).toBe(0x414fa339)
    expect(c).toBeGreaterThanOrEqual(0)
  })

  it('gives the same result chunked and whole', () => {
    const data = new Uint8Array(5000)
    for (let i = 0; i < data.length; i++) data[i] = (i * 31 + 7) & 0xff
    const whole = crc32Of(data)
    for (const step of [1, 7, 512, 4096]) {
      let state = crc32Init()
      for (let off = 0; off < data.length; off += step) {
        state = crc32Update(state, data.subarray(off, Math.min(off + step, data.length)))
      }
      expect(crc32Final(state)).toBe(whole)
    }
  })
})
