import { describe, expect, it } from 'vitest'
import { dupName, findConfigEntry, groupSessions, isIgnoredEntry, LOG_NAME_RE, parseLogName } from './logNames'

describe('LOG_NAME_RE and parseLogName', () => {
  it('matches log names in either case', () => {
    expect(parseLogName('LOG_0010.BIN')).toEqual({ session: 10, kind: 'BIN' })
    expect(parseLogName('log_0010.txt')).toEqual({ session: 10, kind: 'TXT' })
    expect(parseLogName('LOG_9999.TXT')).toEqual({ session: 9999, kind: 'TXT' })
  })

  it('rejects anything else, CONFIG.TXT in every spelling above all', () => {
    for (const name of ['CONFIG.TXT', 'config.txt', 'Config.Txt', 'CONFIG.TXT.crswap']) {
      expect(LOG_NAME_RE.test(name)).toBe(false)
      expect(parseLogName(name)).toBeNull()
    }
    for (const name of ['LOG_010.BIN', 'LOG_00010.BIN', 'LOG0010.BIN', 'LOG_0010.CSV', 'LOG_0010.BIN.crswap', ' LOG_0010.BIN', 'LOG_0010-2.BIN']) {
      expect(parseLogName(name)).toBeNull()
    }
  })
})

describe('isIgnoredEntry', () => {
  it('hides swap files, system folders and dotfiles', () => {
    expect(isIgnoredEntry('LOG_0010.BIN.crswap')).toBe(true)
    expect(isIgnoredEntry('CONFIG.TXT.CRSWAP')).toBe(true)
    expect(isIgnoredEntry('System Volume Information')).toBe(true)
    expect(isIgnoredEntry('$RECYCLE.BIN')).toBe(true)
    expect(isIgnoredEntry('.Trashes')).toBe(true)
    expect(isIgnoredEntry('._LOG_0010.BIN')).toBe(true)
    expect(isIgnoredEntry('LOG_0010.BIN')).toBe(false)
    expect(isIgnoredEntry('CONFIG.TXT')).toBe(false)
  })
})

describe('findConfigEntry', () => {
  it('returns the stored spelling, case-insensitively', () => {
    expect(findConfigEntry(['LOG_0001.BIN', 'config.txt'])).toBe('config.txt')
    expect(findConfigEntry(['CONFIG.TXT'])).toBe('CONFIG.TXT')
    expect(findConfigEntry(['LOG_0001.BIN', 'CONFIG.TXT.crswap'])).toBeNull()
    expect(findConfigEntry([])).toBeNull()
  })
})

describe('groupSessions', () => {
  it('pairs BIN and TXT by session, sorted, tolerating a missing half', () => {
    const sessions = groupSessions([
      { name: 'LOG_0012.TXT', size: 10 },
      { name: 'CONFIG.TXT', size: 723 },
      { name: 'LOG_0010.BIN', size: 4608 },
      { name: 'LOG_0012.BIN', size: 512 },
      { name: 'LOG_0011.BIN', size: 512 },
      { name: 'LOG_0010.TXT', size: 99 },
      { name: 'notes.txt', size: 1 },
    ])
    expect(sessions).toEqual([
      { session: 10, bin: { name: 'LOG_0010.BIN', size: 4608 }, txt: { name: 'LOG_0010.TXT', size: 99 } },
      { session: 11, bin: { name: 'LOG_0011.BIN', size: 512 }, txt: null },
      { session: 12, bin: { name: 'LOG_0012.BIN', size: 512 }, txt: { name: 'LOG_0012.TXT', size: 10 } },
    ])
    expect(groupSessions([{ name: 'LOG_0003.TXT', size: 5 }])).toEqual([
      { session: 3, bin: null, txt: { name: 'LOG_0003.TXT', size: 5 } },
    ])
  })
})

describe('dupName', () => {
  it('inserts the suffix before the extension', () => {
    expect(dupName('LOG_0010.BIN', 2)).toBe('LOG_0010-2.BIN')
    expect(dupName('LOG_0010.TXT', 13)).toBe('LOG_0010-13.TXT')
    expect(dupName('noext', 2)).toBe('noext-2')
  })
})
