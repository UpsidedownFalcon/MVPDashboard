import { describe, expect, it } from 'vitest'
import {
  STORAGE_CSV_WRITE_CHUNK_BYTES,
  STORAGE_GAP_US,
  STORAGE_NOISE_F_CUT_HZ,
  STORAGE_READ_CHUNK_BYTES,
  STORAGE_TS_OUTLIER_US,
} from '../../config'
import {
  BLOCK_BYTES,
  BLOCK_TYPE_IMU,
  classifyBlock,
  FILE_HEADER_BYTES,
  MAX_SAMPLES,
  parseFileHeader,
  readBlockHeader,
  SAMPLE_AREA_OFFSET,
  SAMPLE_BYTES,
  SAMPLE_OFF_AX,
  SAMPLE_OFF_AY,
  SAMPLE_OFF_AZ,
  SAMPLE_OFF_DT_US,
  SAMPLE_OFF_GX,
  SAMPLE_OFF_GY,
  SAMPLE_OFF_GZ,
  viewOf,
} from '../binFormat'
import { encodeLogFile, rampSamples, type BlockSpec } from '../fixtures/binEncode'
import { fixtureBytes, LOG_0010_HEAD64_BIN } from '../fixtures/load'
import { MemDir } from '../memDir'
import { convertAndSummarize } from './pipeline'
import { buildValueTables } from './scaled'
import { StatsSink } from './stats'
import { renderSummary } from './summary'
import { ConvertError, outputNames, type MetaJson, type PipelineOptions } from './types'

const placement = (sid: number) => (sid === 1 ? 'thigh' : 'shin')

function options(stem: string, over: Partial<PipelineOptions> = {}): PipelineOptions {
  return {
    stem,
    sourceFile: `${stem}.BIN`,
    readChunkBytes: STORAGE_READ_CHUNK_BYTES,
    writeChunkBytes: STORAGE_CSV_WRITE_CHUNK_BYTES,
    placement,
    fCutHz: STORAGE_NOISE_F_CUT_HZ,
    gapUs: STORAGE_GAP_US,
    tsOutlierUs: STORAGE_TS_OUTLIER_US,
    ...over,
  }
}

async function run(bytes: Uint8Array, stem: string, out = new MemDir(), over: Partial<PipelineOptions> = {}) {
  const src = new MemDir({ [`${stem}.BIN`]: bytes })
  const result = await convertAndSummarize(await src.open(`${stem}.BIN`), out, options(stem, over))
  return { result, out }
}

/** The summary computed without convert.ts: the local decode loop (as in
 *  stats.test.ts) through a StatsSink, rendered with the meta the pipeline
 *  returned. */
function expectedSummary(bytes: Uint8Array, meta: MetaJson, stem: string): string {
  const parsed = parseFileHeader(bytes)
  if (!parsed.ok) throw new Error(parsed.code)
  const sink = new StatsSink({ fCutHz: STORAGE_NOISE_F_CUT_HZ, gapUs: STORAGE_GAP_US, tsOutlierUs: STORAGE_TS_OUTLIER_US })
  sink.start(parsed.header, buildValueTables(parsed.header))
  const view = viewOf(bytes)
  for (const pass of [1, 2] as const) {
    for (let off = FILE_HEADER_BYTES; off + BLOCK_BYTES <= bytes.length; off += BLOCK_BYTES) {
      if (classifyBlock(bytes.subarray(off, off + BLOCK_BYTES)) !== 'valid') continue
      const bh = readBlockHeader(view, off)
      if (bh.type !== BLOCK_TYPE_IMU || bh.sampleCount > MAX_SAMPLES) continue
      const base = Number(bh.baseTsUs)
      for (let i = 0; i < bh.sampleCount; i++) {
        const p = off + SAMPLE_AREA_OFFSET + i * SAMPLE_BYTES
        const args = [
          base + view.getUint16(p + SAMPLE_OFF_DT_US, true),
          view.getInt16(p + SAMPLE_OFF_AX, true),
          view.getInt16(p + SAMPLE_OFF_AY, true),
          view.getInt16(p + SAMPLE_OFF_AZ, true),
          view.getInt16(p + SAMPLE_OFF_GX, true),
          view.getInt16(p + SAMPLE_OFF_GY, true),
          view.getInt16(p + SAMPLE_OFF_GZ, true),
        ] as const
        if (pass === 1) sink.pass1(bh.sensorId, ...args)
        else sink.pass2(bh.sensorId, ...args)
      }
    }
    if (pass === 1) sink.pass1Done()
  }
  return renderSummary({
    csvName: `${stem}.csv`,
    meta,
    stats: sink.finish(),
    placement,
    fCutHz: STORAGE_NOISE_F_CUT_HZ,
    gapUs: STORAGE_GAP_US,
  })
}

