import { useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { SettingEntry } from '@/api/types/settings'
import { useAnimationPreset } from '@/hooks/useAnimationPreset'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { SettingRow } from './SettingRow'

interface NamespaceSettingRowProps {
  entry: SettingEntry
  dirtyValues: ReadonlyMap<string, string>
  onValueChange: (ck: string, v: string) => void
  savingKeys: ReadonlyMap<string, number>
  controllerDisabledMap: ReadonlyMap<string, boolean>
  changedKeys?: ReadonlySet<string>
  highlightQuery?: string
}

function NamespaceSettingRow({
  entry,
  dirtyValues,
  onValueChange,
  savingKeys,
  controllerDisabledMap,
  changedKeys,
  highlightQuery,
}: NamespaceSettingRowProps) {
  const ck = `${entry.definition.namespace}/${entry.definition.key}`
  return (
    <ErrorBoundary level="component">
      <SettingRow
        entry={entry}
        dirtyValue={dirtyValues.get(ck)}
        onChange={(value) => onValueChange(ck, value)}
        saving={savingKeys.has(ck)}
        controllerDisabled={controllerDisabledMap.get(ck)}
        flash={changedKeys?.has(ck)}
        highlightQuery={highlightQuery}
      />
    </ErrorBoundary>
  )
}

export interface NamespaceSectionProps {
  displayName: string
  icon: React.ReactNode
  entries: SettingEntry[]
  dirtyValues: ReadonlyMap<string, string>
  onValueChange: (compositeKey: string, value: string) => void
  savingKeys: ReadonlyMap<string, number>
  /** Map of composite key -> boolean indicating if its controller is disabled. */
  controllerDisabledMap: ReadonlyMap<string, boolean>
  /** Whether the section is forced open (e.g. during search). */
  forceOpen?: boolean
  /** Set of composite keys that changed externally (flash animation). */
  changedKeys?: ReadonlySet<string>
  /** Hide the collapsible header (when tab bar serves as the header). */
  hideHeader?: boolean
  /** Search query to highlight in setting rows. */
  highlightQuery?: string
  /** Optional footer content rendered at the bottom of the section. */
  footerAction?: React.ReactNode
}

function groupByGroup(entries: SettingEntry[]): Map<string, SettingEntry[]> {
  const groups = new Map<string, SettingEntry[]>()
  for (const entry of entries) {
    const group = entry.definition.group
    const existing = groups.get(group)
    if (existing) {
      existing.push(entry)
    } else {
      groups.set(group, [entry])
    }
  }
  return groups
}

export function NamespaceSection({
  displayName,
  icon,
  entries,
  dirtyValues,
  onValueChange,
  savingKeys,
  controllerDisabledMap,
  forceOpen,
  changedKeys,
  hideHeader,
  highlightQuery,
  footerAction,
}: NamespaceSectionProps) {
  const [collapsed, setCollapsed] = useState(false)
  const isOpen = hideHeader || forceOpen || !collapsed
  const anim = useAnimationPreset()
  const groups = groupByGroup(entries)
  const contentId = `ns-${displayName.replace(/\s+/g, '-').toLowerCase()}-content`

  function renderRow(entry: SettingEntry) {
    return (
      <NamespaceSettingRow
        entry={entry}
        dirtyValues={dirtyValues}
        onValueChange={onValueChange}
        savingKeys={savingKeys}
        controllerDisabledMap={controllerDisabledMap}
        changedKeys={changedKeys}
        highlightQuery={highlightQuery}
      />
    )
  }

  function renderGroups() {
    return [...groups.entries()].map(([group, groupEntries]) => (
      <div key={group} className={groups.size > 1 ? 'py-2' : undefined}>
        {groups.size > 1 && (
          <h3 className="mb-2 text-xs font-medium uppercase tracking-wider text-text-muted">
            {group}
          </h3>
        )}
        <div className="space-y-1">
          {hideHeader
            ? groupEntries.map((entry) => (
              <div key={`${entry.definition.namespace}/${entry.definition.key}`}>
                {renderRow(entry)}
              </div>
            ))
            : groupEntries.map((entry, i) => (
              <motion.div
                key={`${entry.definition.namespace}/${entry.definition.key}`}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * anim.staggerDelay, ...anim.tween }}
              >
                {renderRow(entry)}
              </motion.div>
            ))
          }
        </div>
      </div>
    ))
  }

  return (
    <section className="rounded-lg border border-border bg-card">
      {!hideHeader && (
        <h2 className="text-sm font-semibold text-foreground">
          <button
            type="button"
            onClick={() => setCollapsed((v) => !v)}
            disabled={forceOpen}
            className={cn(
              'flex w-full items-center gap-3 p-card',
              'text-left transition-colors',
              !forceOpen && 'hover:bg-card-hover',
            )}
            aria-expanded={isOpen}
            aria-controls={contentId}
          >
            <span className="text-text-secondary">{icon}</span>
            <span>{displayName}</span>
          <span className="ml-1 text-xs text-text-muted">({entries.length})</span>
          <ChevronDown
            className={cn(
              'ml-auto size-4 text-text-muted transition-transform duration-200',
              isOpen && 'rotate-180',
            )}
            aria-hidden
          />
        </button>
        </h2>
      )}

      {isOpen && hideHeader && (
        <div id={contentId} className="p-card">
          {renderGroups()}
          {footerAction != null && footerAction !== false && (
            <div className="pt-1">{footerAction}</div>
          )}
        </div>
      )}

      <AnimatePresence initial={false}>
        {isOpen && !hideHeader && (
          <motion.div
            id={contentId}
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={anim.spring}
            className="overflow-hidden border-t border-border"
          >
            <div className="p-card">
              {renderGroups()}
              {footerAction != null && footerAction !== false && (
                <div className="pt-1">{footerAction}</div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  )
}
