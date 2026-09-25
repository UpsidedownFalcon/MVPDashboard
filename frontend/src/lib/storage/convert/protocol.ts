// Messages between the page's ConversionQueue and workers/convert.worker.ts
// (agent-docs/03_PLAN_csv_summary 4.5). Handles cross postMessage within an
// origin and keep their permission state, so the worker gets the raw file's
// handle and the output folder's handle, never paths. One job at a time in
// each direction; every message carries the job id so a late message from a
// job the page has forgotten is recognised as such.
import { errorName } from '../io'
import type { ConvertErrorCode, ConvertPhase, MetaJson, OutputNames } from './types'

/** ConvertError's codes plus the two only the main thread can see: the
 *  browser refused the destination handle, or the worker itself died. */
export type ConversionErrorCode = ConvertErrorCode | 'permission' | 'worker'

/** Text for the summary's placement column (decision S): per sensor id
 *  (1 thigh, 2 shin), `fallback` for any other id. Built on the main thread
 *  because the unit's side comes from the ['units'] query. */
export interface Placement {
  labels: Record<number, string>
  fallback: string
}

export type MainToWorker =
  | {
      type: 'convert'
      jobId: string
      raw: FileSystemFileHandle
      outDir: FileSystemDirectoryHandle
      stem: string
      sourceFile: string
      placement: Placement
    }
  | { type: 'cancel'; jobId: string }

export type WorkerToMain =
  | {
      type: 'progress'
      jobId: string
      phase: ConvertPhase
      bytesDone: number
      bytesTotal: number
      rows: number
    }
  | { type: 'done'; jobId: string; meta: MetaJson; summary: string; outputs: OutputNames }
  | { type: 'failed'; jobId: string; code: ConversionErrorCode; detail?: string }
  | { type: 'cancelled'; jobId: string }

/** The DOMException names that mean the browser withdrew the folder grant
 *  (the same two transfer.ts maps to its 'permission'). */
export function isPermissionError(err: unknown): boolean {
  const n = errorName(err)
  return n === 'NotAllowedError' || n === 'SecurityError'
}
