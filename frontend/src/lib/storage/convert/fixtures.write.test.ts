// The synthetic decoder fixtures (fixtures/convert/*.bin) are the encoder
// output of fixtures/convert/specs.ts. With STORAGE_WRITE_FIXTURES=1 this
// test (re)writes them; otherwise it asserts every committed file still
// equals the spec byte for byte, so encoder or spec drift is caught before
// the goldens go stale.
//   PowerShell: $env:STORAGE_WRITE_FIXTURES='1'; npx vitest run src/lib/storage/convert/fixtures.write.test.ts
//   bash:       STORAGE_WRITE_FIXTURES=1 npx vitest run src/lib/storage/convert/fixtures.write.test.ts
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { MAX_SAMPLES } from '../binFormat'
import { convertFixtureUrl } from '../fixtures/convert/load'
import { CONVERT_FIXTURES, fixtureBytesOf, MAX_FIXTURE_BYTES } from '../fixtures/convert/specs'
import { bytesEqual } from '../io'
import { scanBytes } from '../scanner'

const WRITE = process.env.STORAGE_WRITE_FIXTURES === '1'

describe('decoder fixtures', () => {
  it.each(CONVERT_FIXTURES.map((f) => [f.name, f] as const))('%s: committed bytes equal the encoder spec', (_, f) => {
    const bytes = fixtureBytesOf(f)
    expect(bytes.length).toBeLessThan(MAX_FIXTURE_BYTES)
    const url = convertFixtureUrl(f.file)
    if (WRITE) {
      mkdirSync(new URL('./', url), { recursive: true })
      writeFileSync(url, bytes)
    }
    expect(existsSync(url), `${f.file} missing: run with STORAGE_WRITE_FIXTURES=1`).toBe(true)
    expect(bytesEqual(new Uint8Array(readFileSync(url)), bytes), `${f.file} differs from its spec`).toBe(true)
  })

  it('every spec has a unique name, a parseable header and the extras it claims', () => {
    expect(new Set(CONVERT_FIXTURES.map((f) => f.name)).size).toBe(CONVERT_FIXTURES.length)
    for (const f of CONVERT_FIXTURES) {
      const scan = scanBytes(fixtureBytesOf(f))
      expect(scan.header?.sessionId, f.name).toBe(f.header.sessionId)
      expect(scan.unused, f.name).toBe(f.extras.unusedTailBlocks)
      expect(scan.firstBad, f.name).toBe(f.extras.firstBad)
      expect(scan.trailingBytes, f.name).toBe(f.tail.length)
      // The per-sensor bookkeeping the golden test relies on.
      expect(scan.samples[1], f.name).toBe(f.decodableBlocks[1] * MAX_SAMPLES)
      expect(scan.samples[2], f.name).toBe(f.decodableBlocks[2] * MAX_SAMPLES)
    }
  })

  it('kitchen gives every sensor at least 24 decodable blocks and exercises everything', () => {
    const kitchen = CONVERT_FIXTURES.find((f) => f.name === 'kitchen')!
    expect(kitchen.decodableBlocks[1]).toBeGreaterThanOrEqual(24)
    expect(kitchen.decodableBlocks[2]).toBeGreaterThanOrEqual(24)
    const scan = scanBytes(fixtureBytesOf(kitchen))
    expect(scan).toMatchObject({ cleanEnd: true, seqGaps: 2, fifoOverflows: 2, tsClamped: 2, unused: 5, bad: 2 })
    expect(scan.syncBlocks).toHaveLength(3)
    expect(scan.syncBlocks[1].espUs).toBeLessThan(scan.syncBlocks[0].espUs) // out of file order
  })
})
