import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { Link } from 'react-router'
import {
  Brain,
  Eye,
  Globe,
  HardDrive,
  Network,
  Settings,
  Shield,
  Wallet,
} from 'lucide-react'
import type { SettingEntry, SettingNamespace } from '@/api/types/settings'
import { createLogger } from '@/lib/logger'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { ToggleField } from '@/components/ui/toggle-field'
import { useSettingsStore } from '@/stores/settings'
import { useAnimationPreset } from '@/hooks/useAnimationPreset'
import { useSettingsData } from '@/hooks/useSettingsData'
import { useSettingsDirtyState } from '@/hooks/useSettingsDirtyState'
import { useSettingsKeyboard } from '@/hooks/useSettingsKeyboard'
import { useUnsavedChangesGuard } from '@/hooks/use-unsaved-changes-guard'
import {
  HIDDEN_SETTINGS,
  NAMESPACE_DISPLAY_NAMES,
  NAMESPACE_ORDER,
  SETTINGS_ADVANCED_KEY,
  SETTINGS_ADVANCED_WARNED_KEY,
} from '@/utils/constants'
import { AdvancedModeBanner } from './settings/AdvancedModeBanner'
import { NotificationsSection } from './settings/NotificationsSection'
import { CodeEditorPanel } from './settings/CodeEditorPanel'
import { FloatingSaveBar } from './settings/FloatingSaveBar'
import { NamespaceSection } from './settings/NamespaceSection'
import { NamespaceTabBar } from './settings/SettingsHealthSection'
import { RestartBanner } from './settings/RestartBanner'
import { SearchInput } from './settings/SearchInput'
import { SettingsSkeleton } from './settings/SettingsSkeleton'
import { buildControllerDisabledMap, matchesSetting, saveSettingsBatch } from './settings/utils'

import { useToastStore } from '@/stores/toast'
import { ROUTES } from '@/router/routes'

const log = createLogger('settings')

type ViewMode = 'gui' | 'code'

function SettingsActionCard({ to, title, description }: { to: string; title: string; description: string }) {
  return (
    <Link
      to={to}
      className="grid grid-cols-[1fr_auto] items-start gap-grid-gap rounded-md p-card transition-all duration-[var(--so-transition-dim)] hover:bg-card-hover hover:-translate-y-px"
    >
      <div className="min-w-0 space-y-1">
        <span className="text-sm font-medium text-foreground">{title}</span>
        <p className="text-xs text-text-secondary">{description}</p>
      </div>
      <div className="w-full max-w-[14rem] shrink-0 md:w-56">
        <span
          className="inline-flex h-9 w-full items-center justify-center rounded-md border border-border bg-card px-4 text-sm font-medium text-foreground"
          aria-hidden
        >
          Open
        </span>
      </div>
    </Link>
  )
}

const NAMESPACE_ICONS: Partial<Record<SettingNamespace, React.ReactNode>> = {
  api: <Globe className="size-4" />,
  memory: <Brain className="size-4" />,
  budget: <Wallet className="size-4" />,
  security: <Shield className="size-4" />,
  coordination: <Network className="size-4" />,
  observability: <Eye className="size-4" />,
  backup: <HardDrive className="size-4" />,
}

