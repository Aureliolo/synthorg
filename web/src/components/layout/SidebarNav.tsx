import {
  Activity,
  BookOpen,
  Briefcase,
  ClipboardList,
  Cpu,
  DollarSign,
  FileText,
  FolderKanban,
  GitBranch,
  GraduationCap,
  Inbox,
  KanbanSquare,
  KeyRound,
  Layers,
  LayoutDashboard,
  LibraryBig,
  MessageSquare,
  Package,
  Plug,
  Radio,
  Scale,
  Settings,
  Shapes,
  ShieldCheck,
  Sparkles,
  UserCheck,
  Users,
  Video,
  Workflow,
} from 'lucide-react'
import { ROUTES } from '@/router/routes'
import { SidebarNavGroup } from './SidebarNavGroup'
import { SidebarNavItem } from './SidebarNavItem'
import { SidebarSection } from './SidebarSection'

export interface SidebarNavProps {
  collapsed: boolean
}

export function SidebarNav({ collapsed }: SidebarNavProps) {
  return (
    <nav className="flex-1 overflow-y-auto px-2 py-3" aria-label="Main navigation">
      <SidebarSection collapsed={collapsed}>
        <SidebarNavGroup>
          <SidebarNavItem to={ROUTES.DASHBOARD} icon={LayoutDashboard} label="Dashboard" collapsed={collapsed} end />
          <SidebarNavItem to={ROUTES.MISSION_CONTROL} icon={Radio} label="Mission Control" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.ORG} icon={GitBranch} label="Org Chart" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.ROLES} icon={Briefcase} label="Roles" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.TASKS} icon={KanbanSquare} label="Task Board" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.BUDGET} icon={DollarSign} label="Budget" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.REPORTS} icon={FileText} label="Reports" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.CHARTERS} icon={ClipboardList} label="Charters" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.APPROVALS} icon={ShieldCheck} label="Approvals" collapsed={collapsed} badge={0} />
          <SidebarNavItem to={ROUTES.SCALING} icon={Scale} label="Scaling" collapsed={collapsed} />
        </SidebarNavGroup>
      </SidebarSection>

      <SidebarSection label="Workspace" collapsed={collapsed} withTopBorder>
        <SidebarNavGroup>
          <SidebarNavItem to={ROUTES.AGENTS} icon={Users} label="Agents" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.TRAINING} icon={GraduationCap} label="Training" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.PROJECTS} icon={FolderKanban} label="Projects" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.WORKFLOWS} icon={Workflow} label="Workflows" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.SUBWORKFLOWS} icon={Layers} label="Subworkflows" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.ARTIFACTS} icon={Package} label="Artifacts" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.MESSAGES} icon={MessageSquare} label="Messages" collapsed={collapsed} badge={0} />
          <SidebarNavItem to={ROUTES.MEETINGS} icon={Video} label="Meetings" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.PROVIDERS} icon={Cpu} label="Providers" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.ONTOLOGY} icon={Shapes} label="Ontology" collapsed={collapsed} />
        </SidebarNavGroup>
      </SidebarSection>

      <SidebarSection label="Integrations" collapsed={collapsed} withTopBorder>
        <SidebarNavGroup>
          <SidebarNavItem to={ROUTES.CONNECTIONS} icon={Plug} label="Connections" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.OAUTH_APPS} icon={KeyRound} label="OAuth Apps" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.MCP_CATALOG} icon={LibraryBig} label="MCP Catalog" collapsed={collapsed} />
        </SidebarNavGroup>
      </SidebarSection>

      <SidebarSection collapsed={collapsed} withTopBorder>
        <SidebarNavGroup>
          <SidebarNavItem to={ROUTES.DOCUMENTATION} icon={BookOpen} label="Docs" collapsed={collapsed} external />
          <SidebarNavItem to={ROUTES.CLIENTS} icon={UserCheck} label="Clients" collapsed={collapsed} end />
          <SidebarNavItem to={ROUTES.REQUEST_QUEUE} icon={Inbox} label="Request Queue" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.SIMULATION_DASHBOARD} icon={Activity} label="Simulations" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.SETTINGS_FINE_TUNING} icon={Sparkles} label="Fine-Tuning" collapsed={collapsed} />
          <SidebarNavItem
            to={ROUTES.SETTINGS}
            icon={Settings}
            label="Settings"
            collapsed={collapsed}
            inactivePaths={[ROUTES.SETTINGS_FINE_TUNING]}
          />
        </SidebarNavGroup>
      </SidebarSection>
    </nav>
  )
}
