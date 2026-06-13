import { useCallback, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { ArrowLeft } from 'lucide-react'
import { companyVersionsClient } from '@/api/endpoints/version-history'
import { ErrorBanner } from '@/components/ui/error-banner'
import { WsConnectionBanner } from '@/components/ui/ws-connection-banner'
import { Button } from '@/components/ui/button'
import { ToggleField } from '@/components/ui/toggle-field'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { VersionHistorySection } from '@/components/version-rollback/VersionHistorySection'
import type { UpdateCompanyRequest } from '@/api/types/org'
import { AUTONOMY_LEVEL_VALUES, type AutonomyLevel } from '@/api/types/enums'
import { useOrgEditData } from '@/hooks/useOrgEditData'
import { ROUTES } from '@/router/routes'
import { OrgEditSkeleton } from './org-edit/OrgEditSkeleton'
import { YamlEditorPanel } from './org-edit/YamlEditorPanel'
import { OrgEditTabs } from './org-edit/OrgEditTabs'
import { useOrgEditTab } from './org-edit/useOrgEditTab'

const VALID_AUTONOMY_LEVELS: ReadonlySet<string> = new Set(AUTONOMY_LEVEL_VALUES)

/** Map a parsed YAML document onto a typed company-update request. */
function buildCompanyUpdate(parsed: Record<string, unknown>): UpdateCompanyRequest {
  // Omit keys the YAML doesn't set so the existing value is not silently
  // cleared (a dropped key is wire-identical to the previous ``: undefined``).
  // An explicit ``null`` is forwarded only for fields the backend treats as a
  // clear; ``autonomy_level`` is NOT one of those (the backend's partial-update
  // guard ignores a null autonomy_level), so a null there is a backend no-op.
  const autonomyRaw = parsed['autonomy_level']
  return {
    ...(typeof parsed['company_name'] === 'string'
      ? { company_name: parsed['company_name'] }
      : {}),
    ...(typeof autonomyRaw === 'string' && VALID_AUTONOMY_LEVELS.has(autonomyRaw)
      ? { autonomy_level: autonomyRaw as AutonomyLevel }
      : autonomyRaw === null
        ? { autonomy_level: null }
        : {}),
    ...(typeof parsed['budget_monthly'] === 'number' && Number.isFinite(parsed['budget_monthly'])
      ? { budget_monthly: parsed['budget_monthly'] }
      : {}),
    ...(typeof parsed['communication_pattern'] === 'string'
      ? { communication_pattern: parsed['communication_pattern'] }
      : {}),
  }
}

function OrgEditHeader({ children }: { children?: ReactNode }) {
  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-4">
        <Button asChild variant="ghost" size="icon" aria-label="Back to Org Chart">
          <Link to={ROUTES.ORG}>
            <ArrowLeft className="size-4" />
          </Link>
        </Button>
        <h1 className="text-lg font-semibold text-foreground">Edit Organization</h1>
      </div>
      {children != null && children !== false && children}
    </div>
  )
}

function OrgEditErrorBanner({ error, saveError }: { error: string | null; saveError: string | null }) {
  if (!error && !saveError) return null
  return (
    <ErrorBanner
      severity="error"
      title={saveError ? 'Could not save organization' : 'Could not load organization'}
      description={saveError || error || undefined}
    />
  )
}

export default function OrgEditPage() {
  const [yamlMode, setYamlMode] = useState(false)
  const org = useOrgEditData()
  const { updateCompany } = org
  const { activeTab, handleTabChange } = useOrgEditTab()

  const handleYamlSave = useCallback(
    async (parsed: Record<string, unknown>): Promise<boolean> => {
      // updateCompany owns the toast UX; the boolean return lets the
      // YAML editor know whether to clear its dirty flag.
      return updateCompany(buildCompanyUpdate(parsed))
    },
    [updateCompany],
  )

  if (org.loading && !org.config) {
    return <OrgEditSkeleton />
  }

  if (!org.loading && !org.config) {
    return (
      <div className="space-y-section-gap">
        <OrgEditHeader />
        <ErrorBanner
          severity="error"
          title="Could not load organization"
          description={org.error ?? undefined}
        />
      </div>
    )
  }

  return (
    <div className="space-y-section-gap">
      <OrgEditHeader>
        <ToggleField label="YAML" checked={yamlMode} onChange={setYamlMode} />
      </OrgEditHeader>

      <OrgEditErrorBanner error={org.error} saveError={org.saveError} />

      <WsConnectionBanner
        description={org.wsSetupError ?? 'Edits may not sync until the connection recovers.'}
      />

      {yamlMode ? (
        <YamlEditorPanel config={org.config} onSave={handleYamlSave} saving={org.saving} />
      ) : (
        <OrgEditTabs org={org} activeTab={activeTab} onTabChange={handleTabChange} />
      )}

      <ErrorBoundary level="section">
        <VersionHistorySection
          client={companyVersionsClient}
          title="Company history"
          description="Read-only audit trail of organization snapshots. Select two versions to compare."
          emptyTitle="No company versions yet"
          emptyDescription="Versions appear here after the first edit to the organization structure."
        />
      </ErrorBoundary>
    </div>
  )
}