export default function SettingsPage() {
  const {
    entries,
    loading,
    error,
    saving,
    saveError,
    wsConnected,
    wsSetupError,
    updateSetting,
  } = useSettingsData()

  const storeSavingKeys = useSettingsStore((s) => s.savingKeys)
  const anim = useAnimationPreset()

  const [searchQuery, setSearchQuery] = useState('')
  const [advancedMode, setAdvancedMode] = useState(
    () => localStorage.getItem(SETTINGS_ADVANCED_KEY) === 'true',
  )
  const [viewMode, setViewMode] = useState<ViewMode>('gui')
  const [showAdvancedWarning, setShowAdvancedWarning] = useState(false)
  const [codeDirty, setCodeDirty] = useState(false)
  const [showCodeDiscardWarning, setShowCodeDiscardWarning] = useState(false)
  const [restartBannerCount, setRestartBannerCount] = useState(0)
  const [activeNamespace, setActiveNamespace] = useState<SettingNamespace | null>(null)
  const searchRef = useRef<{ focus: () => void }>(null)
  const prevEntriesRef = useRef<Map<string, string>>(new Map())

  // Track which settings changed externally (WS/poll) for flash animation
  const changedKeys = useMemo(() => {
    const changed = new Set<string>()
    const prev = prevEntriesRef.current
    for (const e of entries) {
      const ck = `${e.definition.namespace}/${e.definition.key}`
      const prevVal = prev.get(ck)
      if (prevVal !== undefined && prevVal !== e.value) {
        changed.add(ck)
      }
    }
    return changed
  }, [entries])

  // Update ref after render commits (not inside useMemo to respect concurrent rendering)
  useEffect(() => {
    const next = new Map<string, string>()
    for (const e of entries) {
      next.set(`${e.definition.namespace}/${e.definition.key}`, e.value)
    }
    prevEntriesRef.current = next
  }, [entries])

  const {
    dirtyValues,
    setDirtyValues,
    handleValueChange,
    handleDiscard,
    handleSave: baseSave,
  } = useSettingsDirtyState(entries, updateSetting)

  const handleSave = useCallback(async () => {
    // Count restart-required settings being saved
    const restartCount = [...dirtyValues.keys()].filter((ck) => {
      const entry = entries.find(
        (e) => `${e.definition.namespace}/${e.definition.key}` === ck,
      )
      return entry?.definition.restart_required === true
    }).length
    await baseSave()
    if (restartCount > 0) setRestartBannerCount(restartCount)
  }, [baseSave, dirtyValues, entries])

  useSettingsKeyboard({
    onSave: handleSave,
    onSearchFocus: () => searchRef.current?.focus(),
    canSave: dirtyValues.size > 0 && !saving,
  })

  // Warn the user before they navigate away with unsaved setting drafts.
  // Covers both in-app navigation (via useBlocker) and tab close /
  // reload (via the beforeunload listener inside the hook).  Draft
  // persistence is intentionally NOT wired up -- settings can include
  // secrets and we don't want them lingering in localStorage.
  const hasUnsaved = dirtyValues.size > 0 || codeDirty
  const unsavedGuard = useUnsavedChangesGuard({
    when: hasUnsaved,
    message:
      'You have unsaved setting changes. Leaving now will discard them. Continue anyway?',
  })

  // Filter entries: exclude hidden, filter by level, filter by search
  const filteredByNamespace = useMemo(() => {
    const result = new Map<SettingNamespace, SettingEntry[]>()
    for (const ns of NAMESPACE_ORDER) {
      const nsEntries = entries.filter((e) => {
        if (e.definition.namespace !== ns) return false
        const compositeKey = `${e.definition.namespace}/${e.definition.key}`
        if (HIDDEN_SETTINGS.has(compositeKey)) return false
        if (!advancedMode && e.definition.level === 'advanced') return false
        if (searchQuery && !matchesSetting(e, searchQuery)) return false
        return true
      })
      if (nsEntries.length > 0) {
        result.set(ns, nsEntries)
      }
    }
    return result
  }, [entries, advancedMode, searchQuery])

  const namespaceCounts = useMemo(
    () => new Map(NAMESPACE_ORDER.map((ns) => [ns, filteredByNamespace.get(ns)?.length ?? 0])),
    [filteredByNamespace],
  )

  // Derive effective namespace -- clear selection if filtering removed all its entries
  const effectiveNamespace = activeNamespace && (namespaceCounts.get(activeNamespace) ?? 0) > 0
    ? activeNamespace
    : null

  const controllerDisabledMap = useMemo(
    () => buildControllerDisabledMap(entries, dirtyValues),
    [entries, dirtyValues],
  )

  const handleCodeSave = useCallback(
    async (changes: Map<string, string>): Promise<Set<string>> => {
      try {
        const failedKeys = await saveSettingsBatch(changes, updateSetting)

        // Clear GUI drafts for keys successfully saved from code mode
        setDirtyValues((prev) => {
          const next = new Map(prev)
          for (const key of changes.keys()) {
            if (!failedKeys.has(key)) next.delete(key)
          }
          return next
        })

        // Count restart-required settings among successful saves
        const restartCount = [...changes.keys()].filter((ck) => {
          if (failedKeys.has(ck)) return false
          const entry = entries.find(
            (e) => `${e.definition.namespace}/${e.definition.key}` === ck,
          )
          return entry?.definition.restart_required === true
        }).length
        if (restartCount > 0) setRestartBannerCount(restartCount)

        if (failedKeys.size === 0) {
          useToastStore.getState().add({ variant: 'success', title: 'Settings saved' })
        } else {
          useToastStore.getState().add({
            variant: 'error',
            title: `${failedKeys.size} setting(s) failed to save`,
          })
        }
        return failedKeys
      } catch (err) {
        log.error('Unexpected error in handleCodeSave:', err)
        useToastStore.getState().add({
          variant: 'error',
          title: 'Could not save settings',
          description:
            'Refresh the page and try again, or check the setting value for validation errors.',
        })
        return new Set(changes.keys())
      }
    },
    [updateSetting, setDirtyValues, entries],
  )

  const getFooterAction = useCallback((ns: SettingNamespace) => {
    if (ns === 'observability') {
      return (
        <SettingsActionCard
          to={ROUTES.SETTINGS_SINKS}
          title="Log Sinks"
          description="Configure log outputs, rotation, and routing"
        />
      )
    }
    if (ns === 'coordination') {
      return (
        <SettingsActionCard
          to={ROUTES.SETTINGS_CEREMONY_POLICY}
          title="Ceremony Policy"
          description="Configure scheduling strategies, velocity, and department overrides"
        />
      )
    }
    return undefined
  }, [])

  const pruneAdvancedDrafts = useCallback(() => {
    setDirtyValues((prev) => {
      const next = new Map(prev)
      for (const ck of prev.keys()) {
        const entry = entries.find(
          (e) => `${e.definition.namespace}/${e.definition.key}` === ck,
        )
        if (entry?.definition.level === 'advanced') {
          next.delete(ck)
        }
      }
      return next
    })
  }, [entries, setDirtyValues])

  const handleAdvancedToggle = useCallback((checked: boolean) => {
    if (checked) {
      const warned = sessionStorage.getItem(SETTINGS_ADVANCED_WARNED_KEY)
      if (warned !== 'true') {
        setShowAdvancedWarning(true)
        return
      }
    }
    if (!checked) pruneAdvancedDrafts()
    setAdvancedMode(checked)
    localStorage.setItem(SETTINGS_ADVANCED_KEY, String(checked))
  }, [pruneAdvancedDrafts])

  const confirmAdvancedMode = useCallback(() => {
    sessionStorage.setItem(SETTINGS_ADVANCED_WARNED_KEY, 'true')
    setAdvancedMode(true)
    localStorage.setItem(SETTINGS_ADVANCED_KEY, 'true')
    setShowAdvancedWarning(false)
  }, [])

  if (loading && entries.length === 0) {
    return <SettingsSkeleton />
  }

  // Visible entries for code editor, overlaid with GUI drafts so Code mode sees unsaved GUI edits
  const codeEntries = entries
    .map((entry) => {
      const ck = `${entry.definition.namespace}/${entry.definition.key}`
      const dirtyValue = dirtyValues.get(ck)
      return dirtyValue !== undefined ? { ...entry, value: dirtyValue } : entry
    })
    .filter((e) => {
      const ck = `${e.definition.namespace}/${e.definition.key}`
      if (HIDDEN_SETTINGS.has(ck)) return false
      if (!advancedMode && e.definition.level === 'advanced') return false
      return NAMESPACE_ORDER.includes(e.definition.namespace)
    })

  return (
    <div className="space-y-section-gap">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-lg font-semibold text-foreground">Settings</h1>
        <div className="flex items-center gap-4">
          {viewMode !== 'code' && (
            <SearchInput
              ref={searchRef}
              value={searchQuery}
              onChange={setSearchQuery}
              className="w-64"
              resultCount={searchQuery ? [...filteredByNamespace.values()].reduce((sum, arr) => sum + arr.length, 0) : undefined}
            />
          )}
          <ToggleField
            label="Code"
            checked={viewMode === 'code'}
            onChange={(v) => {
              if (!v && codeDirty) {
                setShowCodeDiscardWarning(true)
                return
              }
              setViewMode(v ? 'code' : 'gui')
            }}
          />
          <ToggleField
            label="Advanced"
            checked={advancedMode}
            onChange={handleAdvancedToggle}
          />
        </div>
      </div>

      <RestartBanner count={restartBannerCount} onDismiss={() => setRestartBannerCount(0)} />

      {error && (
        <ErrorBanner severity="error" title="Could not load settings" description={error} />
      )}

      {!wsConnected && !loading && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates disconnected"
          description={wsSetupError ?? 'Data may be stale until the connection recovers.'}
        />
      )}

      {advancedMode && (
        <AdvancedModeBanner
          onDisable={() => {
            pruneAdvancedDrafts()
            setAdvancedMode(false)
            localStorage.setItem(SETTINGS_ADVANCED_KEY, 'false')
          }}
        />
      )}

      {viewMode === 'code' ? (
        <ErrorBoundary level="section">
          <CodeEditorPanel entries={codeEntries} onSave={handleCodeSave} saving={saving} onDirtyChange={setCodeDirty} />
        </ErrorBoundary>
      ) : (
        <>
          <div className="flex items-center justify-between gap-2">
            <NamespaceTabBar
              namespaces={NAMESPACE_ORDER}
              activeNamespace={effectiveNamespace}
              onSelect={setActiveNamespace}
              namespaceCounts={namespaceCounts}
              namespaceIcons={NAMESPACE_ICONS}
            />
            {advancedMode && (
              // Subtle indicator next to the namespace tabs so the
              // operator knows they're seeing advanced settings even
              // after dismissing the AdvancedModeBanner. The visible
              // "Advanced" text IS the accessible name; an explicit
              // aria-label would just duplicate it.
              <span
                className="shrink-0 rounded-full border border-warning/30 bg-warning/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-warning"
              >
                Advanced
              </span>
            )}
          </div>

          {filteredByNamespace.size === 0 && (
            <EmptyState
              icon={Settings}
              title={searchQuery ? 'No matching settings' : 'No settings available'}
              description={
                searchQuery
                  ? 'Try a different search term or clear the filter.'
                  : 'Settings will appear once the backend is configured.'
              }
            />
          )}

          <AnimatePresence mode="wait">
          <motion.div
            key={effectiveNamespace ?? 'all'}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={anim.tween}
          >
          <StaggerGroup className="space-y-[var(--spacing-section-gap)]">
            {NAMESPACE_ORDER
              .filter((ns) => filteredByNamespace.has(ns))
              .filter((ns) => effectiveNamespace === null || ns === effectiveNamespace)
              .map((ns) => (
              <StaggerItem key={ns}>
                <ErrorBoundary level="section">
                  <NamespaceSection
                    displayName={NAMESPACE_DISPLAY_NAMES[ns]}
                    icon={NAMESPACE_ICONS[ns] ?? <Settings className="size-4" />}
                    entries={filteredByNamespace.get(ns)!}
                    dirtyValues={dirtyValues}
                    onValueChange={handleValueChange}
                    savingKeys={storeSavingKeys}
                    controllerDisabledMap={controllerDisabledMap}
                    forceOpen={effectiveNamespace !== null || searchQuery.length > 0}
                    hideHeader={effectiveNamespace !== null}
                    changedKeys={changedKeys}
                    highlightQuery={searchQuery}
                    footerAction={getFooterAction(ns)}
                  />
                </ErrorBoundary>
              </StaggerItem>
            ))}
          </StaggerGroup>
          </motion.div>
          </AnimatePresence>

          {/* Notifications preferences (client-side, not backend settings) */}
          <NotificationsSection />

          <FloatingSaveBar
            dirtyCount={dirtyValues.size}
            saving={saving}
            onSave={handleSave}
            onDiscard={handleDiscard}
            saveError={saveError}
          />
        </>
      )}

      <ConfirmDialog
        open={showAdvancedWarning}
        onOpenChange={setShowAdvancedWarning}
        title="Enable Advanced Mode?"
        description="Advanced settings control low-level system behavior. Misconfiguration may affect stability or security. Only change these if you know what you are doing."
        confirmLabel="Enable"
        onConfirm={confirmAdvancedMode}
      />

      <ConfirmDialog
        open={showCodeDiscardWarning}
        onOpenChange={setShowCodeDiscardWarning}
        title="Discard code editor changes?"
        description="You have unsaved changes in the code editor. Switching to GUI mode will discard them."
        confirmLabel="Discard"
        variant="destructive"
        onConfirm={() => {
          setCodeDirty(false)
          setShowCodeDiscardWarning(false)
          setViewMode('gui')
        }}
      />

      <ConfirmDialog
        open={unsavedGuard.confirmOpen}
        onOpenChange={(open) => { if (!open) unsavedGuard.cancel() }}
        title="Discard unsaved settings?"
        description={unsavedGuard.message}
        confirmLabel="Leave page"
        variant="destructive"
        onConfirm={unsavedGuard.proceed}
        onCancel={unsavedGuard.cancel}
      />
    </div>
  )
}
