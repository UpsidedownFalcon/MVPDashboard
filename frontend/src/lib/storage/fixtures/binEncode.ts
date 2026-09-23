// Test-only encoder for synthetic LOG_NNNN.BIN files (format_version 1) with
// correct header and block CRCs. Not a test itself (vitest includes only
// *.test.ts); CS2's golden fixtures are built from it too.
import {
  BH_OFF_BASE_TS_US,
  BH_OFF_CRC,
  BH_OFF_FLAGS,
  BH_OFF_MAGIC,
  BH_OFF_SAMPLE_COUNT,
  BH_OFF_SENSOR_ID,
  BH_OFF_SEQ,
  BH_OFF_TYPE,
  BLOCK_BYTES,
  BLOCK_MAGIC,
  BLOCK_TYPE_IMU,
  BLOCK_TYPE_SESSION_END,
  BLOCK_TYPE_TIME_SYNC,
  FH_FW_BYTES,
  FH_OFF_ACCEL_FS_G,
  FH_OFF_ACCEL_SCALE,
  FH_OFF_BOOT_ESP_US,
  FH_OFF_CRC,
  FH_OFF_DEVICE_ID,
  FH_OFF_FIFO_WATERMARK,
  FH_OFF_FORMAT_VERSION,
  FH_OFF_FW,
  FH_OFF_GYRO_FS_DPS,
  FH_OFF_GYRO_SCALE,
  FH_OFF_HEADER_SIZE,
  FH_OFF_MAGIC,
  FH_OFF_ODR_HZ,
  FH_OFF_SENSOR_COUNT,
  FH_OFF_SESSION_ID,
  FH_OFF_SOURCE_ID,
  FH_OFF_UTC_VALID,
  FILE_HEADER_BYTES,
  FILE_MAGIC,
  FORMAT_VERSION,
  SAMPLE_AREA_OFFSET,
  SAMPLE_BYTES,
  SAMPLE_OFF_AX,
  SAMPLE_OFF_AY,
  SAMPLE_OFF_AZ,
  SAMPLE_OFF_DT_US,
  SAMPLE_OFF_GX,
  SAMPLE_OFF_GY,
  SAMPLE_OFF_GZ,
  SYNC_OFF_ESP_US,
  SYNC_OFF_SOURCE,
  SYNC_OFF_UNIX_US,
} from '../binFormat'
import { crc32Of } from '../crc32'
import { concatBytes } from '../io'

/** Counts per LSB: full scale over the i16 range. */
const I16_HALF_RANGE = 32768

export interface HeaderSpec {
  magic?: number
  formatVersion?: number
  headerSize?: number
  fw?: string
  deviceId?: number
  sourceId?: number
  sensorCount?: number
  fifoWatermark?: number
  odrHz?: number
  accelFsG?: number
  gyroFsDps?: number
  sessionId?: number
  bootEspUs?: bigint
  utcValid?: boolean
}

export const DEFAULT_HEADER: Required<HeaderSpec> = {
  magic: FILE_MAGIC,
  formatVersion: FORMAT_VERSION,
  headerSize: FILE_HEADER_BYTES,
  fw: '1.2.0',
  deviceId: 1,
  sourceId: 0,
  sensorCount: 2,
  fifoWatermark: 8,
  odrHz: 6400,
  accelFsG: 32,
  gyroFsDps: 4000,
  sessionId: 1,
  bootEspUs: 0n,
  utcValid: false,
}

export function encodeFileHeader(spec: HeaderSpec = {}): Uint8Array {
  const h = { ...DEFAULT_HEADER, ...spec }
  const bytes = new Uint8Array(FILE_HEADER_BYTES)
  const v = new DataView(bytes.buffer)
  v.setUint32(FH_OFF_MAGIC, h.magic, true)
  v.setUint16(FH_OFF_FORMAT_VERSION, h.formatVersion, true)
  v.setUint16(FH_OFF_HEADER_SIZE, h.headerSize, true)
  const fw = new TextEncoder().encode(h.fw).subarray(0, FH_FW_BYTES - 1)
  bytes.set(fw, FH_OFF_FW)
  v.setUint8(FH_OFF_DEVICE_ID, h.deviceId)
  v.setUint8(FH_OFF_SOURCE_ID, h.sourceId)
  v.setUint8(FH_OFF_SENSOR_COUNT, h.sensorCount)
  v.setUint8(FH_OFF_FIFO_WATERMARK, h.fifoWatermark)
  v.setUint32(FH_OFF_ODR_HZ, h.odrHz, true)
  v.setUint16(FH_OFF_ACCEL_FS_G, h.accelFsG, true)
  v.setUint16(FH_OFF_GYRO_FS_DPS, h.gyroFsDps, true)
  v.setFloat32(FH_OFF_ACCEL_SCALE, h.accelFsG / I16_HALF_RANGE, true)
  v.setFloat32(FH_OFF_GYRO_SCALE, h.gyroFsDps / I16_HALF_RANGE, true)
  v.setUint32(FH_OFF_SESSION_ID, h.sessionId, true)
  v.setBigUint64(FH_OFF_BOOT_ESP_US, h.bootEspUs, true)
  v.setUint8(FH_OFF_UTC_VALID, h.utcValid ? 1 : 0)
  v.setUint32(FH_OFF_CRC, crc32Of(bytes.subarray(0, FH_OFF_CRC)), true)
  return bytes
}

