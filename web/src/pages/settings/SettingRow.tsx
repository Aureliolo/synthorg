import { useEffect } from 'react'
import { cn } from '@/lib/utils'
import type { SettingEntry } from '@/api/types/settings'
import { useFlash } from '@/hooks/useFlash'
import { SECURITY_SENSITIVE_SETTINGS } from '@/utils/constants'
import { SourceBadge } from './SourceBadge'
import { RestartBadge } from './RestartBadge'
import { SettingField } from './SettingField'

export interface SettingRowProps {
  entry: SettingEntry
  dirtyValue: string | undefined
  onChange: (value: string) => void
  saving: boolean
  /** Whether the controller setting for this dependent is disabled. */
  controllerDisabled?: boolean
  /** Trigger a flash animation (e.g. on WebSocket update). */
  flash?: boolean
  /** Search query to highlight matching text. */
  highlightQuery?: string
}

/** Highlight matching substrings in text with accent background. */
function highlightText(text: string, query: string | undefined): React.ReactNode {
  if (!query || !query.trim()) return text
  const q = query.trim().toLowerCase()
  const idx = text.toLowerCase().indexOf(q)
  if (idx === -1) return text
  return (
    <>
      {text.slice(0, idx)}
      <mark className="rounded-sm bg-accent/20 text-accent">{text.slice(idx, idx + q.length)}</mark>
      {text.slice(idx + q.length)}
    </>
  )
}

/** Generic keys that need the namespace prefix for clarity. */
const GENERIC_KEYS: ReadonlySet<string> = new Set(['enabled', 'backend', 'path', 'description'])

function formatKey(key: string, namespace?: string): string {
  const formatted = key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
  if (namespace && GENERIC_KEYS.has(key)) {
    const ns = namespace.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
    return `${ns} ${formatted}`
  }
  return formatted
}

export function SettingRow({
  entry,
  dirtyValue,
  onChange,
  saving,
  controllerDisabled,
  flash,
  highlightQuery,
}: SettingRowProps) {
  const { definition, source } = entry
  const compositeKey = `${definition.namespace}/${definition.key}`
  const { flashStyle, triggerFlash } = useFlash()

  useEffect(() => {
    if (flash) triggerFlash()
  }, [flash, triggerFlash])
  const displayValue = dirtyValue ?? entry.value
  const isEnvLocked = source === 'env'
  const isReadOnlyPostInit = definition.read_only_post_init
  const isDisabled =
    isEnvLocked || saving || controllerDisabled === true || isReadOnlyPostInit
  const isSecuritySensitive = SECURITY_SENSITIVE_SETTINGS.has(compositeKey)

  return (
    <div
      data-setting-key={compositeKey}
      className={cn(
        'grid grid-cols-[1fr_auto] items-start gap-grid-gap rounded-md p-card max-[639px]:grid-cols-1',
        'transition-all duration-200 hover:bg-card-hover hover:-translate-y-px',
        controllerDisabled && 'opacity-50 cursor-not-allowed',
      )}
      style={flashStyle}
      title={controllerDisabled ? 'Enable the parent setting to configure this option' : undefined}
    >
      {/* Left: label, description, badges */}
      <div className="min-w-0 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-foreground">
            {highlightText(formatKey(definition.key, definition.namespace), highlightQuery)}
          </span>
          <SourceBadge source={source} />
          {definition.restart_required && <RestartBadge />}
        </div>
        <p className="text-xs text-text-secondary">{highlightText(definition.description, highlightQuery)}</p>
        {isEnvLocked && (
          <p className="text-[10px] text-warning">Value set by environment variable (read-only)</p>
        )}
        {isReadOnlyPostInit && !isEnvLocked && (
          <p className="text-[10px] text-warning">
            Read-only after startup. Configure via environment variable or YAML before launch.
          </p>
        )}
        {isSecuritySensitive && (
          <p className="text-[10px] text-danger">
            Security-sensitive setting -- misconfiguration may expose the system
          </p>
        )}
      </div>

      {/* Right: field control */}
      <div className="w-56 shrink-0">
        <SettingField
          definition={definition}
          value={displayValue}
          onChange={onChange}
          disabled={isDisabled}
        />
      </div>
    </div>
  )
}
