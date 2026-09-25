// The frontend carries no @types/node (vite and vitest do not need it), yet
// the storage tests read binary fixtures through node's fs, and
// convert/fixtures.write.test.ts (STORAGE_WRITE_FIXTURES=1) writes them.
// This ambient declaration covers exactly the calls fixtures/load.ts,
// fixtures/convert/load.ts and that test make; vitest resolves the real
// built-in module at run time.
declare module 'node:fs' {
  export function readFileSync(path: string | URL): Uint8Array
  export function writeFileSync(path: string | URL, data: Uint8Array): void
  export function mkdirSync(path: string | URL, opts?: { recursive?: boolean }): void
  export function existsSync(path: string | URL): boolean
}
