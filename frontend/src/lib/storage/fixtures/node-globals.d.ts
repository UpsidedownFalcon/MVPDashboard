// No @types/node in the frontend; the fixture-writing test reads one
// environment variable (STORAGE_WRITE_FIXTURES) from node's process global.
declare const process: { env: Record<string, string | undefined> }
