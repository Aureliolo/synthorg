import { Dialog } from '@base-ui/react/dialog'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import { useUsersStore } from '@/stores/users'
import type { OrgRole } from '@/api/types/enums'
import type {
  GrantOrgRoleRequest,
  UserResponse,
} from '@/api/endpoints/users'

interface GrantRoleDialogProps {
  user: UserResponse | null
  open: boolean
  onClose: () => void
}

const ROLE_OPTIONS: ReadonlyArray<{ value: OrgRole; label: string }> = [
  { value: 'owner', label: 'Owner' },
  { value: 'department_admin', label: 'Department admin' },
  { value: 'editor', label: 'Editor' },
  { value: 'viewer', label: 'Viewer' },
]

function GrantForm({ user, onClose }: { user: UserResponse; onClose: () => void }) {
  const grantOrgRole = useUsersStore((s) => s.grantOrgRole)
  const submitting = useUsersStore((s) => s.submitting)

  const [role, setRole] = useState<OrgRole>('viewer')
  const [departmentsRaw, setDepartmentsRaw] = useState<string>('')
  const [validationError, setValidationError] = useState<string | null>(null)

  const handleSubmit = async (): Promise<void> => {
    setValidationError(null)
    let payload: GrantOrgRoleRequest
    if (role === 'department_admin') {
      const scoped = departmentsRaw
        .split(',')
        .map((s) => s.trim())
        .filter((s) => s.length > 0)
      if (scoped.length === 0) {
        setValidationError(
          'Department admin requires at least one scoped department.',
        )
        return
      }
      payload = { role: 'department_admin', scoped_departments: scoped }
    } else {
      payload = { role }
    }
    const result = await grantOrgRole(user.id, payload)
    if (result !== null) {
      onClose()
    }
  }

  return (
    <>
      {validationError && (
        <ErrorBanner severity="warning" title={validationError} />
      )}

      <SelectField
        label="Role"
        value={role}
        onChange={(value) => setRole(value as OrgRole)}
        options={ROLE_OPTIONS}
      />

      {role === 'department_admin' && (
        <InputField
          label="Scoped departments"
          hint="Comma-separated list of department names"
          value={departmentsRaw}
          onChange={(e) => setDepartmentsRaw(e.target.value)}
          required
        />
      )}

      <div className="flex justify-end gap-grid-gap pt-card">
        <Button variant="secondary" onClick={onClose} disabled={submitting}>
          Cancel
        </Button>
        <Button onClick={() => void handleSubmit()} disabled={submitting}>
          {submitting ? 'Granting…' : 'Grant role'}
        </Button>
      </div>
    </>
  )
}

/**
 * Modal for granting an org role to one user.  Department admin
 * grants accept a scoped-departments list; other roles take none.
 */
export function GrantRoleDialog({ user, open, onClose }: GrantRoleDialogProps) {
  if (user === null) return null
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 bg-overlay backdrop-blur-sm" />
        <Dialog.Popup className="fixed left-1/2 top-1/2 z-popup w-full max-w-md -translate-x-1/2 -translate-y-1/2 rounded-md border border-border bg-card p-card-tight sm:p-card md:p-card-roomy shadow-card-hover">
          <Dialog.Title className="text-lg font-semibold text-foreground">
            Grant role
          </Dialog.Title>
          <Dialog.Description className="text-sm text-text-secondary">
            Grant {user.username} an additional org role.  Existing
            roles are preserved.
          </Dialog.Description>
          <div className="mt-section-gap flex flex-col gap-grid-gap">
            <GrantForm key={user.id} user={user} onClose={onClose} />
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
