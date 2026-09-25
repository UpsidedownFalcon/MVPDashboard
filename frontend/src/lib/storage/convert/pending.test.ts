import { describe, expect, it } from 'vitest'
import { MemDir } from '../memDir'
import { listPending, missingOutputs, outputsPresent, RAW_BIN_RE, unitIdOfFolder } from './pending'

/** `<dest>/<folder>/raw/` holding `rawFiles`, with `outputs` beside raw/. */
async function sleeveFolder(
  dest: MemDir,
  folder: string,
  rawFiles: Record<string, string>,
  outputs: string[] = [],
): Promise<MemDir> {
  const dir = (await dest.subdir(folder, true)) as MemDir
  const raw = (await dir.subdir('raw', true)) as MemDir
  for (const [name, content] of Object.entries(rawFiles)) raw.put(name, content)
  for (const name of outputs) dir.put(name, 'x')
  return dir
}

describe('RAW_BIN_RE', () => {
  it('matches the names the transfer writes, including dup copies, and nothing else', () => {
    expect(RAW_BIN_RE.test('LOG_0010.BIN')).toBe(true)
    expect(RAW_BIN_RE.test('LOG_0010-2.BIN')).toBe(true)
    expect(RAW_BIN_RE.test('LOG_0010-13.BIN')).toBe(true)
    expect(RAW_BIN_RE.test('log_0010.bin')).toBe(true)
    expect(RAW_BIN_RE.test('LOG_0010.TXT')).toBe(false)
    expect(RAW_BIN_RE.test('LOG_0010-2.TXT')).toBe(false)
    expect(RAW_BIN_RE.test('LOG_010.BIN')).toBe(false)
    expect(RAW_BIN_RE.test('LOG_0010.csv')).toBe(false)
    expect(RAW_BIN_RE.test('LOG_0010.BIN.crswap')).toBe(false)
    expect(RAW_BIN_RE.test('CONFIG.TXT')).toBe(false)
  })
})

describe('unitIdOfFolder', () => {
  it('reads the unit id out of a sleeve folder name only', () => {
    expect(unitIdOfFolder('sleeve-u7-1')).toBe('u7-1')
    expect(unitIdOfFolder('sleeve-u255-0')).toBe('u255-0')
    expect(unitIdOfFolder('sleeve-u7-5')).toBeNull()
    expect(unitIdOfFolder('sleeve-')).toBeNull()
    expect(unitIdOfFolder('u7-1')).toBeNull()
    expect(unitIdOfFolder('Photos')).toBeNull()
  })
})

describe('missingOutputs and outputsPresent', () => {
  it('treat an empty file as missing: the placeholder a failed conversion leaves', () => {
    const entries = [
      { name: 'LOG_0010.csv', kind: 'file' as const, size: 12 },
      { name: 'LOG_0010.meta.json', kind: 'file' as const, size: 400 },
      { name: 'LOG_0010_summary.txt', kind: 'file' as const, size: 0 },
      { name: 'raw', kind: 'dir' as const, size: 0 },
    ]
    expect(missingOutputs(entries, 'LOG_0010')).toEqual(['summary'])
    expect(outputsPresent(entries, 'LOG_0010')).toBe(false)
    expect(outputsPresent([...entries.slice(0, 2), { name: 'LOG_0010_summary.txt', kind: 'file', size: 1 }], 'LOG_0010')).toBe(true)
    expect(missingOutputs([], 'LOG_0010')).toEqual(['csv', 'meta', 'summary'])
  })
})

