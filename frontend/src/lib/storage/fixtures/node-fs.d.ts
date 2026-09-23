// The frontend carries no @types/node (vite and vitest do not need it), yet
// the storage tests read binary fixtures through node's fs. This ambient
// declaration covers exactly the one call fixtures/load.ts makes; vitest
// resolves the real built-in module at run time.
declare module 'node:fs' {
  export function readFileSync(path: string | URL): Uint8Array
}
