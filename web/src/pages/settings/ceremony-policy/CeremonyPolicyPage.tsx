import { Link } from 'react-router'
import { ArrowLeft, Loader2, Settings, Timer } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { SectionCard } from '@/components/ui/section-card'
import { SkeletonCard } from '@/components/ui/skeleton'
import { PolicySourceBadge } from '@/components/ui/policy-source-badge'
import { ROUTES } from '@/router/routes'
import { StrategyPicker } from './StrategyPicker'
import { StrategyChangeWarning } from './StrategyChangeWarning'
import { StrategyConfigPanel } from './StrategyConfigPanel'
import { PolicyFieldsPanel } from './PolicyFieldsPanel'
import { DepartmentOverridesPanel } from './DepartmentOverridesPanel'
import { CeremonyListPanel } from './CeremonyListPanel'
import { type CeremonyPolicyController, useCeremonyPolicyController } from './useCeremonyPolicyController'

function CeremonyPolicyHeader() {
  return (
    <div className="flex items-center gap-3">
      <Link
        to={ROUTES.SETTINGS}
        aria-label="Back to settings"
        className="rounded-md p-1.5 text-text-muted transition-colors hover:bg-card hover:text-foreground"
      >
        <ArrowLeft className="size-4" />
      </Link>
      <div className="flex items-center gap-2">
        <Timer className="size-5 text-accent" />
        <h1 className="text-lg font-semibold">Ceremony Policy</h1>
      </div>
    </div>
  )
}

function CeremonyLoadBanners({
  loading,
  storeError,
  activeStrategyError,
}: {
  loading: boolean
  storeError: string | null
  activeStrategyError: string | null
}) {
  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="size-6 animate-spin text-text-muted" />
      </div>
    )
  }
  return (
    <>
      {storeError && (
        <div className="rounded-md border border-danger/30 bg-danger/5 p-card text-sm text-danger">
          Failed to load resolved policy: {storeError}
        </div>
      )}
      {activeStrategyError && (
        <div className="rounded-md border border-warning/30 bg-warning/5 p-card text-sm text-warning">
          Failed to load active strategy: {activeStrategyError}
        </div>
      )}
    </>
  )
}

function PolicySaveErrors({
  storeSaveError,
  saveError,
  configParseError,
  overridesParseError,
}: {
  storeSaveError: string | null
  saveError: string | null
  configParseError: boolean
  overridesParseError: boolean
}) {
  return (
    <>
      {Boolean(storeSaveError || saveError) && (
        <div className="rounded-md border border-danger/30 bg-danger/5 p-card text-sm text-danger">
          Save failed: {saveError ?? storeSaveError}
        </div>
      )}
      {Boolean(configParseError || overridesParseError) && (
        <div className="rounded-md border border-warning/30 bg-warning/5 p-card text-sm text-warning">
          Cannot save -- stored JSON is corrupt. Fix the raw values in the settings code editor before saving.
        </div>
      )}
    </>
  )
}

function PolicySaveRow({
  isDirty,
  saving,
  disabled,
  onSave,
}: {
  isDirty: boolean
  saving: boolean
  disabled: boolean
  onSave: () => void
}) {
  return (
    <div className="flex items-center justify-end gap-3 pt-2">
      {Boolean(isDirty && !saving) && <span className="text-xs text-text-muted">Unsaved changes</span>}
      <Button onClick={onSave} disabled={disabled}>
        {saving && <Loader2 className="mr-2 size-4 animate-spin" />}
        Save Policy
      </Button>
    </div>
  )
}

function ProjectPolicyCard({ ctrl }: { ctrl: CeremonyPolicyController }) {
  const { form, store, saving } = ctrl
  const saveDisabled =
    !ctrl.isDirty || saving || ctrl.configParseError || ctrl.overridesParseError
  return (
    <SectionCard title="Project Policy" icon={Settings}>
      <div className="space-y-5">
        <div className="flex items-start gap-2">
          <div className="flex-1">
            <StrategyPicker value={form.form.strategy} onChange={form.handleStrategyChange} disabled={saving} />
          </div>
          {store.resolvedPolicy && (
            <PolicySourceBadge source={store.resolvedPolicy.strategy.source} className="mt-7" />
          )}
        </div>

        <div className="border-t border-border pt-4">
          <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-text-muted">
            Strategy Configuration
          </p>
          <StrategyConfigPanel
            strategy={form.form.strategy}
            config={form.form.strategyConfig}
            onChange={form.setStrategyConfig}
            disabled={saving}
          />
        </div>

        <div className="border-t border-border pt-4">
          <PolicyFieldsPanel
            velocityCalculator={form.form.velocityCalculator}
            autoTransition={form.form.autoTransition}
            transitionThreshold={form.form.transitionThreshold}
            onVelocityCalculatorChange={form.setVelocityCalculator}
            onAutoTransitionChange={form.setAutoTransition}
            onTransitionThresholdChange={form.setTransitionThreshold}
            resolvedPolicy={store.resolvedPolicy}
            disabled={saving}
          />
        </div>

        <PolicySaveErrors
          storeSaveError={store.storeSaveError}
          saveError={ctrl.saveError}
          configParseError={ctrl.configParseError}
          overridesParseError={ctrl.overridesParseError}
        />

        <PolicySaveRow isDirty={ctrl.isDirty} saving={saving} disabled={saveDisabled} onSave={ctrl.handleSave} />
      </div>
    </SectionCard>
  )
}

function CeremonyPolicyBody({ ctrl }: { ctrl: CeremonyPolicyController }) {
  const { store } = ctrl
  const activeStrat = store.activeStrategy?.strategy ?? null
  return (
    <>
      {activeStrat != null && ctrl.form.form.strategy !== activeStrat && (
        <StrategyChangeWarning currentStrategy={ctrl.form.form.strategy} activeStrategy={activeStrat} />
      )}

      <ProjectPolicyCard ctrl={ctrl} />

      {ctrl.deptLoading && <SkeletonCard />}
      {Boolean(!ctrl.deptLoading && ctrl.departments.length > 0) && (
        <DepartmentOverridesPanel departments={ctrl.departments} />
      )}

      <CeremonyListPanel
        overrides={ctrl.overrides.overrides}
        ceremonyNames={ctrl.overrides.ceremonyNames}
        onOverrideChange={ctrl.overrides.handleOverrideChange}
        saving={ctrl.saving}
      />
    </>
  )
}

export default function CeremonyPolicyPage() {
  const ctrl = useCeremonyPolicyController()
  const { store } = ctrl

  return (
    <ErrorBoundary level="page">
      <div className="mx-auto max-w-3xl space-y-section-gap p-6">
        <CeremonyPolicyHeader />
        <CeremonyLoadBanners
          loading={store.loading}
          storeError={store.storeError}
          activeStrategyError={store.activeStrategyError}
        />
        {!store.loading && <CeremonyPolicyBody ctrl={ctrl} />}
      </div>
    </ErrorBoundary>
  )
}
