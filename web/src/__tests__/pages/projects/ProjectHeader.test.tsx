import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ProjectHeader } from '@/pages/projects/ProjectHeader'
import { makeProject } from '@/__tests__/helpers/factories'

describe('ProjectHeader', () => {
  it('renders the Tasks count from the tasks actually fetched', () => {
    // The count must come from real task data. It was previously read from a
    // project field that nothing ever populated, so the header always showed
    // 0 while the task list below it rendered a full list.
    render(
      <ProjectHeader project={makeProject('proj-1')} taskCount={3} contributorCount={2} />,
    )

    const tasks = screen.getByText('Tasks').closest('div')
    expect(tasks).not.toBeNull()
    expect(within(tasks as HTMLElement).getByText('3')).toBeInTheDocument()
  })

  it('renders zero when the project genuinely has no tasks', () => {
    render(
      <ProjectHeader project={makeProject('proj-2')} taskCount={0} contributorCount={0} />,
    )

    const tasks = screen.getByText('Tasks').closest('div')
    expect(tasks).not.toBeNull()
    expect(within(tasks as HTMLElement).getByText('0')).toBeInTheDocument()
  })
})
