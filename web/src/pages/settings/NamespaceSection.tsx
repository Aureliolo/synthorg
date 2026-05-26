import { useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { SettingEntry } from '@/api/types/settings'
import { useAnimationPreset } from '@/hooks/useAnimationPreset'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { SettingRow } from './SettingRow'

interface RowRenderProps {
  dirtyValues: ReadonlyMap<string, string>
  onValueChange: (compositeKey: string, value: string) => void
  savingKeys: ReadonlyMap<string, number>
  controllerDisabledMap: ReadonlyMap<string, boolean>
  changedKeys?: ReadonlySet<string>
  highlightQuery?: string
}

function NamespaceSettingRow({ entry, rows }: { entry: SettingEntry; rows: RowRenderProps }) {
  const ck = `${entry.definition.namespace}/${entry.definition.key}`
  return (
    <ErrorBoundary level="component">
      <SettingRow
        entry={entry}
        dirtyValue={rows.dirtyValues.get(ck)}
        onChange={(value) => rows.onValueChange(ck, value)}
        saving={rows.savingKeys.has(ck)}
        controllerDisabled={rows.controllerDisabledMap.get(ck)}
        flash={rows.changedKeys?.has(ck)}
        highlightQuery={rows.highlightQuery}
      />
    </ErrorBoundary>
  )
}

export interface NamespaceSectionProps extends RowRenderProps {
  displayName: string
  icon: React.ReactNode
  entries: SettingEntry[]
  /** Whether the section is forced open (e.g. during search). */
  forceOpen?: boolean
  /** Hide the collapsible header (when tab bar serves as the header). */
  hideHeader?: boolean
  /** Optional footer content rendered at the bottom of the section. */
  footerAction?: React.ReactNode
}

function groupByGroup(entries: SettingEntry[]): Map<string, SettingEntry[]> {
  const groups = new Map<string, SettingEntry[]>()
  for (const entry of entries) {
    const group = entry.definition.group
    const existing = groups.get(group)
    if (existing) existing.push(entry)
    else groups.set(group, [entry])
  }
  return groups
}

interface NamespaceGroupsProps {
  groups: Map<string, SettingEntry[]>
  hideHeader: boolean | undefined
  anim: ReturnType<typeof useAnimationPreset>
  rows: RowRenderProps
}

function entryKey(entry: SettingEntry): string {
  return `${entry.definition.namespace}/${entry.definition.key}`
}

function NamespaceGroups({ groups, hideHeader, anim, rows }: NamespaceGroupsProps) {
  const multiGroup = groups.size > 1
  return (
    <>
      {[...groups.entries()].map(([group, groupEntries]) => (
        <div key={group} className={multiGroup ? 'py-2' : undefined}>
          {multiGroup && (
            <h3 className="mb-2 text-xs font-medium uppercase tracking-wider text-text-muted">{group}</h3>
          )}
          <div className="space-y-1">
            {groupEntries.map((entry, i) =>
              hideHeader ? (
                <div key={entryKey(entry)}>
                  <NamespaceSettingRow entry={entry} rows={rows} />
                </div>
              ) : (
                <motion.div
                  key={entryKey(entry)}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * anim.staggerDelay, ...anim.tween }}
                >
                  <NamespaceSettingRow entry={entry} rows={rows} />
                </motion.div>
              ),
            )}
          </div>
        </div>
      ))}
    </>
  )
}

function NamespaceFooter({ footerAction }: { footerAction?: React.ReactNode }) {
  if (footerAction == null || footerAction === false) return null
  return <div className="pt-1">{footerAction}</div>
}

interface NamespaceHeaderProps {
  displayName: string
  icon: React.ReactNode
  count: number
  isOpen: boolean
  forceOpen: boolean | undefined
  onToggle: () => void
  contentId: string
}

function NamespaceHeader({ displayName, icon, count, isOpen, forceOpen, onToggle, contentId }: NamespaceHeaderProps) {
  return (
    <h2 className="text-sm font-semibold text-foreground">
      <button
        type="button"
        onClick={onToggle}
        disabled={forceOpen}
        className={cn('flex w-full items-center gap-3 p-card', 'text-left transition-colors', !forceOpen && 'hover:bg-card-hover')}
        aria-expanded={isOpen}
        aria-controls={contentId}
      >
        <span className="text-text-secondary">{icon}</span>
        <span>{displayName}</span>
        <span className="ml-1 text-xs text-text-muted">({count})</span>
        <ChevronDown
          className={cn('ml-auto size-4 text-text-muted transition-transform duration-200', isOpen && 'rotate-180')}
          aria-hidden
        />
      </button>
    </h2>
  )
}

export function NamespaceSection(props: NamespaceSectionProps) {
  const { displayName, icon, entries, forceOpen, hideHeader, footerAction } = props
  const [collapsed, setCollapsed] = useState(false)
  const isOpen = Boolean(hideHeader) || Boolean(forceOpen) || !collapsed
  const anim = useAnimationPreset()
  const groups = groupByGroup(entries)
  const contentId = `ns-${displayName.replace(/\s+/g, '-').toLowerCase()}-content`
  const rows: RowRenderProps = props
  const inner = (
    <>
      <NamespaceGroups groups={groups} hideHeader={hideHeader} anim={anim} rows={rows} />
      <NamespaceFooter footerAction={footerAction} />
    </>
  )

  return (
    <section className="rounded-lg border border-border bg-card">
      {!hideHeader && (
        <NamespaceHeader
          displayName={displayName}
          icon={icon}
          count={entries.length}
          isOpen={isOpen}
          forceOpen={forceOpen}
          onToggle={() => setCollapsed((v) => !v)}
          contentId={contentId}
        />
      )}

      {isOpen && hideHeader && (
        <div id={contentId} className="p-card">
          {inner}
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
            <div className="p-card">{inner}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  )
}
