import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import { SectionCard } from '@/components/ui/section-card'
import { StatPill } from '@/components/ui/stat-pill'
import { MetricCard } from '@/components/ui/metric-card'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import { useStepCompletionSync } from './_hooks'
import { graphemeLength, validateCompanyStep } from '@/utils/setup-validation'
import { CURRENCY_OPTIONS } from '@/utils/currencies'
import type { CurrencyCode } from '@/utils/currencies'
import { ErrorCode } from '@/api/types/errors'
import { TemplateVariables } from './TemplateVariables'
import type { SetupAgentSummary, SetupCompanyResponse } from '@/api/types/setup'

type TemplateVariableValue = string | number | boolean

interface CompanyDetailsFormProps {
  companyName: string
  setCompanyName: (value: string) => void
  companyDescription: string
  setCompanyDescription: (value: string) => void
  currency: CurrencyCode
  setCurrency: (value: CurrencyCode) => void
  templateVariables: Readonly<Record<string, TemplateVariableValue>>
  setTemplateVariable: (key: string, value: TemplateVariableValue) => void
  disabled?: boolean
}

function CompanyDetailsForm({
  companyName,
  setCompanyName,
  companyDescription,
  setCompanyDescription,
  currency,
  setCurrency,
  templateVariables,
  setTemplateVariable,
  disabled,
}: CompanyDetailsFormProps) {
  return (
    <div className="space-y-4 rounded-lg border border-border bg-card p-card">
      <InputField
        label="Company Name"
        required
        value={companyName}
        onChange={(e) => setCompanyName(e.currentTarget.value)}
        placeholder="Your organization name"
        disabled={disabled}
        // Hint sets expectations up front; the error below fires
        // only once the user crosses the boundary, so the two
        // never display together.
        hint="Max 200 characters. Apply Template stays disabled until this is valid."
        error={
          companyName.trim() === ''
            ? null
            : graphemeLength(companyName.trim()) > 200
              ? 'Max 200 characters'
              : null
        }
      />

      <InputField
        label="Description"
        multiline
        rows={3}
        value={companyDescription}
        onChange={(e) => setCompanyDescription(e.currentTarget.value)}
        placeholder="Describe your organization (optional)"
        disabled={disabled}
        hint="Max 1000 characters"
        error={graphemeLength(companyDescription) > 1000 ? 'Max 1000 characters' : null}
      />

      <SelectField
        label="Display Currency"
        options={[...CURRENCY_OPTIONS]}
        value={currency}
        disabled={disabled}
        onChange={(value) => setCurrency(value as CurrencyCode)}
      />

      <SelectField
        label="Model Tier Profile"
        options={[
          { value: 'economy', label: 'Economy' },
          { value: 'balanced', label: 'Balanced' },
          { value: 'premium', label: 'Premium' },
        ]}
        value={String(templateVariables['model_tier_profile'] ?? 'balanced')}
        disabled={disabled}
        onChange={(v) => setTemplateVariable('model_tier_profile', v)}
        hint="Influences which model tiers are assigned to agents."
      />
    </div>
  )
}

function ApplyTemplateButton({
  onApply,
  disabled,
  loading,
  reapply,
}: {
  onApply: () => void
  disabled: boolean
  loading: boolean
  reapply: boolean
}) {
  const idleLabel = reapply ? 'Re-apply Template' : 'Apply Template'
  return (
    <Button onClick={onApply} disabled={disabled} className="w-full">
      {loading ? 'Applying Template...' : idleLabel}
    </Button>
  )
}

interface CompanyApplyControlsProps {
  fieldsLocked: boolean
  showApplyButton: boolean
  applyDisabled: boolean
  companyLoading: boolean
  editing: boolean
  onApply: () => void
  onStartEditing: () => void
}

/**
 * Apply / re-apply affordances. Once a company is applied the form
 * fields lock; this surfaces a locked-state note plus an explicit
 * "Edit & re-apply" toggle so a post-apply change is a deliberate
 * overwrite rather than a silently-inert edit.
 */
function CompanyApplyControls({
  fieldsLocked,
  showApplyButton,
  applyDisabled,
  companyLoading,
  editing,
  onApply,
  onStartEditing,
}: CompanyApplyControlsProps) {
  return (
    <>
      {fieldsLocked && (
        <div className="flex items-center justify-between gap-grid-gap rounded-lg border border-border bg-card p-card">
          <p className="text-sm text-muted-foreground">
            Company details are locked after applying. Edit and re-apply to
            regenerate the company and its agents from the template.
          </p>
          <Button variant="outline" onClick={onStartEditing} className="shrink-0">
            Edit &amp; re-apply
          </Button>
        </div>
      )}
      {showApplyButton && (
        <ApplyTemplateButton
          onApply={onApply}
          disabled={applyDisabled}
          loading={companyLoading}
          reapply={editing}
        />
      )}
    </>
  )
}

