// Browser side of the file-operations seam (PLAN_msd_management 4.1): the
// File System Access API behind io.ts's DirLike, plus the picker and the
// permission calls. Nothing above this module touches a handle's methods.
// DOMExceptions propagate untouched: transfer.ts maps them by name
// (NotAllowedError / SecurityError = permission, NotReadableError /
// NotFoundError mid-copy = the sleeve was unplugged).
import { errorName } from './io'
import type { ByteSink, ByteSource, DirEntry, DirLike } from './io'

export type PickerPurpose = 'sleeve' | 'dest'

/** Chrome/Edge on a secure context (https or localhost). */
export function isSupported(): boolean {
  return typeof window !== 'undefined' && 'showDirectoryPicker' in window && window.isSecureContext
}

/** Show the directory picker (needs a user gesture). null = the user
 *  cancelled; anything else throws. `id` keeps a separate last location per
 *  purpose so the sleeve and the destination do not fight over it. */
export async function pickDirectory(id: PickerPurpose): Promise<FileSystemDirectoryHandle | null> {
  try {
    return await window.showDirectoryPicker({ id, mode: 'readwrite' })
  } catch (err) {
    if (errorName(err) === 'AbortError') return null
    throw err
  }
}

/** 'granted' | 'prompt' | 'denied' for read-write access, without asking. */
export function queryPermission(handle: FileSystemHandle): Promise<PermissionState> {
  return handle.queryPermission({ mode: 'readwrite' })
}

/** Ask for read-write access; must run inside a user gesture. */
export function requestPermission(handle: FileSystemHandle): Promise<PermissionState> {
  return handle.requestPermission({ mode: 'readwrite' })
}

/** ByteSource over a File snapshot. Exported for workers/convert.worker.ts,
 *  which wraps `await handle.getFile()` of the raw file it was handed. */
export class FileSource implements ByteSource {
  constructor(private readonly file: File) {}

  get size(): number {
    return this.file.size
  }

  async read(offset: number, length: number): Promise<Uint8Array> {
    return new Uint8Array(await this.file.slice(offset, offset + length).arrayBuffer())
  }
}

class WritableSink implements ByteSink {
  constructor(private readonly stream: FileSystemWritableFileStream) {}

  write(bytes: Uint8Array): Promise<void> {
    return this.stream.write(bytes)
  }

  close(): Promise<void> {
    return this.stream.close()
  }

  abort(): Promise<void> {
    return this.stream.abort()
  }
}

/** DirLike over a directory handle. list() takes sizes from getFile()
 *  (metadata only, no content read); open() snapshots the File once so
 *  every slice of one copy comes from the same version. */
export function fsaDir(handle: FileSystemDirectoryHandle): DirLike {
  return {
    async list(): Promise<DirEntry[]> {
      const entries: DirEntry[] = []
      for await (const [name, child] of handle.entries()) {
        if (child.kind === 'file') {
          const file = await (child as FileSystemFileHandle).getFile()
          entries.push({ name, kind: 'file', size: file.size })
        } else {
          entries.push({ name, kind: 'dir', size: 0 })
        }
      }
      return entries
    },

    async open(name: string): Promise<ByteSource> {
      const fh = await handle.getFileHandle(name)
      return new FileSource(await fh.getFile())
    },

    async create(name: string): Promise<ByteSink> {
      const fh = await handle.getFileHandle(name, { create: true })
      return new WritableSink(await fh.createWritable())
    },

    remove(name: string): Promise<void> {
      return handle.removeEntry(name)
    },

    async subdir(name: string, create: boolean): Promise<DirLike> {
      return fsaDir(await handle.getDirectoryHandle(name, { create }))
    },

    async exists(name: string): Promise<boolean> {
      try {
        await handle.getFileHandle(name)
        return true
      } catch (err) {
        const n = errorName(err)
        if (n === 'NotFoundError') return false
        // a directory of that name: the name is taken all the same
        if (n === 'TypeMismatchError') return true
        throw err
      }
    },
  }
}
