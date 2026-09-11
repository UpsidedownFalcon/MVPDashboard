// Vitest: pure-module unit tests only (lib/demo, lib/format). No DOM
// environment on purpose - components are verified by `tsc -b` and the
// STAGE4 manual checklist, not rendered here.
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})