const SMALL_BLOCKS: BlockSpec[] = [
  { type: 'sync', seq: 0, espUs: 1000n, unixUs: 1_700_000_000_000_000n },
  { type: 'imu', sensorId: 1, seq: 1, baseTsUs: 1000n, samples: rampSamples(50) },
  { type: 'imu', sensorId: 2, seq: 2, baseTsUs: 1000n, samples: rampSamples(50, 5) },
  { type: 'imu', sensorId: 1, seq: 3, baseTsUs: 8800n, samples: rampSamples(50) },
  { type: 'end', seq: 4 },
]
const small = encodeLogFile({ sessionId: 3 }, SMALL_BLOCKS)
const head64 = fixtureBytes(LOG_0010_HEAD64_BIN)

describe('convertAndSummarize', () => {
  it('writes the CSV, the meta and the summary for a small synthetic log', async () => {
    const { result, out } = await run(small, 'LOG_0001')
    expect(result.outputs).toEqual(outputNames('LOG_0001'))
    expect(out.files.has('LOG_0001.csv')).toBe(true)
    expect(out.files.has('LOG_0001.meta.json')).toBe(true)
    expect(out.text('LOG_0001_summary.txt')).toBe(result.summary)
    expect(out.openSinks).toBe(0)
    expect(result.meta.rows).toEqual({ '1': 100, '2': 50 })
    expect(result.meta.clean_end).toBe(true)
    expect(result.summary).toBe(expectedSummary(small, result.meta, 'LOG_0001'))
    expect(result.summary).toContain('  UTC sync: 2023-11-14 22:13:20Z (1 sync block(s))\n')
    expect(result.summary).toContain('1) Unique sensors: 2\n')
    expect(result.summary).toContain('   (1, 1)  thigh ')
    expect(result.summary.endsWith('integration.\n\n')).toBe(true)
  })

  it('reproduces the head64 summary end to end', async () => {
    const { result, out } = await run(head64, 'LOG_0010')
    expect(result.meta.rows).toEqual({ '1': 9280, '2': 9280 })
    expect(result.summary).toBe(expectedSummary(head64, result.meta, 'LOG_0010'))
    expect(result.summary).toContain('File: LOG_0010.csv\n')
    expect(result.summary).toContain('6410Hz')
    expect(out.text('LOG_0010_summary.txt')).toBe(result.summary)
    expect(out.files.get('LOG_0010.csv')?.length).toBeGreaterThan(1_000_000)
  })

  it('a summary write failure surfaces as ConvertError write with no partial file', async () => {
    const out = new MemDir().failWrite('LOG_0001_summary.txt')
    await expect(run(small, 'LOG_0001', out)).rejects.toMatchObject({ name: 'ConvertError', code: 'write' })
    expect(out.files.has('LOG_0001_summary.txt')).toBe(false)
    expect(out.files.has('LOG_0001.csv')).toBe(true)
    expect(out.openSinks).toBe(0)
  })

  it('a summary close failure is a write error too', async () => {
    const out = new MemDir().failClose('LOG_0001_summary.txt')
    const err = await run(small, 'LOG_0001', out).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ConvertError)
    expect((err as ConvertError).code).toBe('write')
    expect(out.openSinks).toBe(0)
  })
})
