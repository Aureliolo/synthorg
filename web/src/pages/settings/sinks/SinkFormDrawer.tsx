import { useCallback, useState } from 'react'
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

function toLogLevel(value: string | null | undefined): LogLevel {
  return LOG_LEVEL_VALUES.includes(value as LogLevel) ? (value as LogLevel) : 'INFO'
}

function toRotationStrategy(value: string | null | undefined): RotationStrategy {
  return ROTATION_STRATEGY_VALUES.includes(value as RotationStrategy)
    ? (value as RotationStrategy)
    : 'none'
}

export interface SinkFormDrawerProps {
  open: boolean
  onClose: () => void
  sink: SinkInfo | null
  isNew?: boolean
  onTest: (data: { sink_overrides: string; custom_sinks: string }) => Promise<TestSinkResult | null>
  onSave: (sink: SinkInfo) => void
}

export function SinkFormDrawer({ open, onClose, sink, isNew, onTest, onSave }: SinkFormDrawerProps) {
  // State initialized from sink prop. Parent uses key={sink?.identifier} to remount on sink change.
  const [filePath, setFilePath] = useState(sink?.identifier === '__console__' ? '' : (sink?.identifier ?? ''))
  const [level, setLevel] = useState<LogLevel>(() => toLogLevel(sink?.level))
  const [enabled, setEnabled] = useState(sink?.enabled ?? true)
  const [jsonFormat, setJsonFormat] = useState(sink?.json_format ?? false)
  const [rotationStrategy, setRotationStrategy] = useState<RotationStrategy>(() =>
    toRotationStrategy(sink?.rotation?.strategy),
  )
  const [maxBytes, setMaxBytes] = useState(String(sink?.rotation?.max_bytes ?? 10485760))
  const [backupCount, setBackupCount] = useState(String(sink?.rotation?.backup_count ?? 5))
  const [routingPrefixes, setRoutingPrefixes] = useState<string[]>(sink?.routing_prefixes ? [...sink.routing_prefixes] : [])
  const [testResult, setTestResult] = useState<TestSinkResult | null>(null)
  const [testing, setTesting] = useState(false)
  const [filePathError, setFilePathError] = useState<string | null>(null)

  const isConsole = sink?.sink_type === 'console'
  const isDefault = sink?.is_default === true

  const buildPayload = useCallback(() => {
    if (isDefault) {
      const override: Record<string, unknown> = { level, json_format: jsonFormat, enabled }
      if (!isConsole && rotationStrategy !== 'none') {
        override.rotation = {
          strategy: rotationStrategy,
          max_bytes: Number(maxBytes),
          backup_count: Number(backupCount),
        }
      }
      return {
        sink_overrides: JSON.stringify({ [sink!.identifier]: override }),
        custom_sinks: '[]',
      }
    }
    const path = filePath.trim()
    if (!path) {
      setFilePathError('File path is required')
      return null
    }
    setFilePathError(null)
    const customSink: Record<string, unknown> = {
      file_path: path,
      level,
      json_format: jsonFormat,
      enabled,
    }
    if (!isConsole && rotationStrategy !== 'none') {
      customSink.rotation = {
        strategy: rotationStrategy,
        max_bytes: Number(maxBytes) || 10_485_760,
        backup_count: Number(backupCount) || 5,
      }
    }
    if (routingPrefixes.length > 0) {
      customSink.routing_prefixes = routingPrefixes
    }
    return { sink_overrides: '{}', custom_sinks: JSON.stringify([customSink]) }
  }, [isDefault, isConsole, sink, level, jsonFormat, enabled, rotationStrategy, maxBytes, backupCount, filePath, routingPrefixes])

  const handleTest = useCallback(async () => {
    const payload = buildPayload()
    if (!payload) return
    setTesting(true)
    setTestResult(null)
    try {
      const result = await onTest(payload)
      // onTest uses the sentinel contract -- null means the store
      // already logged + toasted. Leave testResult cleared so the
      // caller sees no stale success banner.
      setTestResult(result)
    } finally {
      setTesting(false)
    }
  }, [buildPayload, onTest])

  const handleSave = useCallback(() => {
    if (!isDefault && !filePath.trim()) {
      setFilePathError('File path is required')
      return
    }
    const identifier = isConsole ? '__console__' : (isNew ? filePath.trim() : sink!.identifier)
    const parsedMaxBytes = Number(maxBytes)
    const parsedBackupCount = Number(backupCount)
    const rotation = rotationStrategy === 'none' || isConsole ? null : {
      strategy: rotationStrategy as 'builtin' | 'external',
      max_bytes: Number.isFinite(parsedMaxBytes) && parsedMaxBytes > 0 ? parsedMaxBytes : 10_485_760,
      backup_count: Number.isFinite(parsedBackupCount) && parsedBackupCount >= 0 ? parsedBackupCount : 5,
    }
    onSave({
      identifier,
      sink_type: isConsole ? 'console' : 'file',
      level,
      json_format: jsonFormat,
      rotation,
      is_default: isDefault,
      enabled,
      routing_prefixes: [...routingPrefixes],
    })
    onClose()
  }, [isDefault, isConsole, isNew, sink, filePath, level, jsonFormat, enabled, rotationStrategy, maxBytes, backupCount, routingPrefixes, onSave, onClose])

  return (
    <Drawer open={open} onClose={onClose} title={isNew ? 'Add Custom Sink' : `Edit: ${sink?.identifier ?? 'Sink'}`}>
      <div className="space-y-[var(--spacing-section-gap)] p-card">
        {!isDefault && (
          <InputField
            label="File path"
            value={filePath}
            onChange={(e) => { setFilePath(e.target.value); setFilePathError(null) }}
            disabled={!isNew}
            hint={isNew ? 'Plain filename (e.g. custom.log)' : 'Cannot change path of existing sink'}
            error={filePathError}
          />
        )}

        <div className="flex items-center gap-4">
          <div className="flex-1">
            <SelectField
              label="Level"
              options={LOG_LEVELS}
              value={level}
              onChange={(v) => setLevel(v as LogLevel)}
            />
          </div>
          <ToggleField
            label="Enabled"
            checked={enabled}
            onChange={setEnabled}
          />
        </div>

        <ToggleField
          label="JSON format"
          checked={jsonFormat}
          onChange={setJsonFormat}
        />

        {!isConsole && (
          <>
            <SelectField
              label="Rotation strategy"
              options={ROTATION_STRATEGIES}
              value={rotationStrategy}
              onChange={(v) => setRotationStrategy(v as RotationStrategy)}
            />
            {rotationStrategy !== 'none' && (
              <div className="grid grid-cols-2 gap-3">
                <InputField
                  label="Max size (bytes)"
                  type="number"
                  value={maxBytes}
                  onChange={(e) => setMaxBytes(e.target.value)}
                  hint={`${(Number(maxBytes) / 1024 / 1024).toFixed(1)} MB`}
                />
                <InputField
                  label="Backup count"
                  type="number"
                  value={backupCount}
                  onChange={(e) => setBackupCount(e.target.value)}
                />
              </div>
            )}
          </>
        )}

        {!isDefault && (
          <div className="space-y-1">
            <span className="text-xs font-medium text-text-secondary">Routing prefixes</span>
            <TagInput
              value={routingPrefixes}
              onChange={setRoutingPrefixes}
              placeholder="Add logger prefix..."
            />
          </div>
        )}

        <div className="flex items-center gap-2 pt-2">
          <Button variant="ghost" size="sm" onClick={handleTest} disabled={testing}>
            {testing ? 'Testing...' : 'Test Config'}
          </Button>
          {testResult && (
            <span className={`text-xs ${testResult.valid ? 'text-success' : 'text-danger'}`}>
              {testResult.valid ? 'Valid' : testResult.error}
            </span>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" onClick={handleSave}>Save</Button>
        </div>
      </div>
    </Drawer>
  )
}
