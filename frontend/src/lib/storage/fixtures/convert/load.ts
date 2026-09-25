// Access to the decoder fixtures and goldens in this directory (vitest on
// node; no DOM). The parent's load.ts stays untouched.
import { existsSync, readFileSync } from 'node:fs'

const decoder = new TextDecoder()

/** file: URL of a file in this directory (also what the writer test writes to). */
export function convertFixtureUrl(name: string): URL {
  return new URL(`./${name}`, import.meta.url)
}

export function convertFixtureExists(name: string): boolean {
  return existsSync(convertFixtureUrl(name))
}

export function convertFixtureBytes(name: string): Uint8Array {
  return new Uint8Array(readFileSync(convertFixtureUrl(name)))
}

/** UTF-8 text of a golden (`<name>.csv.sha256`, `<name>.meta.json`, `<name>.summary.txt`). */
export function convertFixtureText(name: string): string {
  return decoder.decode(convertFixtureBytes(name))
}

/** Golden file names for a fixture name (scripts/storage_goldens.py writes them). */
export function goldenNames(name: string): { csvSha256: string; meta: string; summary: string } {
  return { csvSha256: `${name}.csv.sha256`, meta: `${name}.meta.json`, summary: `${name}.summary.txt` }
}
