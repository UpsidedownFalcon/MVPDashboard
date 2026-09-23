// CONFIG.TXT editor (PLAN_msd_management 4.2, decisions E, F, G). Basic
// fields on top; the UDP target row; Advanced as a collapsible section whose
// inputs stay disabled until "I understand" is ticked. Every value shown is
// the draft over what the FIRMWARE would read from the file (effectiveDraft);
// the file's own oddities (BOM, duplicates, octal, over-long lines) appear as
// notices so the user sees what the sleeve actually uses. Presentational:
// every change goes through the callbacks into the page reducer.
import { AlertTriangle, ChevronDown, ChevronUp, Crosshair, Eye, EyeOff } from 'lucide-react'
import { useId, useState, type ReactNode } from 'react'
import type { UdpTarget } from '../../lib/api'
import { ACCEL_FS_ALLOWED_G, GYRO_FS_ALLOWED_DPS } from '../../lib/config'
import {
  CONFIG_SPEC,
  firmwareReadsInt,
  QDBM_PER_DBM,
  type ConfigKey,
  type ValidateCode,
} from '../../lib/storage/configSchema'
import { fill, STORAGE_COPY, validationMessage } from '../../lib/storage/copy'
import {
  FIRMWARE_DEFAULT_SOURCE_ID,
  octalKeys,
  type ConfigState,
  type Identity,
  type UdpStatus,
} from '../../lib/storage/pageState'
import EjectNotice from './EjectNotice'
import UdpTargetRow from './UdpTargetRow'

interface Props {
  config: ConfigState
  values: Record<ConfigKey, string>
  problems: Partial<Record<ConfigKey, ValidateCode>>
  /** Who the sleeve reports as NOW (the file on the card). */
  identity: Identity
  soldierName: string | null
  /** The draft identity belongs to a sleeve the dashboard already knows. */
  duplicateWarning: boolean
  udpStatus: UdpStatus
  udpTarget: UdpTarget | undefined
  canSave: boolean
  /** A save or transfer is running. */
  disabled: boolean
  onEdit: (key: ConfigKey, value: string) => void
  onPointUdp: () => void
  onRepairBom: () => void
  onToggleAdvanced: () => void
  onAck: (ack: boolean) => void
  onSave: () => void
}

const LEG_OPTIONS = [
  { value: '0', label: STORAGE_COPY.editor.legLeft },
  { value: '1', label: STORAGE_COPY.editor.legRight },
] as const

/** wifi_tx_power_dbm number input step, in dBm. */
const TX_POWER_STEP_DBM = 1 / QDBM_PER_DBM

/** "Sleeve {unit} | {name}" rendered with the unit id in bold: the template
 *  stays whole in STORAGE_COPY and is split here at its {unit} slot. */
const [identityPrefix, identitySuffix] = STORAGE_COPY.identity.line.split('{unit}')

function Field({
  id,
  label,
  help,
  warning,
  problem,
  wide,
  children,
}: {
  id: string
  label: string
  help?: string
  warning?: string
  problem?: string
  wide?: boolean
  children: ReactNode
}) {
  return (
    <div className={`storage-field ${wide ? 'storage-field-wide' : ''}`}>
      <label htmlFor={id} className="storage-label">
        {label}
      </label>
      {children}
      {help && (
        <p id={`${id}-help`} className="storage-help">
          {help}
        </p>
      )}
      {warning && <p className="storage-warn">{warning}</p>}
      {problem && (
        <p className="storage-problem" role="alert">
          {problem}
        </p>
      )}
    </div>
  )
}

