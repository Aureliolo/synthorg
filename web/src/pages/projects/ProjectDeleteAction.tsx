import { useState } from 'react'

import { Trash2 } from 'lucide-react'
import { useNavigate } from 'react-router'

import type { Project } from '@/api/types/projects'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ROUTES } from '@/router/routes'
import { useProjectsStore } from '@/stores/projects'

export interface ProjectDeleteActionProps {
  project: Project
}

/**
 * Remove a project from the page that is about it.
 *
 * The list view can delete a selection, but a project opened on its own page
 * had no exit at all: the operator had to remember the name, navigate back,
 * find the row, tick it and use the bulk bar. The API has accepted the delete
 * throughout; only the control was missing.
 */
export function ProjectDeleteAction({ project }: ProjectDeleteActionProps) {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()

  const confirmDelete = async (): Promise<boolean> => {
    const removed = await useProjectsStore.getState().deleteProject(project.id)
    // A refusal keeps the dialog open, so the toast explaining it is read
    // beside the action that caused it rather than on a page that has already
    // navigated away.
    if (!removed) return false
    void navigate(ROUTES.PROJECTS)
    return true
  }

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => { setOpen(true) }}>
        <Trash2 aria-hidden="true" />
        Delete project
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        variant="destructive"
        title="Delete this project?"
        description={`"${project.name}" is removed, along with its plans and tasks and the workspace its agents wrote into. This action cannot be undone.`}
        confirmLabel="Delete project"
        onConfirm={confirmDelete}
      />
    </>
  )
}
