import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), go: vi.fn() }),
  useRoute: () => ({ params: {} }),
  RouterLink: { template: '<a><slot /></a>' },
  createRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    go: vi.fn(),
    beforeEach: vi.fn(),
    currentRoute: { value: { path: '/' } },
  }),
  createWebHistory: vi.fn(),
}))

vi.mock('primevue/usetoast', () => ({
  useToast: () => ({ add: vi.fn() }),
}))

vi.mock('primevue/button', () => ({
  default: {
    props: ['label', 'icon', 'size'],
    template: '<button>{{ label }}</button>',
  },
}))

vi.mock('@/components/layout/AppShell.vue', () => ({
  default: { template: '<div><slot /></div>' },
}))

vi.mock('@/components/common/PageHeader.vue', () => ({
  default: {
    props: ['title', 'subtitle'],
    template: '<div><h1>{{ title }}</h1><p>{{ subtitle }}</p><slot name="actions" /></div>',
  },
}))

vi.mock('@/components/common/LoadingSkeleton.vue', () => ({
  default: {
    props: ['lines'],
    template: '<div data-testid="loading-skeleton">Loading...</div>',
  },
}))

vi.mock('@/components/common/ErrorBoundary.vue', () => ({
  default: {
    props: ['error'],
    template: '<div><slot /></div>',
  },
}))

vi.mock('@/components/tasks/KanbanBoard.vue', () => ({
  default: {
    props: ['tasksByStatus'],
    template: '<div data-testid="kanban-board">Kanban Board</div>',
  },
}))

vi.mock('@/components/tasks/TaskListView.vue', () => ({
  default: {
    props: ['tasks', 'total', 'loading'],
    template: '<div data-testid="task-list">Task List</div>',
  },
}))

vi.mock('@/components/tasks/TaskDetailPanel.vue', () => ({
  default: {
    props: ['task', 'visible'],
    template: '<div data-testid="task-detail">Task Detail</div>',
  },
}))

vi.mock('@/components/tasks/TaskCreateDialog.vue', () => ({
  default: {
    props: ['visible', 'agents'],
    template: '<div data-testid="task-create">Create Task</div>',
  },
}))

vi.mock('@/components/tasks/TaskFilters.vue', () => ({
  default: {
    props: ['agents', 'filters'],
    template: '<div data-testid="task-filters">Filters</div>',
  },
}))

vi.mock('@/api/endpoints/tasks', () => ({
  listTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  createTask: vi.fn(),
  updateTask: vi.fn(),
  transitionTask: vi.fn(),
  cancelTask: vi.fn(),
  deleteTask: vi.fn(),
}))

vi.mock('@/api/endpoints/agents', () => ({
  listAgents: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  getAgent: vi.fn(),
  getAutonomy: vi.fn(),
  setAutonomy: vi.fn(),
}))

vi.mock('@/api/endpoints/auth', () => ({
  getMe: vi.fn(),
  login: vi.fn(),
  setup: vi.fn(),
  changePassword: vi.fn(),
}))

import TaskBoardPage from '@/views/TaskBoardPage.vue'

describe('TaskBoardPage', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('mounts without error', () => {
    const wrapper = mount(TaskBoardPage)
    expect(wrapper.exists()).toBe(true)
  })

  it('renders "Tasks" heading', () => {
    const wrapper = mount(TaskBoardPage)
    expect(wrapper.find('h1').text()).toBe('Tasks')
  })

  it('fetches tasks and agents on mount', async () => {
    const { listTasks } = await import('@/api/endpoints/tasks')
    const { listAgents } = await import('@/api/endpoints/agents')
    mount(TaskBoardPage)
    await flushPromises()
    expect(listTasks).toHaveBeenCalled()
    expect(listAgents).toHaveBeenCalled()
  })

  it('renders kanban board by default after loading', async () => {
    const wrapper = mount(TaskBoardPage)
    await flushPromises()
    expect(wrapper.find('[data-testid="kanban-board"]').exists()).toBe(true)
  })

  it('renders view toggle buttons', () => {
    const wrapper = mount(TaskBoardPage)
    expect(wrapper.text()).toContain('Board')
    expect(wrapper.text()).toContain('List')
  })

  it('renders task filters', () => {
    const wrapper = mount(TaskBoardPage)
    expect(wrapper.find('[data-testid="task-filters"]').exists()).toBe(true)
  })
})
