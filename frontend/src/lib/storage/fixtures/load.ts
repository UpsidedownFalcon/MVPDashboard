// Fixture access for the storage tests (vitest runs on node; no DOM).
import { readFileSync } from 'node:fs'

/** Bytes of a file in this directory. */
export function fixtureBytes(name: string): Uint8Array {
  return new Uint8Array(readFileSync(new URL(`./${name}`, import.meta.url)))
}

export const FIRMWARE_SAMPLE_CONFIG = 'config_firmware_sample.txt'
export const GENERATED_CONFIG_1_2_0 = 'config_generated_1_2_0.txt'
export const LOG_0010_HEAD64_BIN = 'LOG_0010.head64.bin'
export const LOG_0010_HEAD_TXT = 'LOG_0010.head.txt'
