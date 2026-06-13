import { useCallback, useMemo, useState } from 'react'
import type { LogLevel, SinkInfo, TestSinkResult } from '@/api/types/settings'
import { Button } from '@/components/ui/button'
import { Drawer } from '@/components/ui/drawer'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import { TagInput } from '@/components/ui/tag-input'
import { ToggleField } from '@/components/ui/toggle-field'

const LOG_LEVELS = [
  { value: 'DEBUG', label: 'Debug' },
  { value: 'INFO', label: 'Info' },
  { value: 'WARNING', label: 'Warning' },
  { value: 'ERROR', label: 'Error' },
  { value: 'CRITICAL', label: 'Critical' },
]

const ROTATION_STRATEGIES = [
  { value: 'builtin', label: 'Built-in' },
  { value: 'external', label: 'External' },
  { value: 'none', label: 'None' },
]

type RotationStrategy = 'builtin' | 'external' | 'none'

const LOG_LEVEL_VALUES: readonly LogLevel[] = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
const ROTATION_STRATEGY_VALUES: readonly RotationStrategy[] = ['builtin', 'external', 'none']
const DEFAULT_MAX_BYTES = 10_485_760
const DEFAULT_BACKUP_COUNT = 5

function toLogLevel(value: string | null | undefined): LogLevel {
  return LOG_LEVEL_VALUES.includes(value as LogLevel) ? (value as LogLevel) : 'INFO'
}

function toRotationStrategy(value: string | null | undefined): RotationStrategy {
  return ROTATION_STRATEGY_VALUES.includes(value as RotationStrategy)
    ? (value as RotationStrategy)
    : 'none'
}

interface SinkFormValues {
  filePath: string
  level: LogLevel
  enabled: boolean
  jsonFormat: boolean
  rotationStrategy: RotationStrategy
  maxBytes: string
  backupCount: string
  routingPrefixes: string[]
  isConsole: boolean
  isDefault: boolean
  sink: SinkInfo | null
  isNew?: boolean | undefined
}

type SinkPayload = { sink_overrides: string; custom_sinks: string }

function buildOverridePayload(v: SinkFormValues): SinkPayload {
  const override: Record<string, unknown> = { level: v.level, json_format: v.jsonFormat, enabled: v.enabled }
  // Reuse the save-path normalisation so "Test Config" validates the exact
  // rotation payload "Save" would persist (preserves backup_count 0, never
  // emits non-finite numbers).
  const rotation = buildSaveRotation(v)
  if (rotation !== null) override['rotation'] = rotation
  return { sink_overrides: JSON.stringify({ [v.sink!.identifier]: override }), custom_sinks: '[]' }
}

function buildCustomPayload(v: SinkFormValues): SinkPayload | null {
  const path = v.filePath.trim()
  if (!path) return null
  const customSink: Record<string, unknown> = {
    file_path: path,
    level: v.level,
    json_format: v.jsonFormat,
    enabled: v.enabled,
  }
  const rotation = buildSaveRotation(v)
  if (rotation !== null) customSink['rotation'] = rotation
  if (v.routingPrefixes.length > 0) customSink['routing_prefixes'] = v.routingPrefixes
  return { sink_overrides: '{}', custom_sinks: JSON.stringify([customSink]) }
}

function buildSinkPayload(v: SinkFormValues): SinkPayload | null {
  return v.isDefault ? buildOverridePayload(v) : buildCustomPayload(v)
}

function buildSaveRotation(v: SinkFormValues): SinkInfo['rotation'] {
  if (v.rotationStrategy === 'none' || v.isConsole) return null
  const mb = Number(v.maxBytes)
  const bc = Number(v.backupCount)
  return {
    strategy: v.rotationStrategy,
    max_bytes: Number.isFinite(mb) && mb > 0 ? mb : DEFAULT_MAX_BYTES,
    backup_count: Number.isFinite(bc) && bc >= 0 ? bc : DEFAULT_BACKUP_COUNT,
  }
}

