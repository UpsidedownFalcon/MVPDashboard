// Every user-facing string of the Sleeve storage page (PLAN_msd_management
// 4.1). lib/text.test.ts walks this table for the plain-ASCII punctuation
// rule (no em/en dash, ellipsis or middle dot) and "soldier, never athlete".
// Composite messages are TEMPLATES in the table with {slot} holes that
// fill() closes, so the test sees every word the page can show. The
// user-facing device word is "sleeve"; the firmware key names appear only
// where the sleeve's own file is being described.
import {
  CONFIG_SPEC,
  type ConfigKey,
  type ValidateCode,
} from './configSchema'
import type { TransferErrorCode } from './transfer'

export const STORAGE_COPY = {
  title: 'Sleeve storage',
  navItem: 'Sleeve storage',
  intro:
    'Plug a knee sleeve into this computer and open its HIPPOSDATA drive to change its settings or move its log files here.',
  unsupported:
    'Sleeve storage needs Chrome or Edge on a secure (https) address. This browser or address cannot open drives.',

  drive: {
    open: 'Open sleeve drive',
    reconnect: 'Reconnect sleeve drive',
    checking: 'Reading the sleeve...',
    name: 'Drive: {name}',
    invalid: {
      'no-config':
        'That folder has no CONFIG.TXT. Pick the HIPPOSDATA drive itself, not a folder inside it.',
      'read-failed': 'Could not read the drive - {detail}',
      'permission-denied':
        'The browser did not get permission to use the drive. Try again and choose Allow.',
    },
  },

  identity: {
    line: 'Sleeve {unit} | {name}',
    unknown: 'not yet seen by the dashboard',
  },

  editor: {
    basicSection: 'Sleeve settings',
    fields: {
      wifi_ssid: {
        label: 'WiFi network',
        help: 'The network the sleeve joins to stream live data. Cannot be left empty: the sleeve would keep its factory network.',
      },
      wifi_password: {
        label: 'WiFi password',
        help: "Empty keeps the sleeve's factory password. Leave it empty only for an open network.",
      },
      device_id: {
        label: 'Sleeve number',
        help: 'The number this sleeve reports as. Allocate it yourself, 0 to 255, and keep it unique per leg.',
      },
      source_id: {
        label: 'Leg this sleeve is worn on',
        help: '',
      },
      diag_log_enabled: {
        label: 'Write a diagnostics log (LOG_NNNN.TXT) next to each data file',
        help: '',
      },
      stream_enabled: {
        label: 'Stream live data over WiFi when connected',
        help: '',
      },
    },
    password: { show: 'Show', hide: 'Hide' },
    duplicateWarning:
      'A sleeve with this number and leg is already known to the dashboard. If that is this sleeve, ignore this.',
    legLeft: 'Left',
    legRight: 'Right',
    udp: {
      streamsTo: 'Streams to {ip}:{port}',
      thisDashboard: '(this dashboard)',
      notThisDashboard: '(not this dashboard)',
      unknown: '(target unknown)',
      point: 'Point at this dashboard',
      pointDisabled:
        'The dashboard could not work out its own address. Set UDP_PUBLIC_IP on the server.',
      notSet: 'not set',
    },
    advanced: {
      summary: 'Advanced settings',
      warning:
        'Changing these can make the sleeve misbehave: stop logging, mis-scale its sensors, drain or over-protect its battery, or stop it streaming. Only change them if an engineer asked you to.',
      ack: 'I understand',
      labels: {
        udp_ip: 'Stream destination IPv4',
        udp_port: 'Stream destination port',
        low_batt_mv: 'Low-battery stop (mV)',
        accel_fs_g: 'Accelerometer full scale (g)',
        gyro_fs_dps: 'Gyroscope full scale (deg/s)',
        wifi_tx_power_dbm: 'WiFi transmit power (dBm, steps of 0.25)',
        batt_cal_true_mv: 'Battery calibration: measured pack (mV, 0 = off)',
        batt_cal_raw_mv: 'Battery calibration: raw reading (mV, 0 = off)',
      },
      unitG: '{n} g',
      unitDps: '{n} deg/s',
    },
    validation: {
      empty: 'Cannot be empty.',
      range: 'Must be between {min} and {max}.',
      rangeZeroOr: 'Must be 0 or between {min} and {max}.',
      'not-allowed': 'Must be one of {allowed}.',
      'too-long':
        'Too long: at most {max} bytes (a character outside plain ASCII counts as 2 to 4).',
      whitespace: 'Cannot start or end with a space: the sleeve trims it off.',
      format: 'Not a valid value for this setting.',
      formatInt: 'Whole number only, digits 0 to 9.',
      formatIp: 'Must be an IPv4 address like 192.168.1.10.',
      formatQdbm: 'Must be a number from 2 to 20 in steps of 0.25.',
      'line-too-long': 'Too long for the sleeve to read as one line.',
    },
    notices: {
      bom: 'This file starts with a byte-order mark, which makes the sleeve ignore its first setting.',
      repair: 'Repair file',
      repairQueued: 'The byte-order mark will be removed when you save.',
      duplicate: '{key} appears more than once; the last value is the one the sleeve uses.',
      octal:
        '{key} reads as {n} to the sleeve (a leading zero means octal). Save to write it as plain decimal.',
      octalUnreadable:
        '{key} is written as {text}, which the sleeve cannot read as a number, so it uses its built-in default. Enter a value to fix it.',
      overlong: 'Line {n} is longer than the sleeve can read in one go and will be split.',
    },
    save: 'Save to sleeve',
    saving: 'Writing...',
    saved: {
      title: 'Saved. Now eject the HIPPOSDATA drive, then unplug the cable.',
      body: 'The sleeve re-reads its settings about 2 seconds after the cable is out and starts a new session.',
      powerCycle: 'WiFi network or password changed: also switch the sleeve off and on again.',
      fsSynced: 'Dashboard full scale for {unit} updated to match.',
      fsSyncFailed:
        "Dashboard full scale for {unit} could not be updated - {detail}. Set it on the soldier's page.",
      identityChanged:
        'This sleeve will now appear as a new soldier ({to}). Pairing, leg and history stay with {from}.',
    },
    errors: {
      write: 'Could not write to the sleeve - {detail}',
      readback:
        'The file read back differently from what was written; nothing else was changed. Try again.',
    },
  },

  logs: {
    section: 'Log files on the sleeve',
    empty: 'No log files on this sleeve.',
    columns: {
      select: 'Select',
      selectAll: 'Select all log files',
      file: 'File',
      size: 'Size',
      session: 'Session',
      firmware: 'Firmware',
      sleeve: 'Sleeve',
      fullScale: 'Full scale',
      status: 'Status',
    },
    none: '--',
    headerError: {
      short: 'header too short',
      magic: 'not a sleeve log',
      version: 'unknown log format',
      crc: 'header checksum wrong',
      read: 'header could not be read',
    },
  },

  destination: {
    choose: 'Choose destination folder',
    reconnect: 'Reconnect destination folder',
    current: 'Destination: {name}',
    hint: 'Each file goes to sleeve-uN-M/raw inside the destination, named after the sleeve number and leg recorded in that file.',
  },

  transfer: {
    keepCopies: 'Keep copies on the sleeve (do not delete after transfer)',
    start: 'Transfer selected',
    cancel: 'Cancel',
    cancelling: 'Cancelling...',
    doNotUnplug: 'Do not unplug the sleeve while a transfer is running.',
    progressLabel: 'Transfer progress',
    runLine: '{done} of {total} | {rate} | {eta} left',
    status: {
      queued: 'Queued',
      copying: 'Copying {pct}% | {rate} | {eta} left',
      verifying: 'Verifying copy',
      deleting: 'Removing from sleeve',
      doneRemoved: 'Copied and removed from sleeve',
      doneKept: 'Copied (kept on sleeve)',
      alreadyTransferred: 'Already transferred',
      failed: 'Failed: {reason}',
      cancelled: 'Cancelled',
    },
    failed: {
      permission: 'the browser lost permission to the drive or folder',
      'card-read': 'the sleeve stopped answering - was it unplugged?',
      'dest-write': 'could not write to the destination folder',
      'verify-mismatch':
        'the copy did not match the sleeve; the copy was discarded and the file was left on the sleeve',
      'card-delete': 'the copy is good but the file could not be removed from the sleeve',
      aborted: 'cancelled',
    },
    summary:
      'Done: {copied} copied, {already} already transferred, {failed} failed, {deleted} removed from the sleeve.',
    notStarted: '{n} not started.',
    stopped: 'Transfer stopped unexpectedly - {detail}',
    speedNote:
      'Sleeves transfer at about 1 MB/s over USB, so a 512 MB file takes about 9 minutes.',
  },

  units: {
    b: 'B',
    kb: 'KB',
    mb: 'MB',
    gb: 'GB',
    perSecond: '{n} MB/s',
    seconds: '{n} s',
    minutes: '{n} min',
    hoursMinutes: '{h} h {m} min',
  },
} as const

