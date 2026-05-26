import { useCallback, useRef, useState } from 'react'
import type { CompanyConfig } from '@/api/types/org'
import { Button } from '@/components/ui/button'
import { serializeToYaml, parseYaml, validateCompanyYaml } from '@/utils/yaml'

export interface YamlEditorPanelProps {
  config: CompanyConfig | null
  /**
   * Persist the parsed YAML. Resolves to ``true`` on success and
   * ``false`` on failure; the page's store-backed mutation already
   * owns the toast UX, so this panel just uses the boolean to decide
   * whether to clear ``dirty`` state.
   */
  onSave: (parsed: Record<string, unknown>) => Promise<boolean>
  saving: boolean
}

interface YamlEditorFooterProps {
  dirty: boolean
  saving: boolean
  onSave: () => void
  onReset: () => void
}

function YamlEditorFooter({ dirty, saving, onSave, onReset }: YamlEditorFooterProps) {
  const busy = !dirty || saving
  return (
    <div className="flex items-center gap-3">
      <Button onClick={onSave} disabled={busy}>
        {saving ? 'Saving...' : 'Save YAML'}
      </Button>
      <Button variant="outline" onClick={onReset} disabled={busy}>
        Reset
      </Button>
      {dirty && <span className="text-xs text-warning">Unsaved changes</span>}
    </div>
  )
}

export function YamlEditorPanel({ config, onSave, saving }: YamlEditorPanelProps) {
  const [yamlText, setYamlText] = useState('')
  const [parseError, setParseError] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)

  const prevConfigRef = useRef<typeof config | undefined>(undefined)
  if (config !== prevConfigRef.current) {
    prevConfigRef.current = config
    if (config && !dirty) {
      setYamlText(serializeToYaml(config))
      setDirty(false)
      setParseError(null)
    }
  }

  const handleChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setYamlText(e.target.value)
    setDirty(true)
    setParseError(null)
  }, [])

  const handleSave = useCallback(async () => {
    try {
      const parsed = parseYaml(yamlText)
      const validationError = validateCompanyYaml(parsed)
      if (validationError) {
        setParseError(validationError)
        return
      }
      const ok = await onSave(parsed)
      if (ok) setDirty(false)
    } catch (err) {
      setParseError(err instanceof Error ? err.message : 'Failed to parse YAML')
    }
  }, [yamlText, onSave])

  const handleReset = useCallback(() => {
    if (config) {
      setYamlText(serializeToYaml(config))
      setDirty(false)
      setParseError(null)
    }
  }, [config])

  return (
    <div className="space-y-3">
      <textarea
        value={yamlText}
        onChange={handleChange}
        className="w-full min-h-96 rounded-lg border border-border bg-surface p-4 font-mono text-sm text-foreground outline-none focus:ring-2 focus:ring-accent resize-y"
        spellCheck={false}
        aria-label="YAML editor"
      />
      <p className="text-xs text-text-muted">
        Save applies company-level settings only. Use the GUI tabs to manage agents and departments.
      </p>
      {parseError && (
        <p className="text-xs text-danger" role="alert">{parseError}</p>
      )}
      <YamlEditorFooter dirty={dirty} saving={saving} onSave={handleSave} onReset={handleReset} />
    </div>
  )
}