describe('listPending', () => {
  it('lists a raw whose only gap is an empty output file', async () => {
    const dest = new MemDir()
    const dir = await sleeveFolder(dest, 'sleeve-u7-1', { 'LOG_0010.BIN': 'a' }, ['LOG_0010.csv', 'LOG_0010.meta.json'])
    dir.put('LOG_0010_summary.txt', '')
    const pending = await listPending(dest)
    expect(pending.map((p) => [p.localName, p.missing])).toEqual([['LOG_0010.BIN', ['summary']]])
  })

  it('lists raw BINs without outputs across sleeve folders, sorted by folder then name', async () => {
    const dest = new MemDir()
    await sleeveFolder(dest, 'sleeve-u7-1', { 'LOG_0011.BIN': 'b', 'LOG_0010.BIN': 'a' })
    await sleeveFolder(dest, 'sleeve-u3-0', { 'LOG_0002.BIN': 'ccc' })
    const pending = await listPending(dest)
    expect(pending.map((p) => `${p.folder}/${p.localName}`)).toEqual([
      'sleeve-u3-0/LOG_0002.BIN',
      'sleeve-u7-1/LOG_0010.BIN',
      'sleeve-u7-1/LOG_0011.BIN',
    ])
    expect(pending[0]).toEqual({
      folder: 'sleeve-u3-0',
      localName: 'LOG_0002.BIN',
      stem: 'LOG_0002',
      size: 3,
      unitId: 'u3-0',
      missing: ['csv', 'meta', 'summary'],
    })
  })

  it('handles dup-named copies with their own stem and ignores TXT files', async () => {
    const dest = new MemDir()
    await sleeveFolder(dest, 'sleeve-u7-1', {
      'LOG_0010.BIN': 'a',
      'LOG_0010-2.BIN': 'b',
      'LOG_0010.TXT': 'diag',
      'LOG_0010-2.TXT': 'diag',
    })
    const pending = await listPending(dest)
    expect(pending.map((p) => [p.localName, p.stem])).toEqual([
      ['LOG_0010-2.BIN', 'LOG_0010-2'],
      ['LOG_0010.BIN', 'LOG_0010'],
    ])
  })

  it('ignores folders that are not sleeve folders, files in the root and folders without raw/', async () => {
    const dest = new MemDir({ 'LOG_0010.BIN': 'stray' })
    await sleeveFolder(dest, 'sleeve-u7-1', { 'LOG_0010.BIN': 'a' })
    await sleeveFolder(dest, 'Photos', { 'LOG_0010.BIN': 'a' })
    await sleeveFolder(dest, 'sleeve-u7-5', { 'LOG_0010.BIN': 'a' })
    await sleeveFolder(dest, 'sleeve-', { 'LOG_0010.BIN': 'a' })
    await dest.subdir('sleeve-u9-0', true)
    const pending = await listPending(dest)
    expect(pending.map((p) => p.folder)).toEqual(['sleeve-u7-1'])
  })

  it('skips raw files whose three outputs are all present', async () => {
    const dest = new MemDir()
    await sleeveFolder(dest, 'sleeve-u7-1', { 'LOG_0010.BIN': 'a', 'LOG_0011.BIN': 'b' }, [
      'LOG_0010.csv',
      'LOG_0010.meta.json',
      'LOG_0010_summary.txt',
    ])
    const pending = await listPending(dest)
    expect(pending.map((p) => p.localName)).toEqual(['LOG_0011.BIN'])
  })

  it('reports exactly which outputs a partially converted raw file lacks', async () => {
    const dest = new MemDir()
    await sleeveFolder(
      dest,
      'sleeve-u7-1',
      { 'LOG_0010.BIN': 'a', 'LOG_0011.BIN': 'b', 'LOG_0012.BIN': 'c' },
      ['LOG_0010.csv', 'LOG_0010.meta.json', 'LOG_0011_summary.txt', 'LOG_0012.csv'],
    )
    const pending = await listPending(dest)
    expect(pending.map((p) => [p.localName, p.missing])).toEqual([
      ['LOG_0010.BIN', ['summary']],
      ['LOG_0011.BIN', ['csv', 'meta']],
      ['LOG_0012.BIN', ['meta', 'summary']],
    ])
  })

  it('looks for the outputs beside raw/, not inside it', async () => {
    const dest = new MemDir()
    await sleeveFolder(dest, 'sleeve-u7-1', {
      'LOG_0010.BIN': 'a',
      'LOG_0010.csv': 'misplaced',
      'LOG_0010.meta.json': 'misplaced',
      'LOG_0010_summary.txt': 'misplaced',
    })
    const pending = await listPending(dest)
    expect(pending.map((p) => p.missing)).toEqual([['csv', 'meta', 'summary']])
  })

  it('skips a sleeve folder that cannot be read and keeps the others', async () => {
    const dest = new MemDir()
    await sleeveFolder(dest, 'sleeve-u3-0', { 'LOG_0002.BIN': 'a' })
    const broken = await sleeveFolder(dest, 'sleeve-u7-1', { 'LOG_0010.BIN': 'a' })
    broken.unplug()
    const pending = await listPending(dest)
    expect(pending.map((p) => p.folder)).toEqual(['sleeve-u3-0'])
  })

  it('is empty for an empty destination and throws when the destination itself cannot be listed', async () => {
    expect(await listPending(new MemDir())).toEqual([])
    await expect(listPending(new MemDir().unplug())).rejects.toMatchObject({ name: 'NotFoundError' })
  })
})