export default function ConfigEditor(props: Props) {
  const { config, values, problems, identity, soldierName, udpStatus, udpTarget, disabled } = props
  const c = STORAGE_COPY.editor
  const base = useId()
  const [showPassword, setShowPassword] = useState(false)
  const view = config.view
  if (!view) return null

  const id = (key: ConfigKey) => `${base}-${key}`
  const problemText = (key: ConfigKey) => {
    const code = problems[key]
    return code ? validationMessage(key, code) : undefined
  }
  const inputProps = (key: ConfigKey) => ({
    id: id(key),
    value: values[key],
    'aria-invalid': problems[key] ? true : undefined,
    'aria-describedby': c.fields[key as keyof typeof c.fields]?.help ? `${id(key)}-help` : undefined,
    onChange: (e: { target: { value: string } }) => props.onEdit(key, e.target.value),
  })
  const numberProps = (key: ConfigKey) => {
    const spec = CONFIG_SPEC[key]
    return { type: 'number' as const, inputMode: 'numeric' as const, min: spec.min, max: spec.max, step: 1 }
  }
  const boolChecked = (key: ConfigKey) => firmwareReadsInt(values[key]) === 1
  const legValue = values.source_id === '' ? String(FIRMWARE_DEFAULT_SOURCE_ID) : values.source_id
  const canPoint = udpTarget !== undefined && udpTarget.ip !== null

  const notices: ReactNode[] = []
  if (view.bom) {
    notices.push(
      <div className="storage-notice" key="bom">
        <AlertTriangle aria-hidden />
        <span>{c.notices.bom}</span>
        {config.repairBom ? (
          <span>{c.notices.repairQueued}</span>
        ) : (
          <button type="button" className="segment rig-action" disabled={disabled} onClick={props.onRepairBom}>
            {c.notices.repair}
          </button>
        )}
      </div>,
    )
  }
  for (const key of view.duplicates) {
    notices.push(
      <div className="storage-notice" key={`dup-${key}`}>
        <AlertTriangle aria-hidden />
        <span>{fill(c.notices.duplicate, { key })}</span>
      </div>,
    )
  }
  for (const o of octalKeys(view)) {
    notices.push(
      <div className="storage-notice" key={`octal-${o.key}`}>
        <AlertTriangle aria-hidden />
        <span>
          {o.reads === null
            ? fill(c.notices.octalUnreadable, { key: o.key, text: o.text })
            : fill(c.notices.octal, { key: o.key, n: o.reads })}
        </span>
      </div>,
    )
  }
  for (const line of view.overlongLines) {
    notices.push(
      <div className="storage-notice" key={`long-${line}`}>
        <AlertTriangle aria-hidden />
        <span>{fill(c.notices.overlong, { n: line + 1 })}</span>
      </div>,
    )
  }

  const segmented = (key: ConfigKey, label: string, options: readonly { value: string; label: string }[], current: string) => (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          className={`segment ${current === o.value ? 'is-active' : ''}`}
          aria-pressed={current === o.value}
          disabled={disabled}
          onClick={() => props.onEdit(key, o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )

  const advancedOpen = config.advancedOpen
  const advancedBodyId = `${base}-advanced`
  const accelValue = firmwareReadsInt(values.accel_fs_g)
  const gyroValue = firmwareReadsInt(values.gyro_fs_dps)

  return (
    <section className="card storage-card" aria-label={c.basicSection}>
      <h2 className="storage-h2">{c.basicSection}</h2>
      <p className="storage-identity">
        {identityPrefix}
        <b>{identity.unitId}</b>
        {fill(identitySuffix, { name: soldierName ?? STORAGE_COPY.identity.unknown })}
      </p>
      {notices}

      <fieldset className="storage-fields" disabled={disabled}>
        <Field id={id('wifi_ssid')} label={c.fields.wifi_ssid.label} help={c.fields.wifi_ssid.help} problem={problemText('wifi_ssid')}>
          <input type="text" autoComplete="off" spellCheck={false} {...inputProps('wifi_ssid')} />
        </Field>

        <Field
          id={id('wifi_password')}
          label={c.fields.wifi_password.label}
          help={c.fields.wifi_password.help}
          problem={problemText('wifi_password')}
        >
          <div className="storage-password">
            <input
              type={showPassword ? 'text' : 'password'}
              autoComplete="off"
              spellCheck={false}
              {...inputProps('wifi_password')}
            />
            <button
              type="button"
              className="segment rig-action"
              aria-pressed={showPassword}
              onClick={() => setShowPassword((v) => !v)}
            >
              {showPassword ? <EyeOff size={13} aria-hidden /> : <Eye size={13} aria-hidden />}
              {showPassword ? c.password.hide : c.password.show}
            </button>
          </div>
        </Field>

        <Field
          id={id('device_id')}
          label={c.fields.device_id.label}
          help={c.fields.device_id.help}
          warning={props.duplicateWarning ? c.duplicateWarning : undefined}
          problem={problemText('device_id')}
        >
          <input {...numberProps('device_id')} {...inputProps('device_id')} />
        </Field>

        <Field id={id('source_id')} label={c.fields.source_id.label} problem={problemText('source_id')}>
          {segmented('source_id', c.fields.source_id.label, LEG_OPTIONS, legValue)}
        </Field>

        <label className="storage-check storage-field-wide">
          <input
            type="checkbox"
            checked={boolChecked('diag_log_enabled')}
            onChange={(e) => props.onEdit('diag_log_enabled', e.target.checked ? '1' : '0')}
          />
          {c.fields.diag_log_enabled.label}
        </label>
        <label className="storage-check storage-field-wide">
          <input
            type="checkbox"
            checked={boolChecked('stream_enabled')}
            onChange={(e) => props.onEdit('stream_enabled', e.target.checked ? '1' : '0')}
          />
          {c.fields.stream_enabled.label}
        </label>
      </fieldset>

      <UdpTargetRow
        ip={values.udp_ip}
        port={values.udp_port}
        status={udpStatus}
        canPoint={canPoint}
        disabled={disabled}
        onPoint={props.onPointUdp}
      />

      <div className="storage-advanced">
        <button
          type="button"
          className="storage-advanced-toggle"
          aria-expanded={advancedOpen}
          aria-controls={advancedBodyId}
          onClick={props.onToggleAdvanced}
        >
          {advancedOpen ? <ChevronUp size={15} aria-hidden /> : <ChevronDown size={15} aria-hidden />}
          {c.advanced.summary}
        </button>
        {advancedOpen && (
          <div id={advancedBodyId} className="storage-advanced-body">
            <p className="storage-warning">
              <AlertTriangle aria-hidden />
              {c.advanced.warning}
            </p>
            <label className="storage-check">
              <input
                type="checkbox"
                checked={config.advancedAck}
                disabled={disabled}
                onChange={(e) => props.onAck(e.target.checked)}
              />
              {c.advanced.ack}
            </label>
            <fieldset className="storage-fields storage-fieldset" disabled={disabled || !config.advancedAck}>
              <Field id={id('udp_ip')} label={c.advanced.labels.udp_ip} problem={problemText('udp_ip')}>
                <input type="text" inputMode="decimal" autoComplete="off" spellCheck={false} {...inputProps('udp_ip')} />
              </Field>
              <Field id={id('udp_port')} label={c.advanced.labels.udp_port} problem={problemText('udp_port')}>
                <input {...numberProps('udp_port')} {...inputProps('udp_port')} />
              </Field>
              <div className="storage-field-wide storage-actions">
                <button
                  type="button"
                  className="segment rig-action"
                  disabled={!canPoint || udpStatus === 'this-dashboard'}
                  title={canPoint ? undefined : c.udp.pointDisabled}
                  onClick={props.onPointUdp}
                >
                  <Crosshair size={13} aria-hidden />
                  {c.udp.point}
                </button>
              </div>

              <Field id={id('low_batt_mv')} label={c.advanced.labels.low_batt_mv} problem={problemText('low_batt_mv')}>
                <input {...numberProps('low_batt_mv')} {...inputProps('low_batt_mv')} />
              </Field>
              <Field
                id={id('wifi_tx_power_dbm')}
                label={c.advanced.labels.wifi_tx_power_dbm}
                problem={problemText('wifi_tx_power_dbm')}
              >
                <input
                  type="number"
                  inputMode="decimal"
                  min={CONFIG_SPEC.wifi_tx_power_dbm.min}
                  max={CONFIG_SPEC.wifi_tx_power_dbm.max}
                  step={TX_POWER_STEP_DBM}
                  {...inputProps('wifi_tx_power_dbm')}
                />
              </Field>

              <Field id={id('accel_fs_g')} label={c.advanced.labels.accel_fs_g} problem={problemText('accel_fs_g')}>
                {segmented(
                  'accel_fs_g',
                  c.advanced.labels.accel_fs_g,
                  ACCEL_FS_ALLOWED_G.map((g) => ({ value: String(g), label: fill(c.advanced.unitG, { n: g }) })),
                  accelValue === null ? '' : String(accelValue),
                )}
              </Field>
              <Field id={id('gyro_fs_dps')} label={c.advanced.labels.gyro_fs_dps} problem={problemText('gyro_fs_dps')}>
                {segmented(
                  'gyro_fs_dps',
                  c.advanced.labels.gyro_fs_dps,
                  GYRO_FS_ALLOWED_DPS.map((d) => ({ value: String(d), label: fill(c.advanced.unitDps, { n: d }) })),
                  gyroValue === null ? '' : String(gyroValue),
                )}
              </Field>

              <Field
                id={id('batt_cal_true_mv')}
                label={c.advanced.labels.batt_cal_true_mv}
                problem={problemText('batt_cal_true_mv')}
              >
                <input {...numberProps('batt_cal_true_mv')} {...inputProps('batt_cal_true_mv')} />
              </Field>
              <Field
                id={id('batt_cal_raw_mv')}
                label={c.advanced.labels.batt_cal_raw_mv}
                problem={problemText('batt_cal_raw_mv')}
              >
                <input {...numberProps('batt_cal_raw_mv')} {...inputProps('batt_cal_raw_mv')} />
              </Field>
            </fieldset>
          </div>
        )}
      </div>

      <div className="storage-actions">
        <button
          type="button"
          className="segment rig-action storage-primary"
          disabled={!props.canSave || disabled}
          onClick={props.onSave}
        >
          {config.saving ? c.saving : c.save}
        </button>
      </div>
      {config.error && (
        <p className="rig-error notice" role="alert">
          {config.error.code === 'write'
            ? fill(c.errors.write, { detail: config.error.detail ?? '' })
            : c.errors.readback}
        </p>
      )}
      {config.saved && <EjectNotice saved={config.saved} />}
    </section>
  )
}
