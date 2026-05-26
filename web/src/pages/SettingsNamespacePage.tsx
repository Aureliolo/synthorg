import { useMemo, useState, type ReactNode } from 'react'
import { useParams, Link } from 'react-router'
import { ArrowLeft, Settings } from 'lucide-react'
import type { SettingEntry, SettingNamespace } from '@/api/types/settings'
import { ErrorBanner } from '@/components/ui/error-banner'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { useSettingsStore } from '@/stores/settings'
import { useSettingsData } from '@/hooks/useSettingsData'
import { useSettingsDirtyState } from '@/hooks/useSettingsDirtyState'
import { NAMESPACE_DISPLAY_NAMES, NAMESPACE_ORDER, SETTINGS_ADVANCED_KEY } from '@/utils/constants'
import { ROUTES } from '@/router/routes'
import { FloatingSaveBar } from './settings/FloatingSaveBar'
import { NamespaceSection } from './settings/NamespaceSection'
import { SearchInput } from './settings/SearchInput'
import { SettingsSkeleton } from './settings/SettingsSkeleton'
import { buildControllerDisabledMap } from './settings/utils'
import { filterNamespaceEntries } from './settings/settings-page-helpers'

function SettingsBackHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-4">
      <div className="flex items-center gap-4">
        <Button asChild variant="ghost" size="icon" aria-label="Back to settings">
          <Link to={ROUTES.SETTINGS}>
            <ArrowLeft className="size-4" />
          </Link>
        </Button>
        <h1 className="text-lg font-semibold text-foreground">{title}</h1>
      </div>
      {children}
    </div>
  )
}

interface NamespaceContentProps {
  displayName: string
  filteredEntries: SettingEntry[]
  searchQuery: string
  dirtyValues: ReturnType<typeof useSettingsDirtyState>['dirtyValues']
  onValueChange: ReturnType<typeof useSettingsDirtyState>['handleValueChange']
  savingKeys: ReturnType<typeof useSettingsStore.getState>['savingKeys']
  controllerDisabledMap: ReturnType<typeof buildControllerDisabledMap>
}

function NamespaceContent({
  displayName,
  filteredEntries,
  searchQuery,
  dirtyValues,
  onValueChange,
  savingKeys,
  controllerDisabledMap,
}: NamespaceContentProps) {
  if (filteredEntries.length === 0) {
    return (
      <EmptyState
        icon={Settings}
        title={searchQuery ? 'No matching settings' : 'No settings available'}
        description={
          searchQuery
            ? 'Try a different search term or clear the filter.'
            : `No ${displayName.toLowerCase()} settings are available.`
        }
      />
    )
  }
  return (
    <ErrorBoundary level="section">
      <NamespaceSection
        displayName={displayName}
        icon={<Settings className="size-4" />}
        entries={filteredEntries}
        dirtyValues={dirtyValues}
        onValueChange={onValueChange}
        savingKeys={savingKeys}
        controllerDisabledMap={controllerDisabledMap}
        forceOpen
      />
    </ErrorBoundary>
  )
}

export default function SettingsNamespacePage() {
  const { namespace } = useParams<{ namespace: string }>()
  const { entries, loading, error, saving, saveError, wsConnected, wsSetupError, updateSetting } =
    useSettingsData()
  const storeSavingKeys = useSettingsStore((s) => s.savingKeys)

  const [searchQuery, setSearchQuery] = useState('')
  const [advancedMode] = useState(() => localStorage.getItem(SETTINGS_ADVANCED_KEY) === 'true')

  const { dirtyValues, handleValueChange, handleDiscard, handleSave } = useSettingsDirtyState(
    entries,
    updateSetting,
  )
  const validNamespace = NAMESPACE_ORDER.includes(namespace as SettingNamespace)
  const ns = namespace as SettingNamespace

  const filteredEntries = useMemo(
    () => (validNamespace ? filterNamespaceEntries(entries, ns, advancedMode, searchQuery) : []),
    [entries, ns, validNamespace, advancedMode, searchQuery],
  )
  const controllerDisabledMap = useMemo(
    () => buildControllerDisabledMap(entries, dirtyValues),
    [entries, dirtyValues],
  )

  if (loading && entries.length === 0) {
    return <SettingsSkeleton />
  }

  if (!validNamespace) {
    return (
      <div className="space-y-section-gap">
        <SettingsBackHeader title="Settings" />
        <EmptyState
          icon={Settings}
          title="Unknown namespace"
          description={`"${namespace}" is not a valid settings namespace.`}
        />
      </div>
    )
  }

  const displayName = NAMESPACE_DISPLAY_NAMES[ns]

  return (
    <div className="space-y-section-gap">
      <SettingsBackHeader title={`${displayName} Settings`}>
        <SearchInput value={searchQuery} onChange={setSearchQuery} className="w-64" />
      </SettingsBackHeader>

      {error && (
        <ErrorBanner severity="error" title="Could not load settings namespace" description={error} />
      )}

      {!wsConnected && !loading && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates disconnected"
          description={wsSetupError ?? 'Data may be stale until the connection recovers.'}
        />
      )}

      <NamespaceContent
        displayName={displayName}
        filteredEntries={filteredEntries}
        searchQuery={searchQuery}
        dirtyValues={dirtyValues}
        onValueChange={handleValueChange}
        savingKeys={storeSavingKeys}
        controllerDisabledMap={controllerDisabledMap}
      />

      <FloatingSaveBar
        dirtyCount={dirtyValues.size}
        saving={saving}
        onSave={handleSave}
        onDiscard={handleDiscard}
        saveError={saveError}
      />
    </div>
  )
}
