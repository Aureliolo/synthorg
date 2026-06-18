import { AnimatePresence, motion } from 'motion/react'
import { Link } from 'react-router'
import { Brain, Eye, Globe, HardDrive, Network, RefreshCw, Settings, Shield, Wallet } from 'lucide-react'
import type { SettingNamespace } from '@/api/types/settings'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { ToggleField } from '@/components/ui/toggle-field'
import { NAMESPACE_DISPLAY_NAMES, NAMESPACE_ORDER } from '@/pages/settings/settings-constants'
import { AdvancedModeBanner } from './settings/AdvancedModeBanner'
import { NotificationsSection } from './settings/NotificationsSection'
import { SecurityConfigSection } from './settings/SecurityConfigSection'
import { CodeEditorPanel } from './settings/CodeEditorPanel'
import { FloatingSaveBar } from './settings/FloatingSaveBar'
import { NamespaceSection } from './settings/NamespaceSection'
import { NamespaceTabBar } from './settings/SettingsHealthSection'
import { RestartBanner } from './settings/RestartBanner'
import { SearchInput } from './settings/SearchInput'
import { SettingsSkeleton } from './settings/SettingsSkeleton'
import { type SettingsPageController, useSettingsPageController } from './settings/useSettingsPageController'
import { ROUTES } from '@/router/routes'

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

function getFooterAction(ns: SettingNamespace): React.ReactNode {
  if (ns === 'security') {
    return (
      <div className="flex flex-col gap-section-gap">
        <SettingsActionCard
          to={ROUTES.SETTINGS_SECURITY_SESSIONS}
          title="Active Sessions"
          description="Review and revoke active sessions for your account"
        />
        <SettingsActionCard
          to={ROUTES.ADMIN_AUDIT_LOG}
          title="Audit Log"
          description="Review the immutable record of security-relevant actions"
        />
        <SecurityConfigSection />
      </div>
    )
  }
  if (ns === 'backup') {
    return (
      <SettingsActionCard
        to={ROUTES.ADMIN_BACKUPS}
        title="Backups"
        description="Create, restore, and delete system backups"
      />
    )
  }
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
}

function SettingsHeader({ ctrl }: { ctrl: SettingsPageController }) {
  const { ui, data, advanced, filters } = ctrl
  const resultCount = ui.searchQuery
    ? [...filters.filteredByNamespace.values()].reduce((sum, arr) => sum + arr.length, 0)
    : undefined
  return (
    <div className="flex flex-wrap items-center justify-between gap-section-gap">
      <div className="flex items-baseline gap-2">
        <h1 className="text-lg font-semibold text-foreground">Settings</h1>
        {data.isRefetching && (
          <span aria-live="polite" className="text-muted-foreground">
            <RefreshCw className="size-3 animate-spin" aria-hidden="true" />
            <span className="sr-only">Refreshing</span>
          </span>
        )}
      </div>
      <div className="flex items-center gap-4">
        {ui.viewMode !== 'code' && (
          <SearchInput
            ref={ctrl.searchRef}
            value={ui.searchQuery}
            onChange={ui.setSearchQuery}
            className="w-64"
            resultCount={resultCount}
          />
        )}
        <ToggleField
          label="Code"
          checked={ui.viewMode === 'code'}
          onChange={(v) => {
            if (!v && ui.codeDirty) {
              ui.setShowCodeDiscardWarning(true)
              return
            }
            ui.setViewMode(v ? 'code' : 'gui')
          }}
        />
        <ToggleField label="Advanced" checked={advanced.advancedMode} onChange={advanced.handleAdvancedToggle} />
      </div>
    </div>
  )
}

function SettingsBanners({ ctrl }: { ctrl: SettingsPageController }) {
  const { data, advanced, ui } = ctrl
  return (
    <>
      <RestartBanner count={ui.restartBannerCount} onDismiss={() => ui.setRestartBannerCount(0)} />
      {Boolean(data.error) && (
        <ErrorBanner severity="error" title="Could not load settings" description={data.error} />
      )}
      {!data.wsConnected && !data.loading && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates disconnected"
          description={data.wsSetupError ?? 'Data may be stale until the connection recovers.'}
        />
      )}
      {advanced.advancedMode && <AdvancedModeBanner onDisable={advanced.disableAdvanced} />}
    </>
  )
}

