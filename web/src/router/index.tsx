import { lazy, Suspense } from 'react'
import { createBrowserRouter, RouterProvider } from 'react-router'
import { AuthGuard, GuestGuard, SetupCompleteGuard, SetupGuard } from './guards'
import { ROUTES } from './routes'

// Lazy-loaded pages
const DashboardPage = lazy(() => import('@/pages/DashboardPage'))
const LoginPage = lazy(() => import('@/pages/LoginPage'))
const SetupPage = lazy(() => import('@/pages/SetupPage'))
const OrgChartPage = lazy(() => import('@/pages/OrgChartPage'))
const OrgEditPage = lazy(() => import('@/pages/OrgEditPage'))
const TaskBoardPage = lazy(() => import('@/pages/TaskBoardPage'))
const TaskDetailPage = lazy(() => import('@/pages/TaskDetailPage'))
const BudgetPage = lazy(() => import('@/pages/BudgetPage'))
const BudgetForecastPage = lazy(() => import('@/pages/BudgetForecastPage'))
const ReportsPage = lazy(() => import('@/pages/ReportsPage'))
const ApprovalsPage = lazy(() => import('@/pages/ApprovalsPage'))
const ScalingPage = lazy(() => import('@/pages/ScalingPage'))
const MetaPage = lazy(() => import('@/pages/MetaPage'))
const AgentsPage = lazy(() => import('@/pages/AgentsPage'))
const AgentDetailPage = lazy(() => import('@/pages/AgentDetailPage'))
const TrainingPage = lazy(() => import('@/pages/TrainingPage'))
const MessagesPage = lazy(() => import('@/pages/MessagesPage'))
const MeetingsPage = lazy(() => import('@/pages/MeetingsPage'))
const MeetingDetailPage = lazy(() => import('@/pages/MeetingDetailPage'))
const ProvidersPage = lazy(() => import('@/pages/ProvidersPage'))
const ProviderDetailPage = lazy(() => import('@/pages/ProviderDetailPage'))
const OntologyPage = lazy(() => import('@/pages/OntologyPage'))
const CustomRulesPage = lazy(() => import('@/pages/CustomRulesPage'))
const EscalationQueuePage = lazy(() => import('@/pages/EscalationQueuePage'))
const UsersPage = lazy(() => import('@/pages/UsersPage'))
const ProjectsPage = lazy(() => import('@/pages/ProjectsPage'))
const ProjectDetailPage = lazy(() => import('@/pages/ProjectDetailPage'))
const ArtifactsPage = lazy(() => import('@/pages/ArtifactsPage'))
const ArtifactDetailPage = lazy(() => import('@/pages/ArtifactDetailPage'))
const WorkflowsPage = lazy(() => import('@/pages/WorkflowsPage'))
const WorkflowEditorPage = lazy(() => import('@/pages/WorkflowEditorPage'))
const SubworkflowsPage = lazy(() => import('@/pages/SubworkflowsPage'))
const FineTuningPage = lazy(() => import('@/pages/FineTuningPage'))
const ClientListPage = lazy(() => import('@/pages/ClientListPage'))
const ClientDetailPage = lazy(() => import('@/pages/ClientDetailPage'))
const RequestQueuePage = lazy(() => import('@/pages/RequestQueuePage'))
const SimulationDashboardPage = lazy(
  () => import('@/pages/SimulationDashboardPage'),
)
const ReviewPipelinePage = lazy(() => import('@/pages/ReviewPipelinePage'))
const ConnectionsPage = lazy(() => import('@/pages/ConnectionsPage'))
const OauthAppsPage = lazy(() => import('@/pages/OauthAppsPage'))
const McpCatalogPage = lazy(() => import('@/pages/McpCatalogPage'))
const SettingsPage = lazy(() => import('@/pages/SettingsPage'))
const SettingsNamespacePage = lazy(() => import('@/pages/SettingsNamespacePage'))
const SettingsSinksPage = lazy(() => import('@/pages/SettingsSinksPage'))
const CeremonyPolicyPage = lazy(() => import('@/pages/settings/ceremony-policy/CeremonyPolicyPage'))
const NotFoundPage = lazy(() => import('@/pages/NotFoundPage'))
const AppLayout = lazy(() => import('@/components/layout/AppLayout'))

function SuspenseWrapper({ children }: { children: React.ReactNode }) {
  return (
    <Suspense
      fallback={
        <div className="flex h-screen items-center justify-center">
          <span className="text-sm text-muted-foreground">Loading...</span>
        </div>
      }
    >
      {children}
    </Suspense>
  )
}

