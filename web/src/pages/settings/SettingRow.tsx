import { useEffect, useId } from 'react'
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

interface RowFlags {
  displayValue: string
  isEnvLocked: boolean
  isReadOnlyPostInit: boolean
  isDisabled: boolean
  isSecuritySensitive: boolean
}

function computeRowFlags(
  entry: SettingEntry,
  dirtyValue: string | undefined,
  saving: boolean,
  controllerDisabled: boolean | undefined,
): RowFlags {
  const isEnvLocked = entry.source === 'env'
  const isReadOnlyPostInit = entry.definition.read_only_post_init
  const compositeKey = `${entry.definition.namespace}/${entry.definition.key}`
  return {
    displayValue: dirtyValue ?? entry.value,
    isEnvLocked,
    isReadOnlyPostInit,
    isDisabled: isEnvLocked || saving || controllerDisabled === true || isReadOnlyPostInit,
    isSecuritySensitive: SECURITY_SENSITIVE_SETTINGS.has(compositeKey),
  }
}

interface NoticeIds {
  env: string
  readonly: string
  security: string
}

function buildDescribedBy(flags: RowFlags, ids: NoticeIds): string {
  return [
    flags.isEnvLocked ? ids.env : null,
    flags.isReadOnlyPostInit && !flags.isEnvLocked ? ids.readonly : null,
    flags.isSecuritySensitive ? ids.security : null,
  ]
    .filter((id): id is string => id !== null)
    .join(' ')
}

function SettingRowNotices({ flags, ids }: { flags: RowFlags; ids: NoticeIds }) {
  return (
    <>
      {flags.isEnvLocked && (
        <p id={ids.env} className="text-micro text-warning">
          Value set by environment variable (read-only)
        </p>
      )}
      {flags.isReadOnlyPostInit && !flags.isEnvLocked && (
        <p id={ids.readonly} className="text-micro text-warning">
          Read-only after startup. Configure via environment variable or YAML before launch.
        </p>
      )}
      {flags.isSecuritySensitive && (
        <p id={ids.security} className="text-micro text-danger">
          Security-sensitive setting: misconfiguration may expose the system
        </p>
      )}
    </>
  )
}

function SettingRowLabel({
  entry,
  highlightQuery,
}: {
  entry: SettingEntry
  highlightQuery: string | undefined
}) {
  const { definition, source } = entry
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-foreground">
          {highlightText(formatKey(definition.key, definition.namespace), highlightQuery)}
        </span>
        <SourceBadge source={source} />
        {definition.restart_required && <RestartBadge />}
      </div>
      <p className="text-xs text-text-secondary">
        {highlightText(definition.description, highlightQuery)}
      </p>
    </>
  )
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
  const { definition } = entry
  const compositeKey = `${definition.namespace}/${definition.key}`
  const { flashStyle, triggerFlash } = useFlash()
  const noticeRootId = useId()

  useEffect(() => {
    if (flash) triggerFlash()
  }, [flash, triggerFlash])

  const flags = computeRowFlags(entry, dirtyValue, saving, controllerDisabled)
  // Stable per-row IDs so the warning paragraphs can be referenced by the
  // field's accessible description (screen readers announce the
  // explanation alongside the disabled control).
  const ids: NoticeIds = {
    env: `${noticeRootId}-env`,
    readonly: `${noticeRootId}-readonly`,
    security: `${noticeRootId}-security`,
  }
  const describedByIds = buildDescribedBy(flags, ids)
  const fieldLabel = formatKey(definition.key, definition.namespace)

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
      <div className="min-w-0 space-y-1">
        <SettingRowLabel entry={entry} highlightQuery={highlightQuery} />
        <SettingRowNotices flags={flags} ids={ids} />
      </div>

      {/* Field wrapper carries aria-describedby so the warning paragraphs
          are announced together with the control, even when disabled. */}
      <div
        className="w-56 shrink-0"
        role="group"
        aria-label={fieldLabel}
        aria-describedby={describedByIds || undefined}
      >
        <SettingField
          definition={definition}
          value={flags.displayValue}
          onChange={onChange}
          disabled={flags.isDisabled}
        />
      </div>
    </div>
  )
}
