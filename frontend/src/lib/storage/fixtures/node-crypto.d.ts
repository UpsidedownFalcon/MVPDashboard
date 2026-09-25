// No @types/node in the frontend; the golden tests hash the CSV they
// produce with node's built-in SHA-256 (what scripts/storage_goldens.py
// records). Exactly the calls convert/goldens.test.ts makes.
declare module 'node:crypto' {
  export interface Hash {
    update(data: Uint8Array): Hash
    digest(encoding: 'hex'): string
  }
  export function createHash(algorithm: 'sha256'): Hash
}
