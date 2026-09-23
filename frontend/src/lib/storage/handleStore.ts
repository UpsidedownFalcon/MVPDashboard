// Persisted directory handles (PLAN_msd_management s3): Chrome lets a
// FileSystemDirectoryHandle be stored in IndexedDB and re-checked for
// permission on the next visit, so the sleeve and the destination folder
// survive a reload. Every call swallows a missing or blocked IndexedDB
// (private windows, disabled storage): the page then simply asks again.
const DB_NAME = 'hippos-storage'
const DB_VERSION = 1
const STORE = 'handles'

export type HandleKey = 'sleeve' | 'dest'

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      reject(new Error('IndexedDB unavailable'))
      return
    }
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) req.result.createObjectStore(STORE)
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error ?? new Error('IndexedDB open failed'))
    req.onblocked = () => reject(new Error('IndexedDB blocked'))
  })
}

function awaitRequest<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error ?? new Error('IndexedDB request failed'))
  })
}

async function withStore<T>(
  mode: IDBTransactionMode,
  op: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  const db = await openDb()
  try {
    return await awaitRequest(op(db.transaction(STORE, mode).objectStore(STORE)))
  } finally {
    db.close()
  }
}

export async function saveHandle(key: HandleKey, handle: FileSystemDirectoryHandle): Promise<void> {
  try {
    await withStore('readwrite', (s) => s.put(handle, key))
  } catch {
    // no persistence: the user picks again next time
  }
}

export async function loadHandle(key: HandleKey): Promise<FileSystemDirectoryHandle | null> {
  try {
    const value: unknown = await withStore('readonly', (s) => s.get(key))
    return value instanceof FileSystemDirectoryHandle ? value : null
  } catch {
    return null
  }
}

export async function clearHandle(key: HandleKey): Promise<void> {
  try {
    await withStore('readwrite', (s) => s.delete(key))
  } catch {
    // nothing stored, or nowhere to store: same outcome
  }
}
