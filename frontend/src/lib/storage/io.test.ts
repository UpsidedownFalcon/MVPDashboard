import { describe, expect, it } from 'vitest'
import { BLOCK_BYTES, FILE_HEADER_BYTES } from './binFormat'
import { bytesEqual, chunkPlan, concatBytes, readChunks } from './io'
import { MemDir } from './memDir'

const MIB = 1024 * 1024

async function collect(gen: AsyncGenerator<Uint8Array>): Promise<Uint8Array[]> {
  const out: Uint8Array[] = []
  for await (const c of gen) out.push(c)
  return out
}

describe('chunkPlan', () => {
  it('sizes the first chunk as 512 + whole blocks and later chunks as whole blocks', () => {
    expect(chunkPlan(4 * MIB)).toEqual({ first: FILE_HEADER_BYTES + 1023 * BLOCK_BYTES, rest: 1023 * BLOCK_BYTES })
    expect(chunkPlan(FILE_HEADER_BYTES + 2 * BLOCK_BYTES)).toEqual({ first: FILE_HEADER_BYTES + 2 * BLOCK_BYTES, rest: 2 * BLOCK_BYTES })
    // Never below one block, even for a silly budget.
    expect(chunkPlan(100)).toEqual({ first: FILE_HEADER_BYTES + BLOCK_BYTES, rest: BLOCK_BYTES })
  })
})

describe('readChunks', () => {
  it('yields block-aligned slices that reassemble the file', async () => {
    const size = FILE_HEADER_BYTES + 2500 * BLOCK_BYTES + 100
    const data = new Uint8Array(size)
    for (let i = 0; i < size; i++) data[i] = i & 0xff
    const dir = new MemDir({ f: data })
    const chunks = await collect(readChunks(await dir.open('f'), 4 * MIB))
    const plan = chunkPlan(4 * MIB)
    expect(chunks.map((c) => c.length)).toEqual([plan.first, plan.rest, size - plan.first - plan.rest])
    let offset = 0
    for (const c of chunks.slice(0, -1)) {
      offset += c.length
      expect((offset - FILE_HEADER_BYTES) % BLOCK_BYTES).toBe(0)
    }
    expect(bytesEqual(concatBytes(chunks), data)).toBe(true)
  })

  it('handles an empty source and a source smaller than one chunk', async () => {
    const dir = new MemDir({ empty: new Uint8Array(0), small: new Uint8Array(10).fill(7) })
    expect(await collect(readChunks(await dir.open('empty'), 4 * MIB))).toEqual([])
    const small = await collect(readChunks(await dir.open('small'), 4 * MIB))
    expect(small.map((c) => c.length)).toEqual([10])
  })

  it('stops with the abort reason between reads', async () => {
    const dir = new MemDir({ f: new Uint8Array(FILE_HEADER_BYTES + 3 * BLOCK_BYTES) })
    const ac = new AbortController()
    const gen = readChunks(await dir.open('f'), FILE_HEADER_BYTES + BLOCK_BYTES, ac.signal)
    expect((await gen.next()).value).toBeInstanceOf(Uint8Array)
    ac.abort()
    await expect(gen.next()).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('propagates read failures from the source', async () => {
    const dir = new MemDir({ f: new Uint8Array(FILE_HEADER_BYTES + 3 * BLOCK_BYTES) })
    dir.failRead('f', FILE_HEADER_BYTES + BLOCK_BYTES)
    const gen = readChunks(await dir.open('f'), FILE_HEADER_BYTES + BLOCK_BYTES)
    await gen.next()
    await expect(gen.next()).rejects.toMatchObject({ name: 'NotReadableError' })
  })
})
