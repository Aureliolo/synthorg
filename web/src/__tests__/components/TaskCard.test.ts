import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import TaskCard from '@/components/tasks/TaskCard.vue'
import type { Task } from '@/api/types'

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

describe('TaskCard', () => {
  it('renders the task title', () => {
    const wrapper = mount(TaskCard, {
      props: { task: mockTask },
    })
    expect(wrapper.text()).toContain('Fix bug')
  })

  it('renders the assigned agent name', () => {
    const wrapper = mount(TaskCard, {
      props: { task: mockTask },
    })
    expect(wrapper.text()).toContain('agent-1')
  })

  it('shows "Unassigned" when assigned_to is null', () => {
    const unassigned: Task = { ...mockTask, assigned_to: null }
    const wrapper = mount(TaskCard, {
      props: { task: unassigned },
    })
    expect(wrapper.text()).toContain('Unassigned')
  })

  it('emits "click" event with the task when clicked', async () => {
    const wrapper = mount(TaskCard, {
      props: { task: mockTask },
    })
    await wrapper.find('[role="button"]').trigger('click')
    expect(wrapper.emitted('click')).toBeTruthy()
    expect(wrapper.emitted('click')![0]).toEqual([mockTask])
  })

  it('emits "click" event on Enter keydown', async () => {
    const wrapper = mount(TaskCard, {
      props: { task: mockTask },
    })
    await wrapper.find('[role="button"]').trigger('keydown.enter')
    expect(wrapper.emitted('click')).toBeTruthy()
    expect(wrapper.emitted('click')![0]).toEqual([mockTask])
  })

  it('emits "click" event on Space keydown', async () => {
    const wrapper = mount(TaskCard, {
      props: { task: mockTask },
    })
    await wrapper.find('[role="button"]').trigger('keydown.space')
    expect(wrapper.emitted('click')).toBeTruthy()
    expect(wrapper.emitted('click')![0]).toEqual([mockTask])
  })

  it('has role="button" attribute', () => {
    const wrapper = mount(TaskCard, {
      props: { task: mockTask },
    })
    const card = wrapper.find('[role="button"]')
    expect(card.exists()).toBe(true)
  })

  it('has tabindex="0" for keyboard accessibility', () => {
    const wrapper = mount(TaskCard, {
      props: { task: mockTask },
    })
    const card = wrapper.find('[role="button"]')
    expect(card.attributes('tabindex')).toBe('0')
  })

  it('renders different task titles correctly', () => {
    const custom: Task = { ...mockTask, title: 'Implement auth flow' }
    const wrapper = mount(TaskCard, {
      props: { task: custom },
    })
    expect(wrapper.text()).toContain('Implement auth flow')
  })
})