export interface Sample {
  dtUs: number
  ax: number
  ay: number
  az: number
  gx: number
  gy: number
  gz: number
}

/** Deterministic samples: a ramp so every field differs. */
export function rampSamples(count: number, seed = 0): Sample[] {
  const out: Sample[] = []
  for (let i = 0; i < count; i++) {
    const k = seed + i
    out.push({
      dtUs: i * 156,
      ax: ((k * 7) % 65536) - 32768,
      ay: ((k * 11) % 65536) - 32768,
      az: ((k * 13) % 65536) - 32768,
      gx: ((k * 17) % 65536) - 32768,
      gy: ((k * 19) % 65536) - 32768,
      gz: ((k * 23) % 65536) - 32768,
    })
  }
  return out
}

export interface ImuBlockSpec {
  type: 'imu'
  sensorId: number
  seq: number
  baseTsUs?: bigint
  samples?: Sample[]
  /** Override the stored sample_count (e.g. 300 to exceed MAX_SAMPLES). */
  count?: number
  flags?: number
  /** Store a wrong CRC so the block classifies as 'bad'. */
  corruptCrc?: boolean
}

export interface SyncBlockSpec {
  type: 'sync'
  seq: number
  espUs: bigint
  unixUs: bigint
  source?: number
}

export interface EndBlockSpec {
  type: 'end'
  seq: number
}

/** A whole block of one byte value (0x00 / 0xFF = unused, else garbage). */
export interface FillBlockSpec {
  type: 'fill'
  byte: number
}

/** Arbitrary bytes, any length (garbage or a partial trailing block). */
export interface RawBlockSpec {
  type: 'raw'
  bytes: Uint8Array
}

export type BlockSpec = ImuBlockSpec | SyncBlockSpec | EndBlockSpec | FillBlockSpec | RawBlockSpec

export function fill(length: number, byte: number): Uint8Array {
  return new Uint8Array(length).fill(byte)
}

function sealBlock(block: Uint8Array, corrupt = false): Uint8Array {
  const v = new DataView(block.buffer, block.byteOffset, block.byteLength)
  v.setUint32(BH_OFF_CRC, 0, true)
  const crc = crc32Of(block)
  v.setUint32(BH_OFF_CRC, corrupt ? crc ^ 0xffffffff : crc, true)
  return block
}

export function encodeBlock(spec: BlockSpec): Uint8Array {
  if (spec.type === 'raw') return spec.bytes.slice()
  if (spec.type === 'fill') return fill(BLOCK_BYTES, spec.byte)
  const block = new Uint8Array(BLOCK_BYTES)
  const v = new DataView(block.buffer)
  v.setUint16(BH_OFF_MAGIC, BLOCK_MAGIC, true)
  v.setUint32(BH_OFF_SEQ, spec.seq, true)
  if (spec.type === 'end') {
    v.setUint8(BH_OFF_TYPE, BLOCK_TYPE_SESSION_END)
    return sealBlock(block)
  }
  if (spec.type === 'sync') {
    v.setUint8(BH_OFF_TYPE, BLOCK_TYPE_TIME_SYNC)
    v.setBigUint64(SYNC_OFF_ESP_US, spec.espUs, true)
    v.setBigUint64(SYNC_OFF_UNIX_US, spec.unixUs, true)
    v.setUint8(SYNC_OFF_SOURCE, spec.source ?? 0)
    return sealBlock(block)
  }
  const samples = spec.samples ?? []
  v.setUint8(BH_OFF_TYPE, BLOCK_TYPE_IMU)
  v.setUint8(BH_OFF_SENSOR_ID, spec.sensorId)
  v.setBigUint64(BH_OFF_BASE_TS_US, spec.baseTsUs ?? 0n, true)
  v.setUint16(BH_OFF_SAMPLE_COUNT, spec.count ?? samples.length, true)
  v.setUint16(BH_OFF_FLAGS, spec.flags ?? 0, true)
  samples.forEach((s, i) => {
    const off = SAMPLE_AREA_OFFSET + i * SAMPLE_BYTES
    v.setUint16(off + SAMPLE_OFF_DT_US, s.dtUs, true)
    v.setInt16(off + SAMPLE_OFF_AX, s.ax, true)
    v.setInt16(off + SAMPLE_OFF_AY, s.ay, true)
    v.setInt16(off + SAMPLE_OFF_AZ, s.az, true)
    v.setInt16(off + SAMPLE_OFF_GX, s.gx, true)
    v.setInt16(off + SAMPLE_OFF_GY, s.gy, true)
    v.setInt16(off + SAMPLE_OFF_GZ, s.gz, true)
  })
  return sealBlock(block, spec.corruptCrc)
}

/** Header + blocks (+ an optional raw tail such as a partial block). */
export function encodeLogFile(
  header: HeaderSpec | Uint8Array = {},
  blocks: BlockSpec[] = [],
  tail: Uint8Array = new Uint8Array(0),
): Uint8Array {
  const head = header instanceof Uint8Array ? header : encodeFileHeader(header)
  return concatBytes([head, ...blocks.map(encodeBlock), tail])
}

/** A copy with one byte inverted. */
export function withByteFlipped(bytes: Uint8Array, offset: number): Uint8Array {
  const out = bytes.slice()
  out[offset] ^= 0xff
  return out
}
