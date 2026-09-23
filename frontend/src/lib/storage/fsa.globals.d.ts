// File System Access API members that TypeScript 5.6's lib.dom does not
// declare (PLAN_msd_management s3): the directory picker and the per-handle
// permission calls. FileSystem*Handle, FileSystemWritableFileStream and the
// async `entries()` iterator already come from lib DOM + DOM.AsyncIterable.
// A module (export {}) with a global augmentation, so it merges rather than
// shadows.
export {}

declare global {
  interface StorageDirectoryPickerOptions {
    /** Chrome remembers a separate last directory per id. */
    id?: string
    mode?: 'read' | 'readwrite'
    startIn?: 'desktop' | 'documents' | 'downloads' | 'music' | 'pictures' | 'videos' | FileSystemHandle
  }

  interface StorageHandlePermissionDescriptor {
    mode?: 'read' | 'readwrite'
  }

  interface Window {
    showDirectoryPicker(options?: StorageDirectoryPickerOptions): Promise<FileSystemDirectoryHandle>
  }

  interface FileSystemHandle {
    queryPermission(descriptor?: StorageHandlePermissionDescriptor): Promise<PermissionState>
    requestPermission(descriptor?: StorageHandlePermissionDescriptor): Promise<PermissionState>
  }
}
