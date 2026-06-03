import { useEffect, useState } from 'react'
import { InputField } from '@/components/ui/input-field'
import { LazyCodeMirrorEditor } from '@/components/ui/lazy-code-mirror-editor'

export interface MilestoneDrivenConfigProps {
  config: Record<string, unknown>
  onChange: (config: Record<string, unknown>) => void
  disabled?: boolean
}

export function MilestoneDrivenConfig({ config, onChange, disabled }: MilestoneDrivenConfigProps) {
  const transitionMilestone = typeof config.transition_milestone === 'string' ? config.transition_milestone : ''
  const [rawJson, setRawJson] = useState(() => JSON.stringify(Array.isArray(config.milestones) ? config.milestones : [], null, 2))
  const [jsonError, setJsonError] = useState<string | null>(null)

  // Sync rawJson when config.milestones changes externally (e.g. parent reset).
  // rawJson is intentionally excluded from deps to avoid feedback loops;
  // we only want to sync when the *prop* changes, not when the user edits.
  useEffect(() => {
    const milestones = Array.isArray(config.milestones) ? config.milestones : []
    const incoming = JSON.stringify(milestones, null, 2)
    try {
      const currentParsed = JSON.parse(rawJson)
      const incomingParsed = milestones
      if (JSON.stringify(currentParsed) !== JSON.stringify(incomingParsed)) {
        // eslint-disable-next-line @eslint-react/set-state-in-effect -- legitimate prop-to-local-state sync
        setRawJson(incoming)
        // eslint-disable-next-line @eslint-react/set-state-in-effect -- legitimate prop-to-local-state sync
        setJsonError(null)
      }
    } catch {
      // Current rawJson is invalid -- always sync from prop
      // eslint-disable-next-line @eslint-react/set-state-in-effect -- legitimate prop-to-local-state sync
      setRawJson(incoming)
      // eslint-disable-next-line @eslint-react/set-state-in-effect -- legitimate prop-to-local-state sync
      setJsonError(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- rawJson intentionally excluded: this resyncs local state FROM the prop, so depending on rawJson would loop
  }, [config.milestones])

  return (
    <div className="space-y-3">
      <div>
        <p className="mb-1 text-sm font-medium text-foreground">Milestones</p>
        <p className="mb-2 text-xs text-text-secondary">
          JSON array of milestone definitions with name and ceremony fields
        </p>
        <LazyCodeMirrorEditor
          value={rawJson}
          onChange={(val) => {
            setRawJson(val)
            try {
              const parsed: unknown = JSON.parse(val)
              if (!Array.isArray(parsed)) {
                setJsonError('Must be a JSON array')
                return
              }
              onChange({ ...config, milestones: parsed })
              setJsonError(null)
            } catch {
              setJsonError('Invalid JSON')
            }
          }}
          language="json"
          readOnly={disabled}
          aria-label="Milestones JSON"
          className="max-h-48"
        />
        {jsonError && (
          <p className="mt-1 text-xs text-danger">{jsonError}</p>
        )}
      </div>

      <InputField
        label="Transition Milestone"
        value={transitionMilestone}
        onChange={(e) => onChange({ ...config, transition_milestone: e.target.value })}
        disabled={disabled}
        hint="Milestone name that triggers sprint auto-transition"
      />
    </div>
  )
}