interface CompanyErrorBannerProps {
  companyError: string | null
  tierCoverageInsufficient: boolean
  applyDisabled: boolean
  onApply: () => void
  onOpenProviders: () => void
}

function CompanyErrorBanner({
  companyError,
  tierCoverageInsufficient,
  applyDisabled,
  onApply,
  onOpenProviders,
}: CompanyErrorBannerProps) {
  if (!companyError) return null
  return (
    <ErrorBanner
      variant="section"
      severity="error"
      title="Could not apply template"
      description={companyError}
      // Gate Retry by the same submit gate that controls the Apply button
      // so the user cannot retry while base details are invalid or while
      // a submit is already in flight. For the tier-coverage error
      // specifically, hide Retry entirely (it would always re-fail until
      // upstream provider state is fixed) and surface the
      // "Open Providers step" action via the ``action`` prop instead.
      onRetry={tierCoverageInsufficient || applyDisabled ? undefined : () => onApply()}
      action={
        tierCoverageInsufficient ? { label: 'Open Providers step', onClick: onOpenProviders } : undefined
      }
    />
  )
}

function CompanyPreview({
  companyResponse,
  agents,
}: {
  companyResponse: SetupCompanyResponse
  agents: readonly SetupAgentSummary[]
}) {
  return (
    <>
      <StaggerGroup className="grid grid-cols-3 gap-grid-gap max-[639px]:grid-cols-1">
        <StaggerItem>
          <MetricCard label="Departments" value={companyResponse.department_count} />
        </StaggerItem>
        <StaggerItem>
          <MetricCard label="Agents" value={companyResponse.agent_count} />
        </StaggerItem>
        <StaggerItem>
          <MetricCard label="Template" value={companyResponse.template_applied ?? 'None'} />
        </StaggerItem>
      </StaggerGroup>

      {agents.length > 0 && (
        <SectionCard title="Generated Agents">
          <ul className="space-y-1 text-xs text-muted-foreground">
            {agents.map((agent, index) => (
              // eslint-disable-next-line @eslint-react/no-array-index-key -- names may duplicate
              <li key={`${agent.name}-${index}`}>
                {agent.name} ({agent.department}) - {agent.tier} model
              </li>
            ))}
          </ul>
        </SectionCard>
      )}
    </>
  )
}

