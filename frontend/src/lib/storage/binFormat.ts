// On-disk facts of LOG_NNNN.BIN (firmware log_format.h, format_version 1,
// frozen; PLAN_msd_management s3). Every offset, magic and limit of the format
// is a named constant here so the scanner, the transfer engine and CS2's
// decoder never restate a number. All integers are little-endian.
import { crc32Final, crc32Init, crc32Of, crc32Update } from './crc32'

/** File header size; the first block starts right after it. */
export const FILE_HEADER_BYTES = 512
export const BLOCK_BYTES = 4096
export const BLOCK_HEADER_BYTES = 32
export const SAMPLE_BYTES = 14
/** floor((BLOCK_BYTES - BLOCK_HEADER_BYTES) / SAMPLE_BYTES). */
export const MAX_SAMPLES = 290
/** "NYKS" read as a LE u32. */
export const FILE_MAGIC = 0x534b594e
export const FORMAT_VERSION = 1
export const BLOCK_MAGIC = 0xb10c

export const BLOCK_TYPE_IMU = 0
export const BLOCK_TYPE_TIME_SYNC = 1
export const BLOCK_TYPE_SESSION_END = 2

export const FLAG_FIFO_OVERFLOW = 1 << 0
export const FLAG_TS_CLAMPED = 1 << 1

export const SENSOR_THIGH = 1
export const SENSOR_SHIN = 2

// log_file_header_t field offsets.
export const FH_OFF_MAGIC = 0
export const FH_OFF_FORMAT_VERSION = 4
export const FH_OFF_HEADER_SIZE = 6
export const FH_OFF_FW = 8
export const FH_FW_BYTES = 16
export const FH_OFF_DEVICE_ID = 24
export const FH_OFF_SOURCE_ID = 25
export const FH_OFF_SENSOR_COUNT = 26
export const FH_OFF_FIFO_WATERMARK = 27
export const FH_OFF_ODR_HZ = 28
export const FH_OFF_ACCEL_FS_G = 32
export const FH_OFF_GYRO_FS_DPS = 34
export const FH_OFF_ACCEL_SCALE = 36
export const FH_OFF_GYRO_SCALE = 40
export const FH_OFF_SESSION_ID = 44
export const FH_OFF_BOOT_ESP_US = 48
export const FH_OFF_UTC_VALID = 56
/** crc32 over bytes [0, FH_OFF_CRC). */
export const FH_OFF_CRC = 508

// log_block_header_t field offsets.
export const BH_OFF_MAGIC = 0
export const BH_OFF_TYPE = 2
export const BH_OFF_SENSOR_ID = 3
export const BH_OFF_SEQ = 4
export const BH_OFF_BASE_TS_US = 8
export const BH_OFF_SAMPLE_COUNT = 16
export const BH_OFF_FLAGS = 18
/** crc32 over the whole block with these four bytes zeroed. */
export const BH_OFF_CRC = 20
export const BH_OFF_RESERVED = 24

/** IMU blocks: log_sample_t[sample_count] from here. */
export const SAMPLE_AREA_OFFSET = 32
export const SAMPLE_OFF_DT_US = 0
export const SAMPLE_OFF_AX = 2
export const SAMPLE_OFF_AY = 4
export const SAMPLE_OFF_AZ = 6
export const SAMPLE_OFF_GX = 8
export const SAMPLE_OFF_GY = 10
export const SAMPLE_OFF_GZ = 12

/** TIME_SYNC blocks: log_time_sync_t from here. */
export const SYNC_PAYLOAD_OFFSET = 32
export const SYNC_OFF_ESP_US = 32
export const SYNC_OFF_UNIX_US = 40
export const SYNC_OFF_SOURCE = 48

export interface FileHeader {
  formatVersion: number
  headerSize: number
  /** NUL-trimmed ASCII, e.g. "1.1.0". */
  fw: string
  deviceId: number
  sourceId: number
  sensorCount: number
  fifoWatermark: number
  odrHz: number
  accelFsG: number
  gyroFsDps: number
  /** g per LSB, read from the stored f32. */
  accelScale: number
  /** dps per LSB, read from the stored f32. */
  gyroScale: number
  sessionId: number
  bootEspUs: bigint
  utcValid: boolean
  crc32: number
}

export type FileHeaderErrorCode = 'short' | 'magic' | 'version' | 'crc'

export type FileHeaderResult =
  | { ok: true; header: FileHeader }
  | { ok: false; code: FileHeaderErrorCode }

export interface BlockHeader {
  magic: number
  type: number
  sensorId: number
  seq: number
  baseTsUs: bigint
  sampleCount: number
  flags: number
  crc32: number
}

export type BlockClass = 'valid' | 'bad' | 'unused'

/** A DataView over exactly the bytes of `bytes` (respects subarray offsets). */
export function viewOf(bytes: Uint8Array): DataView {
  return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
}

