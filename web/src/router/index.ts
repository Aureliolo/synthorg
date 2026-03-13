import { createRouter, createWebHistory } from 'vue-router'
import { authGuard } from './guards'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginPage.vue'),
      meta: { requiresAuth: false },
    },
    {
      path: '/setup',
      name: 'setup',
      component: () => import('@/views/SetupPage.vue'),
      meta: { requiresAuth: false },
    },
    {
      path: '/',
      name: 'dashboard',
      component: () => import('@/views/DashboardPage.vue'),
    },
    {
      path: '/org-chart',
      name: 'org-chart',
      component: () => import('@/views/OrgChartPage.vue'),
    },
    {
      path: '/tasks',
      name: 'tasks',
      component: () => import('@/views/TaskBoardPage.vue'),
    },
    {
      path: '/messages',
      name: 'messages',
      component: () => import('@/views/MessageFeedPage.vue'),
    },
    {
      path: '/approvals',
      name: 'approvals',
      component: () => import('@/views/ApprovalQueuePage.vue'),
    },
    {
      path: '/agents',
      name: 'agents',
      component: () => import('@/views/AgentProfilesPage.vue'),
    },
    {
      path: '/agents/:name',
      name: 'agent-detail',
      component: () => import('@/views/AgentDetailPage.vue'),
      props: true,
    },
    {
      path: '/budget',
      name: 'budget',
      component: () => import('@/views/BudgetPanelPage.vue'),
    },
    {
      path: '/meetings',
      name: 'meetings',
      component: () => import('@/views/MeetingLogsPage.vue'),
    },
    {
      path: '/artifacts',
      name: 'artifacts',
      component: () => import('@/views/ArtifactBrowserPage.vue'),
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('@/views/SettingsPage.vue'),
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/',
    },
  ],
})

router.beforeEach(authGuard)

export { router }
