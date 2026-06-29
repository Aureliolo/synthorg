import { useRef, useState } from 'react'
import { Download, ShieldCheck, Upload } from 'lucide-react'

import { exportSecurityConfig, importSecurityConfig } from '@/api/endpoints/settings'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { InputField } from '@/components/ui/input-field'
import { SectionCard } from '@/components/ui/section-card'
import { useToastStore } from '@/stores/toast'
import { downloadTextFile } from '@/utils/download'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { isObject } from '@/utils/type-guards'

/** State + handlers for the security-config export / import actions. */
function useSecurityConfigActions() {
  const toast = useToastStore((s) => s.add)
  const [exporting, setExporting] = useState(false)
  const [importing, setImporting] = useState(false)
  const [pendingConfig, setPendingConfig] = useState<Record<string, unknown> | null>(null)
  const [importReason, setImportReason] = useState('')

  function notifyCustomPoliciesWarning(warning: string | null | undefined) {
    if (warning) {
      toast({ variant: 'warning', title: 'Custom policies need matching code', description: warning })
    }
  }

  async function handleExport() {
    setExporting(true)
    try {
      const result = await exportSecurityConfig()
      downloadTextFile(
        JSON.stringify(result.config, null, 2),
        'security-config.json',
        'application/json',
      )
      toast({ variant: 'success', title: 'Security configuration exported' })
      notifyCustomPoliciesWarning(result.custom_policies_warning)
    } catch (err) {
      toast({ variant: 'error', ...getCrudErrorTitle(err, 'Export failed'), description: getErrorMessage(err) })
    } finally {
      setExporting(false)
    }
  }

  async function readImportFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    // Reset so re-selecting the same file still fires a change event.
    event.target.value = ''
    if (!file) return
    try {
      const parsed: unknown = JSON.parse(await file.text())
      if (!isObject(parsed)) {
        throw new Error('Import file must contain a JSON object')
      }
      setPendingConfig(parsed)
    } catch (err) {
      toast({ variant: 'error', title: 'Could not read import file', description: getErrorMessage(err) })
    }
  }

  async function handleConfirmImport(): Promise<boolean> {
    if (!pendingConfig) return false
    const config = pendingConfig
    // The destructive confirmation IS the deliberate action the backend
    // security-write guardrail requires, so the import carries confirm=true
    // plus the operator's reason (a descriptive default when left blank); the
    // actor is taken from the authenticated request. This lets an import that
    // weakens the posture through without a separate rejection round-trip.
    const reason = importReason.trim() || 'Imported via the security settings dashboard'
    setImporting(true)
    try {
      const result = await importSecurityConfig({ config, confirm: true, reason })
      toast({ variant: 'success', title: 'Security configuration imported' })
      notifyCustomPoliciesWarning(result.custom_policies_warning)
      // Returning true closes the dialog; the close handler clears the
      // pending config + reason. A failed import returns false below so the
      // dialog stays open with the upload + reason intact for a retry.
      return true
    } catch (err) {
      toast({ variant: 'error', ...getCrudErrorTitle(err, 'Import failed'), description: getErrorMessage(err) })
      return false
    } finally {
      setImporting(false)
    }
  }

  return {
    exporting,
    importing,
    pendingConfig,
    setPendingConfig,
    importReason,
    setImportReason,
    handleExport,
    readImportFile,
    handleConfirmImport,
  }
}

/**
 * Export / import the backend security configuration.
 *
 * Export downloads the current config as a JSON file; import reads a JSON
 * file, confirms (the import overwrites registered security settings), and
 * posts it. Validation failures (HTTP 422) surface as an error toast. The
 * action owns its own toast UX, so callers never wrap these in try/catch.
 */
export function SecurityConfigSection() {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const {
    exporting,
    importing,
    pendingConfig,
    setPendingConfig,
    importReason,
    setImportReason,
    handleExport,
    readImportFile,
    handleConfirmImport,
  } = useSecurityConfigActions()

  return (
    <SectionCard title="Configuration Export / Import" icon={ShieldCheck}>
      <div className="flex flex-col gap-section-gap">
        <p className="text-xs text-text-secondary">
          Export the active security configuration as JSON, or import a previously exported file.
          Importing overwrites the registered security settings.
        </p>
        <div className="flex flex-wrap gap-3">
          <Button variant="outline" size="sm" disabled={exporting} onClick={() => void handleExport()}>
            <Download className="size-4" aria-hidden="true" />
            Export
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={importing}
            onClick={() => fileInputRef.current?.click()}
          >
            <Upload className="size-4" aria-hidden="true" />
            Import from file
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            aria-hidden="true"
            onChange={(e) => void readImportFile(e)}
          />
        </div>
      </div>

      <ConfirmDialog
        open={pendingConfig !== null}
        onOpenChange={(open) => {
          if (!open) {
            setPendingConfig(null)
            setImportReason('')
          }
        }}
        title="Import security configuration?"
        description="This overwrites the current security settings with the imported configuration. Invalid configurations are rejected without changing anything."
        confirmLabel="Import"
        variant="destructive"
        onConfirm={handleConfirmImport}
        onCancel={() => {
          setPendingConfig(null)
          setImportReason('')
        }}
      >
        <InputField
          multiline
          label="Reason"
          hint="Recorded in the security audit trail. A weakening import needs a reason; a descriptive default is used if left blank."
          placeholder="Why is this configuration being imported?"
          rows={2}
          value={importReason}
          onValueChange={setImportReason}
        />
      </ConfirmDialog>
    </SectionCard>
  )
}