function buildSinkInfo(v: SinkFormValues): SinkInfo {
  const identifier = v.isConsole ? '__console__' : v.isNew ? v.filePath.trim() : v.sink!.identifier
  return {
    identifier,
    sink_type: v.isConsole ? 'console' : 'file',
    level: v.level,
    json_format: v.jsonFormat,
    rotation: buildSaveRotation(v),
    is_default: v.isDefault,
    enabled: v.enabled,
    routing_prefixes: [...v.routingPrefixes],
  }
}

export interface SinkFormDrawerProps {
  open: boolean
  onClose: () => void
  sink: SinkInfo | null
  isNew?: boolean
  onTest: (data: { sink_overrides: string; custom_sinks: string }) => Promise<TestSinkResult | null>
  onSave: (sink: SinkInfo) => void
}

interface SinkForm {
  values: SinkFormValues
  setFilePath: (value: string) => void
  setLevel: (value: LogLevel) => void
  setEnabled: (value: boolean) => void
  setJsonFormat: (value: boolean) => void
  setRotationStrategy: (value: RotationStrategy) => void
  setMaxBytes: (value: string) => void
  setBackupCount: (value: string) => void
  setRoutingPrefixes: (value: string[]) => void
  filePathError: string | null
  clearFilePathError: () => void
  testResult: TestSinkResult | null
  testing: boolean
  handleTest: () => Promise<void>
  handleSave: () => void
}

interface SinkTestState {
  testResult: TestSinkResult | null
  testing: boolean
  handleTest: () => Promise<void>
}

/** Test-config state for the sink form (banner result + in-flight flag). */
function useSinkTest(
  values: SinkFormValues,
  onTest: SinkFormDrawerProps['onTest'],
  setFilePathError: (value: string | null) => void,
): SinkTestState {
  const [testResult, setTestResult] = useState<TestSinkResult | null>(null)
  const [testing, setTesting] = useState(false)

  const handleTest = useCallback(async () => {
    const payload = buildSinkPayload(values)
    if (!payload) {
      setFilePathError('File path is required')
      return
    }
    setFilePathError(null)
    setTesting(true)
    setTestResult(null)
    try {
      // onTest uses the sentinel contract -- null means the store already
      // logged + toasted, so leave testResult cleared (no stale banner).
      setTestResult(await onTest(payload))
    } finally {
      setTesting(false)
    }
  }, [values, onTest, setFilePathError])

  return { testResult, testing, handleTest }
}

function useSinkForm(props: SinkFormDrawerProps): SinkForm {
  const { sink, isNew, onTest, onSave, onClose } = props
  // State seeded lazily from the sink prop (the parent remounts via
  // key={sink?.identifier}); lazy initializers keep the prop's optional
  // chains out of this hook's complexity budget.
  const [filePath, setFilePath] = useState(() =>
    sink?.identifier === '__console__' ? '' : (sink?.identifier ?? ''),
  )
  const [level, setLevel] = useState<LogLevel>(() => toLogLevel(sink?.level))
  const [enabled, setEnabled] = useState(() => sink?.enabled ?? true)
  const [jsonFormat, setJsonFormat] = useState(() => sink?.json_format ?? false)
  const [rotationStrategy, setRotationStrategy] = useState<RotationStrategy>(() =>
    toRotationStrategy(sink?.rotation?.strategy),
  )
  const [maxBytes, setMaxBytes] = useState(() => String(sink?.rotation?.max_bytes ?? DEFAULT_MAX_BYTES))
  const [backupCount, setBackupCount] = useState(() =>
    String(sink?.rotation?.backup_count ?? DEFAULT_BACKUP_COUNT),
  )
  const [routingPrefixes, setRoutingPrefixes] = useState<string[]>(() =>
    sink?.routing_prefixes ? [...sink.routing_prefixes] : [],
  )
  const [filePathError, setFilePathError] = useState<string | null>(null)

  const values: SinkFormValues = useMemo(
    () => ({
      filePath,
      level,
      enabled,
      jsonFormat,
      rotationStrategy,
      maxBytes,
      backupCount,
      routingPrefixes,
      isConsole: sink?.sink_type === 'console',
      isDefault: sink?.is_default === true,
      sink,
      isNew,
    }),
    [filePath, level, enabled, jsonFormat, rotationStrategy, maxBytes, backupCount, routingPrefixes, sink, isNew],
  )

  const { testResult, testing, handleTest } = useSinkTest(values, onTest, setFilePathError)

  const handleSave = useCallback(() => {
    if (!values.isDefault && !filePath.trim()) {
      setFilePathError('File path is required')
      return
    }
    onSave(buildSinkInfo(values))
    onClose()
  }, [values, filePath, onSave, onClose])

  return {
    values,
    setFilePath,
    setLevel,
    setEnabled,
    setJsonFormat,
    setRotationStrategy,
    setMaxBytes,
    setBackupCount,
    setRoutingPrefixes,
    filePathError,
    clearFilePathError: () => setFilePathError(null),
    testResult,
    testing,
    handleTest,
    handleSave,
  }
}