/** Exported for test introspection (e.g. verifying /docs/ is not registered). */
// eslint-disable-next-line react-refresh/only-export-components
export const router = createBrowserRouter([
  // Public: Login
  {
    path: '/login',
    element: (
      <GuestGuard>
        <SuspenseWrapper>
          <LoginPage />
        </SuspenseWrapper>
      </GuestGuard>
    ),
  },
  // Public: Setup wizard
  {
    path: '/setup',
    element: (
      <SetupCompleteGuard>
        <SuspenseWrapper>
          <SetupPage />
        </SuspenseWrapper>
      </SetupCompleteGuard>
    ),
  },
  {
    path: '/setup/:step',
    element: (
      <SetupCompleteGuard>
        <SuspenseWrapper>
          <SetupPage />
        </SuspenseWrapper>
      </SetupCompleteGuard>
    ),
  },
  // Protected: All app routes with layout shell
  {
    element: <AuthGuard />,
    children: [
      {
        element: <SetupGuard />,
        children: [
          {
            element: (
              <SuspenseWrapper>
                <AppLayout />
              </SuspenseWrapper>
            ),
            children: [
              { index: true, element: <DashboardPage /> },
              { path: 'org', element: <OrgChartPage /> },
              { path: 'org/edit', element: <OrgEditPage /> },
              { path: 'tasks', element: <TaskBoardPage /> },
              { path: 'tasks/:taskId', element: <TaskDetailPage /> },
              { path: 'budget', element: <BudgetPage /> },
              { path: 'budget/forecast', element: <BudgetForecastPage /> },
              { path: 'reports', element: <ReportsPage /> },
              { path: 'approvals', element: <ApprovalsPage /> },
              { path: 'scaling', element: <ScalingPage /> },
              { path: ROUTES.META.slice(1), element: <MetaPage /> },
              { path: 'agents', element: <AgentsPage /> },
              { path: 'agents/:agentId', element: <AgentDetailPage /> },
              { path: ROUTES.TRAINING.slice(1), element: <TrainingPage /> },
              { path: 'messages', element: <MessagesPage /> },
              { path: 'meetings', element: <MeetingsPage /> },
              { path: 'meetings/:meetingId', element: <MeetingDetailPage /> },
              { path: 'providers', element: <ProvidersPage /> },
              { path: 'providers/:providerName', element: <ProviderDetailPage /> },
              { path: ROUTES.CONNECTIONS.slice(1), element: <ConnectionsPage /> },
              { path: ROUTES.OAUTH_APPS.slice(1), element: <OauthAppsPage /> },
              { path: ROUTES.MCP_CATALOG.slice(1), element: <McpCatalogPage /> },
              { path: 'ontology', element: <OntologyPage /> },
              { path: ROUTES.CUSTOM_RULES.slice(1), element: <CustomRulesPage /> },
              { path: ROUTES.ESCALATIONS.slice(1), element: <EscalationQueuePage /> },
              { path: ROUTES.USERS.slice(1), element: <UsersPage /> },
              { path: 'projects', element: <ProjectsPage /> },
              { path: 'projects/:projectId', element: <ProjectDetailPage /> },
              { path: 'artifacts', element: <ArtifactsPage /> },
              { path: 'artifacts/:artifactId', element: <ArtifactDetailPage /> },
              { path: 'workflows', element: <WorkflowsPage /> },
              { path: 'workflows/editor', element: <WorkflowEditorPage /> },
              { path: 'subworkflows', element: <SubworkflowsPage /> },
              { path: 'clients', element: <ClientListPage /> },
              { path: 'clients/requests', element: <RequestQueuePage /> },
              { path: 'clients/simulations', element: <SimulationDashboardPage /> },
              { path: 'clients/reviews/:taskId', element: <ReviewPipelinePage /> },
              { path: 'clients/:clientId', element: <ClientDetailPage /> },
              { path: ROUTES.SETTINGS_FINE_TUNING.slice(1), element: <FineTuningPage /> },
              { path: 'settings', element: <SettingsPage /> },
              { path: 'settings/observability/sinks', element: <SettingsSinksPage /> },
              { path: ROUTES.SETTINGS_CEREMONY_POLICY.slice(1), element: <CeremonyPolicyPage /> },
              { path: 'settings/:namespace', element: <SettingsNamespacePage /> },
              { path: '*', element: <NotFoundPage /> },
            ],
          },
        ],
      },
    ],
  },
])

export function AppRouter() {
  return <RouterProvider router={router} />
}
