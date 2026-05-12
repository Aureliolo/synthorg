import { useCallback, useEffect, useMemo } from 'react'
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
import { validateCompanyStep } from '@/utils/setup-validation'
import { CURRENCY_OPTIONS } from '@/utils/currencies'
import type { CurrencyCode } from '@/utils/currencies'
import { ErrorCode } from '@/api/types/errors'
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

  // Clear a stale companyError on remount unless a submit is currently
  // in flight (its eventual response should be allowed to write to the
  // error slot). Mirrors ProvidersStep's mount-time cleanup so the two
  // steps follow the same pattern; otherwise the previous attempt's
  // error would resurface as soon as the user revisits the step.
  useEffect(() => {
    const state = useSetupWizardStore.getState()
    if (!state.companyLoading && (state.companyError || state.companyErrorCode)) {
      useSetupWizardStore.setState({
        companyError: null,
        companyErrorCode: null,
      })
    }
  }, [])

  const handleApplyTemplate = useCallback(async () => {
    await submitCompany()
  }, [submitCompany])

  const goToProvidersStep = useCallback(() => {
    navigate('/setup/providers')
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
  const errorBannerAction = tierCoverageInsufficient
    ? { label: 'Open Providers step', onClick: goToProvidersStep }
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
          // specifically, hide Retry entirely (it would always re-fail until
          // upstream provider state is fixed) and surface the
          // "Open Providers step" action via the ``action`` prop instead.
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