function SinkFormFields({ form }: { form: SinkForm }) {
  const { values } = form
  return (
    <>
      {!values.isDefault && (
        <InputField
          label="File path"
          value={values.filePath}
          onChange={(e) => {
            form.setFilePath(e.target.value)
            form.clearFilePathError()
          }}
          disabled={!values.isNew}
          hint={values.isNew ? 'Plain filename (e.g. custom.log)' : 'Cannot change path of existing sink'}
          error={form.filePathError}
        />
      )}

      <div className="flex items-center gap-4">
        <div className="flex-1">
          <SelectField
            label="Level"
            options={LOG_LEVELS}
            value={values.level}
            onChange={(v) => form.setLevel(v as LogLevel)}
          />
        </div>
        <ToggleField label="Enabled" checked={values.enabled} onChange={form.setEnabled} />
      </div>

      <ToggleField label="JSON format" checked={values.jsonFormat} onChange={form.setJsonFormat} />

      {!values.isConsole && (
        <>
          <SelectField
            label="Rotation strategy"
            options={ROTATION_STRATEGIES}
            value={values.rotationStrategy}
            onChange={(v) => form.setRotationStrategy(toRotationStrategy(v))}
          />
          {values.rotationStrategy !== 'none' && (
            <div className="grid grid-cols-2 gap-3">
              <InputField
                label="Max size (bytes)"
                type="number"
                value={values.maxBytes}
                onChange={(e) => form.setMaxBytes(e.target.value)}
                hint={`${(Number(values.maxBytes) / 1024 / 1024).toFixed(1)} MB`}
              />
              <InputField
                label="Backup count"
                type="number"
                value={values.backupCount}
                onChange={(e) => form.setBackupCount(e.target.value)}
              />
            </div>
          )}
        </>
      )}

      {!values.isDefault && (
        <div className="space-y-1">
          <span className="text-xs font-medium text-text-secondary">Routing prefixes</span>
          <TagInput
            value={values.routingPrefixes}
            onChange={form.setRoutingPrefixes}
            placeholder="Add logger prefix..."
          />
        </div>
      )}
    </>
  )
}

function SinkFormActions({ form, onClose }: { form: SinkForm; onClose: () => void }) {
  return (
    <>
      <div className="flex items-center gap-2 pt-2">
        <Button variant="ghost" size="sm" onClick={form.handleTest} disabled={form.testing}>
          {form.testing ? 'Testing...' : 'Test Config'}
        </Button>
        {form.testResult && (
          <span className={`text-xs ${form.testResult.valid ? 'text-success' : 'text-danger'}`}>
            {form.testResult.valid ? 'Valid' : form.testResult.error}
          </span>
        )}
      </div>

      <div className="flex justify-end gap-2 border-t border-border pt-4">
        <Button variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button size="sm" onClick={form.handleSave}>
          Save
        </Button>
      </div>
    </>
  )
}

export function SinkFormDrawer(props: SinkFormDrawerProps) {
  const { open, onClose, sink, isNew } = props
  const form = useSinkForm(props)

  return (
    <Drawer open={open} onClose={onClose} title={isNew ? 'Add Custom Sink' : `Edit: ${sink?.identifier ?? 'Sink'}`}>
      <div className="space-y-[var(--spacing-section-gap)] p-card">
        <SinkFormFields form={form} />
        <SinkFormActions form={form} onClose={onClose} />
      </div>
    </Drawer>
  )
}