function asciiNulTrimmed(bytes: Uint8Array): string {
  let out = ''
  for (let i = 0; i < bytes.length; i++) {
    const b = bytes[i]
    if (b === 0) break
    out += b < 0x80 ? String.fromCharCode(b) : '\ufffd'
  }
  return out
}

/** Parse the 512 B file header; mirrors bin2csv.parse_file_header. */
export function parseFileHeader(bytes: Uint8Array): FileHeaderResult {
  if (bytes.length < FILE_HEADER_BYTES) return { ok: false, code: 'short' }
  const v = viewOf(bytes)
  if (v.getUint32(FH_OFF_MAGIC, true) !== FILE_MAGIC) return { ok: false, code: 'magic' }
  const formatVersion = v.getUint16(FH_OFF_FORMAT_VERSION, true)
  const headerSize = v.getUint16(FH_OFF_HEADER_SIZE, true)
  if (formatVersion !== FORMAT_VERSION || headerSize !== FILE_HEADER_BYTES) {
    return { ok: false, code: 'version' }
  }
  const stored = v.getUint32(FH_OFF_CRC, true)
  const computed = crc32Of(bytes.subarray(0, FH_OFF_CRC))
  if (stored !== computed) return { ok: false, code: 'crc' }
  return {
    ok: true,
    header: {
      formatVersion,
      headerSize,
      fw: asciiNulTrimmed(bytes.subarray(FH_OFF_FW, FH_OFF_FW + FH_FW_BYTES)),
      deviceId: v.getUint8(FH_OFF_DEVICE_ID),
      sourceId: v.getUint8(FH_OFF_SOURCE_ID),
      sensorCount: v.getUint8(FH_OFF_SENSOR_COUNT),
      fifoWatermark: v.getUint8(FH_OFF_FIFO_WATERMARK),
      odrHz: v.getUint32(FH_OFF_ODR_HZ, true),
      accelFsG: v.getUint16(FH_OFF_ACCEL_FS_G, true),
      gyroFsDps: v.getUint16(FH_OFF_GYRO_FS_DPS, true),
      accelScale: v.getFloat32(FH_OFF_ACCEL_SCALE, true),
      gyroScale: v.getFloat32(FH_OFF_GYRO_SCALE, true),
      sessionId: v.getUint32(FH_OFF_SESSION_ID, true),
      bootEspUs: v.getBigUint64(FH_OFF_BOOT_ESP_US, true),
      utcValid: v.getUint8(FH_OFF_UTC_VALID) !== 0,
      crc32: stored,
    },
  }
}

/** Read a block header at byte offset `off` of `view` (no validation). */
export function readBlockHeader(view: DataView, off: number): BlockHeader {
  return {
    magic: view.getUint16(off + BH_OFF_MAGIC, true),
    type: view.getUint8(off + BH_OFF_TYPE),
    sensorId: view.getUint8(off + BH_OFF_SENSOR_ID),
    seq: view.getUint32(off + BH_OFF_SEQ, true),
    baseTsUs: view.getBigUint64(off + BH_OFF_BASE_TS_US, true),
    sampleCount: view.getUint16(off + BH_OFF_SAMPLE_COUNT, true),
    flags: view.getUint16(off + BH_OFF_FLAGS, true),
    crc32: view.getUint32(off + BH_OFF_CRC, true),
  }
}

const ZERO_CRC_FIELD = new Uint8Array(4)

/** True when the block's stored CRC matches the CRC over the block with its
 *  CRC field zeroed. Computed incrementally over [0, 20) + four zero bytes +
 *  [24, 4096) so no copy of the block is made. */
export function blockCrcOk(block: Uint8Array): boolean {
  if (block.length !== BLOCK_BYTES) return false
  let state = crc32Init()
  state = crc32Update(state, block.subarray(0, BH_OFF_CRC))
  state = crc32Update(state, ZERO_CRC_FIELD)
  state = crc32Update(state, block.subarray(BH_OFF_RESERVED))
  const stored =
    (block[BH_OFF_CRC] |
      (block[BH_OFF_CRC + 1] << 8) |
      (block[BH_OFF_CRC + 2] << 16) |
      (block[BH_OFF_CRC + 3] << 24)) >>>
    0
  return crc32Final(state) === stored
}

/** 'valid' = block magic + CRC ok; 'unused' = every byte 0x00 or every byte
 *  0xFF (a preallocated or erased-flash tail); anything else is 'bad'. The
 *  magic is checked first: 0xB10C is never an all-same-byte pattern. */
export function classifyBlock(block: Uint8Array): BlockClass {
  if (block.length !== BLOCK_BYTES) return 'bad'
  const magic = block[0] | (block[1] << 8)
  if (magic === BLOCK_MAGIC) return blockCrcOk(block) ? 'valid' : 'bad'
  const fill = block[0]
  if (fill !== 0x00 && fill !== 0xff) return 'bad'
  for (let i = 1; i < block.length; i++) if (block[i] !== fill) return 'bad'
  return 'unused'
}
