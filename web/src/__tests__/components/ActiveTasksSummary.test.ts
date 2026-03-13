import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ActiveTasksSummary from '@/components/dashboard/ActiveTasksSummary.vue'
import type { Task } from '@/api/types'

const pushMock = vi.fn()

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: pushMock }),
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
}))

const mockTask: Task = {
  id: 't1',
  title: 'Fix bug',
  description: '',
  type: 'development',
  priority: 'high',
  status: 'in_progress',
  project: 'proj',
  created_by: 'admin',
  assigned_to: 'agent-1',
  reviewers: [],
  dependencies: [],
  artifacts_expected: [],
  acceptance_criteria: [],
  estimated_complexity: 'medium',
  budget_limit: 10,
  cost_usd: 0,
  deadline: null,
  max_retries: 3,
  parent_task_id: null,
  delegation_chain: [],
  task_structure: null,
  coordination_topology: 'auto',
  version: 1,
  created_at: '2026-03-12T10:00:00Z',
  updated_at: '2026-03-12T10:00:00Z',
}

describe('ActiveTasksSummary', () => {
  it('renders "Active Tasks" heading', () => {
    const wrapper = mount(ActiveTasksSummary, {
      props: { tasks: [] },
    })
    expect(wrapper.text()).toContain('Active Tasks')
  })

  it('shows "No active tasks" when tasks array is empty', () => {
    const wrapper = mount(ActiveTasksSummary, {
      props: { tasks: [] },
    })
    expect(wrapper.text()).toContain('No active tasks')
  })

  it('renders task titles when tasks are provided', () => {
    const tasks: Task[] = [
      { ...mockTask, id: 't1', title: 'Fix bug' },
      { ...mockTask, id: 't2', title: 'Add feature' },
    ]
    const wrapper = mount(ActiveTasksSummary, {
      props: { tasks },
    })
    expect(wrapper.text()).toContain('Fix bug')
    expect(wrapper.text()).toContain('Add feature')
  })

  it('renders at most 5 task titles', () => {
    const tasks: Task[] = Array.from({ length: 7 }, (_, i) => ({
      ...mockTask,
      id: `t${i}`,
      title: `Task ${i}`,
    }))
    const wrapper = mount(ActiveTasksSummary, {
      props: { tasks },
    })
    expect(wrapper.text()).toContain('Task 0')
    expect(wrapper.text()).toContain('Task 4')
    expect(wrapper.text()).not.toContain('Task 5')
    expect(wrapper.text()).not.toContain('Task 6')
  })

  it('shows "View all" button', () => {
    const wrapper = mount(ActiveTasksSummary, {
      props: { tasks: [] },
    })
    expect(wrapper.text()).toContain('View all')
  })

  it('navigates to /tasks when "View all" is clicked', async () => {
    pushMock.mockClear()
    const wrapper = mount(ActiveTasksSummary, {
      props: { tasks: [] },
    })
    const link = wrapper.find('a')
    expect(link.attributes('href')).toBe('/tasks')
  })

  it('does not show "No active tasks" when tasks are present', () => {
    const wrapper = mount(ActiveTasksSummary, {
      props: { tasks: [mockTask] },
    })
    expect(wrapper.text()).not.toContain('No active tasks')
  })

  it('shows assigned agent name for each task', () => {
    const wrapper = mount(ActiveTasksSummary, {
      props: { tasks: [mockTask] },
    })
    expect(wrapper.text()).toContain('agent-1')
  })

  it('shows "Unassigned" when assigned_to is null', () => {
    const unassigned: Task = { ...mockTask, assigned_to: null }
    const wrapper = mount(ActiveTasksSummary, {
      props: { tasks: [unassigned] },
    })
    expect(wrapper.text()).toContain('Unassigned')
  })
})