function useCompanyStepController() {
  const [editing, setEditing] = useState(false)
  const templates = useSetupWizardStore((s) => s.templates)
  const selectedTemplate = useSetupWizardStore((s) => s.selectedTemplate)
  const companyName = useSetupWizardStore((s) => s.companyName)
  const companyDescription = useSetupWizardStore((s) => s.companyDescription)
  const currency = useSetupWizardStore((s) => s.currency)
  const companyResponse = useSetupWizardStore((s) => s.companyResponse)
  const companyLoading = useSetupWizardStore((s) => s.companyLoading)
  const companyError = useSetupWizardStore((s) => s.companyError)
  const companyErrorCode = useSetupWizardStore((s) => s.companyErrorCode)
  const templateVariables = useSetupWizardStore((s) => s.templateVariables)
  const agents = useSetupWizardStore((s) => s.agents)

  const setCompanyName = useSetupWizardStore((s) => s.setCompanyName)
  const setCompanyDescription = useSetupWizardStore((s) => s.setCompanyDescription)
  const setCurrency = useSetupWizardStore((s) => s.setCurrency)
  const setTemplateVariable = useSetupWizardStore((s) => s.setTemplateVariable)
  const submitCompany = useSetupWizardStore((s) => s.submitCompany)
  const navigate = useNavigate()

  // Resolve the full template object for the selected template
  const selectedTemplateObj = useMemo(
    () => templates.find((t) => t.name === selectedTemplate) ?? null,
    [templates, selectedTemplate],
  )

  // Validate and track completion
  const validation = useMemo(() => validateCompanyStep({
    companyName,
    companyDescription,
    companyResponse,
  }), [companyName, companyDescription, companyResponse])

  useStepCompletionSync('company', validation.valid)

  // Clear a stale companyError on unmount; the store action no-ops while
  // a submit is in flight so a late-completing submit can still land its
  // error for the next CompanyStep mount to surface.
  useEffect(() => {
    return () => {
      useSetupWizardStore.getState().clearCompanyError()
    }
  }, [])

  const handleApplyTemplate = useCallback(async () => {
    // Re-applying is a deliberate POST /setup/company, which the backend
    // treats as an overwrite (settings_svc.set + agent regeneration) so
    // long as setup is not yet complete -- it is the supported "edit"
    // path, not a 409 duplicate. The store owns the error UX; only leave
    // edit mode once the submit lands cleanly so a failed re-apply keeps
    // the fields open for correction.
    await submitCompany()
    if (useSetupWizardStore.getState().companyError === null) {
      setEditing(false)
    }
  }, [submitCompany])

  const startEditing = useCallback(() => {
    setEditing(true)
  }, [])

  const goToProvidersStep = useCallback(() => {
    void navigate('/setup/providers')
  }, [navigate])

  // The Apply button is the affordance that moves `templateApplied` from
  // false -> true, so it must be enabled when `baseDetailsValid` holds (name
  // / description within limits) and a submit is not already in flight. The
  // validator's `baseDetailsValid` flag is the source of truth here, no
  // string matching against the template-gate error message.
  const applyDisabled = !validation.baseDetailsValid || companyLoading

  // When the most recent failure was the tier-coverage error, give
  // the operator a direct affordance to fix the upstream config
  // instead of a Retry-only banner that would always re-fail. The
  // discriminator is the structured ``error_code`` (2004), not the
  // human-readable message; the message is locale-coupled, the
  // code is the contract.
  const tierCoverageInsufficient =
    companyErrorCode === ErrorCode.PROVIDER_TIER_COVERAGE_INSUFFICIENT

  // Once a company is applied the form fields would otherwise stay
  // editable while every keystroke is silently inert (the backend state
  // is already written). Lock the fields in the applied state and expose
  // an explicit "Edit & re-apply" toggle that re-opens them and drives a
  // fresh overwrite POST.
  const applied = companyResponse !== null
  const fieldsLocked = applied && !editing
  const showApplyButton = !applied || editing

  return {
    selectedTemplate, companyName, setCompanyName, companyDescription, setCompanyDescription,
    currency, setCurrency, templateVariables, setTemplateVariable, selectedTemplateObj,
    companyResponse, companyError, agents, companyLoading, applyDisabled, tierCoverageInsufficient,
    fieldsLocked, showApplyButton, editing, startEditing, handleApplyTemplate, goToProvidersStep,
  }
}

export function CompanyStep() {
  const {
    selectedTemplate,
    companyName,
    setCompanyName,
    companyDescription,
    setCompanyDescription,
    currency,
    setCurrency,
    templateVariables,
    setTemplateVariable,
    selectedTemplateObj,
    companyResponse,
    companyError,
    agents,
    companyLoading,
    applyDisabled,
    tierCoverageInsufficient,
    fieldsLocked,
    showApplyButton,
    editing,
    startEditing,
    handleApplyTemplate,
    goToProvidersStep,
  } = useCompanyStepController()

  return (
    <div className="space-y-section-gap">
      <div className="space-y-2">
        <h2 className="text-lg font-semibold text-foreground">Configure Your Company</h2>
        <p className="text-sm text-muted-foreground">
          Name your organization and customize the template.
        </p>
      </div>

      {/* Template indicator */}
      {selectedTemplate && (
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">Template:</span>
          <StatPill label="" value={selectedTemplate} />
        </div>
      )}

      <CompanyDetailsForm
        companyName={companyName}
        setCompanyName={setCompanyName}
        companyDescription={companyDescription}
        setCompanyDescription={setCompanyDescription}
        currency={currency}
        setCurrency={setCurrency}
        templateVariables={templateVariables}
        setTemplateVariable={setTemplateVariable}
        disabled={fieldsLocked}
      />

      {/* Template variables */}
      <TemplateVariables
        variables={selectedTemplateObj?.variables ?? []}
        values={templateVariables}
        onChange={setTemplateVariable}
        currency={currency}
        disabled={fieldsLocked}
      />

      <CompanyApplyControls
        fieldsLocked={fieldsLocked}
        showApplyButton={showApplyButton}
        applyDisabled={applyDisabled}
        companyLoading={companyLoading}
        editing={editing}
        onApply={handleApplyTemplate}
        onStartEditing={startEditing}
      />

      <CompanyErrorBanner
        companyError={companyError}
        tierCoverageInsufficient={tierCoverageInsufficient}
        applyDisabled={applyDisabled}
        onApply={handleApplyTemplate}
        onOpenProviders={goToProvidersStep}
      />

      {/* Preview after applying */}
      {companyResponse && <CompanyPreview companyResponse={companyResponse} agents={agents} />}
    </div>
  )
}
