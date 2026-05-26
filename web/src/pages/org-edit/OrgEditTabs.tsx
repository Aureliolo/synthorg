import { Tabs } from '@base-ui/react/tabs'
import { Building2, Settings, Users } from 'lucide-react'
import { Link } from 'react-router'
import { cn } from '@/lib/utils'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import type { useOrgEditData } from '@/hooks/useOrgEditData'
import { ROUTES } from '@/router/routes'
import { GeneralTab } from './GeneralTab'
import { AgentsTab } from './AgentsTab'
import { DepartmentsTab } from './DepartmentsTab'
import { type TabValue, isTabValue } from './useOrgEditTab'

type OrgEditData = ReturnType<typeof useOrgEditData>

const TRIGGER_CLASSES = cn(
  'px-4 py-2 text-sm font-medium text-text-secondary transition-colors',
  'data-[active]:text-foreground data-[active]:border-b-2 data-[active]:border-accent',
  'hover:text-foreground',
)

/**
 * Each tab renders as a real react-router `<Link>` via the Base UI
 * `render` prop, so middle-click / ctrl-click / "Open in new tab" all
 * work like the sidebar nav links. `nativeButton={false}` acknowledges
 * we intentionally render an `<a>` rather than the default `<button>`;
 * keyboard activation still works because Base UI fires on Enter/Space.
 */
function OrgEditTabList() {
  return (
    <Tabs.List className="flex border-b border-border" aria-label="Organization sections">
      <Tabs.Tab
        value="general"
        className={TRIGGER_CLASSES}
        nativeButton={false}
        render={<Link to={ROUTES.ORG_EDIT} />}
      >
        <span className="flex items-center gap-1.5">
          <Settings className="size-3.5" />
          General
        </span>
      </Tabs.Tab>
      <Tabs.Tab
        value="agents"
        className={TRIGGER_CLASSES}
        nativeButton={false}
        render={<Link to={`${ROUTES.ORG_EDIT}?tab=agents`} />}
      >
        <span className="flex items-center gap-1.5">
          <Users className="size-3.5" />
          Agents
        </span>
      </Tabs.Tab>
      <Tabs.Tab
        value="departments"
        className={TRIGGER_CLASSES}
        nativeButton={false}
        render={<Link to={`${ROUTES.ORG_EDIT}?tab=departments`} />}
      >
        <span className="flex items-center gap-1.5">
          <Building2 className="size-3.5" />
          Departments
        </span>
      </Tabs.Tab>
    </Tabs.List>
  )
}

function OrgEditTabPanels({ org }: { org: OrgEditData }) {
  return (
    <div className="pt-section-gap">
      <Tabs.Panel value="general">
        <ErrorBoundary level="section">
          <GeneralTab config={org.config} onUpdate={org.updateCompany} saving={org.saving} />
        </ErrorBoundary>
      </Tabs.Panel>

      <Tabs.Panel value="agents">
        <ErrorBoundary level="section">
          <AgentsTab
            config={org.config}
            saving={org.saving}
            onCreateAgent={org.createAgent}
            onUpdateAgent={org.updateAgent}
            onDeleteAgent={org.deleteAgent}
            onReorderAgents={org.reorderAgents}
            optimisticReorderAgents={org.optimisticReorderAgents}
          />
        </ErrorBoundary>
      </Tabs.Panel>

      <Tabs.Panel value="departments">
        <ErrorBoundary level="section">
          <DepartmentsTab
            config={org.config}
            departmentHealths={org.departmentHealths}
            saving={org.saving}
            onCreateDepartment={org.createDepartment}
            onUpdateDepartment={org.updateDepartment}
            onDeleteDepartment={org.deleteDepartment}
            onReorderDepartments={org.reorderDepartments}
            optimisticReorderDepartments={org.optimisticReorderDepartments}
            onCreateTeam={org.createTeam}
            onUpdateTeam={org.updateTeam}
            onDeleteTeam={org.deleteTeam}
            onReorderTeams={org.reorderTeams}
          />
        </ErrorBoundary>
      </Tabs.Panel>
    </div>
  )
}

export interface OrgEditTabsProps {
  org: OrgEditData
  activeTab: TabValue
  onTabChange: (value: TabValue) => void
}

/** Tabbed GUI editor (general / agents / departments). */
export function OrgEditTabs({ org, activeTab, onTabChange }: OrgEditTabsProps) {
  return (
    <Tabs.Root
      value={activeTab}
      onValueChange={(value: string) => {
        if (isTabValue(value)) onTabChange(value)
      }}
    >
      <OrgEditTabList />
      <OrgEditTabPanels org={org} />
    </Tabs.Root>
  )
}