/** Close the {slot} holes of a template. Unknown slots are left as-is so a
 *  typo shows up in the UI rather than vanishing. */
export function fill(template: string, params: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (whole, name: string) =>
    name in params ? String(params[name]) : whole,
  )
}

/** The inline message for a validateValue() failure on `key`. */
export function validationMessage(key: ConfigKey, code: ValidateCode): string {
  const spec = CONFIG_SPEC[key]
  const v = STORAGE_COPY.editor.validation
  switch (code) {
    case 'range':
      if (spec.zeroOr) return fill(v.rangeZeroOr, { min: spec.zeroOr.min, max: spec.zeroOr.max })
      return fill(v.range, { min: spec.min ?? 0, max: spec.max ?? 0 })
    case 'not-allowed':
      return fill(v['not-allowed'], {
        allowed: spec.kind === 'bool' ? '0, 1' : (spec.allowed ?? []).join(', '),
      })
    case 'too-long':
      return fill(v['too-long'], { max: spec.maxBytes ?? 0 })
    case 'format':
      if (spec.kind === 'ip') return v.formatIp
      if (spec.kind === 'qdbm') return v.formatQdbm
      if (spec.kind === 'str') return v.format
      return v.formatInt
    default:
      return v[code]
  }
}

/** The reason text of a failed transfer item. */
export function transferFailureText(code: TransferErrorCode): string {
  return fill(STORAGE_COPY.transfer.status.failed, { reason: STORAGE_COPY.transfer.failed[code] })
}