function SettingsNamespaceSections({ ctrl }: { ctrl: SettingsPageController }) {
  const { filters, ui, dirtyValues, storeSavingKeys, controllerDisabledMap, changedKeys, anim } = ctrl
  const { effectiveNamespace } = filters
  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={effectiveNamespace ?? 'all'}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={anim.tween}
      >
        <StaggerGroup className="space-y-[var(--spacing-section-gap)]">
          {NAMESPACE_ORDER.filter((ns) => filters.filteredByNamespace.has(ns))
            .filter((ns) => effectiveNamespace === null || ns === effectiveNamespace)
            .map((ns) => (
              <StaggerItem key={ns}>
                <ErrorBoundary level="section">
                  <NamespaceSection
                    displayName={NAMESPACE_DISPLAY_NAMES[ns]}
                    icon={NAMESPACE_ICONS[ns] ?? <Settings className="size-4" />}
                    entries={filters.filteredByNamespace.get(ns)!}
                    dirtyValues={dirtyValues}
                    onValueChange={ctrl.handleValueChange}
                    savingKeys={storeSavingKeys}
                    controllerDisabledMap={controllerDisabledMap}
                    forceOpen={effectiveNamespace !== null || ui.searchQuery.length > 0}
                    hideHeader={effectiveNamespace !== null}
                    changedKeys={changedKeys}
                    highlightQuery={ui.searchQuery}
                    footerAction={getFooterAction(ns)}
                  />
                </ErrorBoundary>
              </StaggerItem>
            ))}
        </StaggerGroup>
      </motion.div>
    </AnimatePresence>
  )
}

function SettingsGuiView({ ctrl }: { ctrl: SettingsPageController }) {
  const { filters, ui, advanced, data } = ctrl
  return (
    <>
      <div className="flex items-center justify-between gap-2">
        <NamespaceTabBar
          namespaces={NAMESPACE_ORDER}
          activeNamespace={filters.effectiveNamespace}
          onSelect={ui.setActiveNamespace}
          namespaceCounts={filters.namespaceCounts}
          namespaceIcons={NAMESPACE_ICONS}
        />
        {advanced.advancedMode && (
          <span className="shrink-0 rounded-full border border-warning/30 bg-warning/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-warning">
            Advanced
          </span>
        )}
      </div>

      {filters.filteredByNamespace.size === 0 && (
        <EmptyState
          icon={Settings}
          title={ui.searchQuery ? 'No matching settings' : 'No settings available'}
          description={
            ui.searchQuery
              ? 'Try a different search term or clear the filter.'
              : 'Settings will appear once the backend is configured.'
          }
        />
      )}

      <SettingsNamespaceSections ctrl={ctrl} />

      {/* Notifications preferences (client-side, not backend settings) */}
      <NotificationsSection />

      <FloatingSaveBar
        dirtyCount={ctrl.dirtyValues.size}
        saving={data.saving}
        onSave={ctrl.handleSave}
        onDiscard={ctrl.handleDiscard}
        saveError={data.saveError}
      />
    </>
  )
}

function SettingsDialogs({ ctrl }: { ctrl: SettingsPageController }) {
  const { ui, advanced, unsavedGuard } = ctrl
  return (
    <>
      <ConfirmDialog
        open={advanced.showAdvancedWarning}
        onOpenChange={advanced.setShowAdvancedWarning}
        title="Enable Advanced Mode?"
        description="Advanced settings control low-level system behavior. Misconfiguration may affect stability or security. Only change these if you know what you are doing."
        confirmLabel="Enable"
        onConfirm={advanced.confirmAdvancedMode}
      />

      <ConfirmDialog
        open={ui.showCodeDiscardWarning}
        onOpenChange={ui.setShowCodeDiscardWarning}
        title="Discard code editor changes?"
        description="You have unsaved changes in the code editor. Switching to GUI mode will discard them."
        confirmLabel="Discard"
        variant="destructive"
        onConfirm={() => {
          ui.setCodeDirty(false)
          ui.setShowCodeDiscardWarning(false)
          ui.setViewMode('gui')
        }}
      />

      <ConfirmDialog
        open={unsavedGuard.confirmOpen}
        onOpenChange={(open) => {
          if (!open) unsavedGuard.cancel()
        }}
        title="Discard unsaved settings?"
        description={unsavedGuard.message}
        confirmLabel="Leave page"
        variant="destructive"
        onConfirm={unsavedGuard.proceed}
        onCancel={unsavedGuard.cancel}
      />
    </>
  )
}

export default function SettingsPage() {
  const ctrl = useSettingsPageController()

  if (ctrl.data.loading && ctrl.data.entries.length === 0) {
    return <SettingsSkeleton />
  }

  return (
    <div className="space-y-section-gap">
      <SettingsHeader ctrl={ctrl} />
      <SettingsBanners ctrl={ctrl} />
      {ctrl.ui.viewMode === 'code' ? (
        <ErrorBoundary level="section">
          <CodeEditorPanel
            entries={ctrl.codeEntries}
            onSave={ctrl.handleCodeSave}
            saving={ctrl.data.saving}
            onDirtyChange={ctrl.ui.setCodeDirty}
          />
        </ErrorBoundary>
      ) : (
        <SettingsGuiView ctrl={ctrl} />
      )}
      <SettingsDialogs ctrl={ctrl} />
    </div>
  )
}
