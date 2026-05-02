import { useCallback, useEffect, useMemo } from 'react'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import { SectionCard } from '@/components/ui/section-card'
import { StatPill } from '@/components/ui/stat-pill'
import { MetricCard } from '@/components/ui/metric-card'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import { validateCompanyStep } from '@/utils/setup-validation'
import { CURRENCY_OPTIONS } from '@/utils/currencies'
import type { CurrencyCode } from '@/utils/currencies'
import { ERROR_CODE_PROVIDER_TIER_COVERAGE_INSUFFICIENT } from '@/api/types/errors'
import { TemplateVariables } from './TemplateVariables'

export function CompanyStep() {
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
  // Cross-step gate: derive whether at least one configured provider
  // has at least one model. Without this the operator can click Apply
  // Template and hit a backend tier-coverage error -- the same error
  // the issue calls out as the user-reported "trapped state". The
  // selector picks the boolean (not the providers map) so this
  // component does not re-render on every per-provider model edit.
  const hasUsableProvider = useSetupWizardStore((s) =>
    Object.values(s.providers).some((p) => p.models.length > 0),
  )

  const setCompanyName = useSetupWizardStore((s) => s.setCompanyName)
  const setCompanyDescription = useSetupWizardStore((s) => s.setCompanyDescription)
  const setCurrency = useSetupWizardStore((s) => s.setCurrency)
  const setTemplateVariable = useSetupWizardStore((s) => s.setTemplateVariable)
  const submitCompany = useSetupWizardStore((s) => s.submitCompany)
  const setStep = useSetupWizardStore((s) => s.setStep)
  const markStepComplete = useSetupWizardStore((s) => s.markStepComplete)
  const markStepIncomplete = useSetupWizardStore((s) => s.markStepIncomplete)

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

  useEffect(() => {
    if (validation.valid) {
      markStepComplete('company')
    } else {
      markStepIncomplete('company')
    }
  }, [validation.valid, markStepComplete, markStepIncomplete])

  // Clear a stale companyError when the user leaves the step (e.g.
  // navigates back to Providers to fix tier coverage). Guard against
  // racing an in-flight submit: if the user navigates away while
  // companyLoading is true, leave the error slot alone so the
  // eventual response can write to it. The next CompanyStep mount
  // will see the error and surface it; without this guard a
  // long-running submit that fails after unmount silently nulls its
  // own error.
  useEffect(() => {
    return () => {
      const state = useSetupWizardStore.getState()
      if (!state.companyLoading) {
        useSetupWizardStore.setState({
          companyError: null,
          companyErrorCode: null,
        })
      }
    }
  }, [])

  const handleApplyTemplate = useCallback(async () => {
    await submitCompany()
  }, [submitCompany])

  const goToProvidersStep = useCallback(() => {
    setStep('providers')
  }, [setStep])

  // The Apply button is the affordance that moves `templateApplied` from
  // false -> true, so it must be enabled when `baseDetailsValid` holds (name
  // / description within limits) and a submit is not already in flight. The
  // validator's `baseDetailsValid` flag is the source of truth here -- no
  // string matching against the template-gate error message.
  // Additional cross-step gate: at least one provider must expose at
  // least one model. Without this, clicking Apply would hit the
  // backend ``ProviderTierCoverageInsufficientError`` (422) every
  // time -- a guaranteed-fail click is worse UX than a disabled
  // button with an inline explanation.
  const applyDisabled =
    !validation.baseDetailsValid || companyLoading || !hasUsableProvider

  // When the cross-step provider gate is what's blocking submission
  // (base details are valid, not loading, but no usable provider),
  // surface an inline help banner above the button so the operator
  // knows why it's disabled and can fix the upstream config in one
  // click. Suppressed once the request has succeeded (preview is
  // showing instead of the form).
  const showProviderGateHelp =
    !companyResponse
    && validation.baseDetailsValid
    && !companyLoading
    && !hasUsableProvider

  // When the most recent failure was the tier-coverage error, give
  // the operator a direct affordance to fix the upstream config
  // instead of a Retry-only banner that would always re-fail. The
  // discriminator is the structured ``error_code`` (2004), not the
  // human-readable message -- the message is locale-coupled, the
  // code is the contract.
  const tierCoverageInsufficient =
    companyErrorCode === ERROR_CODE_PROVIDER_TIER_COVERAGE_INSUFFICIENT
  const errorBannerAction = tierCoverageInsufficient
    ? { label: 'Go back to Providers step', onClick: goToProvidersStep }
    : undefined

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

      {/* Company details form */}
      <div className="space-y-4 rounded-lg border border-border bg-card p-card">
        <InputField
          label="Company Name"
          required
          value={companyName}
          onChange={(e) => setCompanyName(e.currentTarget.value)}
          placeholder="Your organization name"
          // Hint sets expectations up front; the error below fires
          // only once the user crosses the boundary, so the two
          // never display together.
          hint="Max 200 characters. Apply Template stays disabled until this is valid."
          error={
            companyName.trim() === ''
              ? null
              : companyName.trim().length > 200
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
          hint="Max 1000 characters"
          error={companyDescription.length > 1000 ? 'Max 1000 characters' : null}
        />

        <SelectField
          label="Display Currency"
          options={[...CURRENCY_OPTIONS]}
          value={currency}
          onChange={(value) => setCurrency(value as CurrencyCode)}
        />

        <SelectField
          label="Model Tier Profile"
          options={[
            { value: 'economy', label: 'Economy' },
            { value: 'balanced', label: 'Balanced' },
            { value: 'premium', label: 'Premium' },
          ]}
          value={String(templateVariables.model_tier_profile ?? 'balanced')}
          onChange={(v) => setTemplateVariable('model_tier_profile', v)}
          hint="Influences which model tiers are assigned to agents."
        />
      </div>

      {/* Template variables */}
      <TemplateVariables
        variables={selectedTemplateObj?.variables ?? []}
        values={templateVariables}
        onChange={setTemplateVariable}
        currency={currency}
      />

      {/* Inline help when the cross-step provider gate is blocking submission.
          Rendered above the Apply button so the operator sees the cause
          before they have to click a disabled control to find out. */}
      {showProviderGateHelp && (
        <ErrorBanner
          variant="inline"
          severity="info"
          title="Add a model before applying a template"
          description={
            'Apply Template stays disabled until at least one configured '
            + 'provider exposes at least one model. Open the Providers step, '
            + 'add a model, then return here.'
          }
          action={{ label: 'Go back to Providers step', onClick: goToProvidersStep }}
        />
      )}

      {/* Apply template button. */}
      {!companyResponse && (
        <Button
          onClick={handleApplyTemplate}
          disabled={applyDisabled}
          className="w-full"
        >
          {companyLoading ? 'Applying Template...' : 'Apply Template'}
        </Button>
      )}

      {companyError && (
        <ErrorBanner
          variant="section"
          severity="error"
          title="Could not apply template"
          description={companyError}
          // Gate Retry by the same submit gate that controls the Apply button
          // so the user cannot retry while base details are invalid or while
          // a submit is already in flight. For the tier-coverage error
          // specifically, hide Retry entirely -- it would always re-fail
          // until upstream provider state is fixed -- and surface the
          // "Go back to Providers step" action via the ``action`` prop
          // instead.
          onRetry={
            tierCoverageInsufficient || applyDisabled
              ? undefined
              : () => void handleApplyTemplate()
          }
          action={errorBannerAction}
        />
      )}

      {/* Preview after applying */}
      {companyResponse && (
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
      )}

      {/* Agent preview list */}
      {companyResponse && agents.length > 0 && (
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
    </div>
  )
}
