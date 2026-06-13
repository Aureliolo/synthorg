/**
 * Company profile settings that live in the settings store rather than
 * the company-config endpoint: the free-text description and the
 * name-generation locales. Both read/write via the settings API
 * (``company/description`` and ``company/name_locales``).
 */
import { useCallback, useEffect, useState } from 'react'
import { Loader2, Building2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { SectionCard } from '@/components/ui/section-card'
import { getNamespaceSettings, updateSetting } from '@/api/endpoints/settings'
import type { SettingEntry } from '@/api/types/settings'
import { useToastStore } from '@/stores/toast'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'

const log = createLogger('CompanyProfileSection')

function valueFor(entries: readonly SettingEntry[], key: string): string | undefined {
  return entries.find((e) => e.definition.key === key)?.value
}

/** Parse the ``name_locales`` JSON array setting into a comma-joined string. */
function parseLocales(raw: string | undefined): string {
  if (raw == null || raw.trim() === '') return ''
  try {
    const parsed: unknown = JSON.parse(raw)
    if (Array.isArray(parsed)) return parsed.filter((x): x is string => typeof x === 'string').join(', ')
  } catch {
    // Malformed persisted value: fall back to the raw string so the
    // operator can see and correct it rather than silently blanking it.
    return raw
  }
  return raw
}

/** Serialize a comma-separated locale string back to a JSON array string. */
function serializeLocales(value: string): string {
  const codes = value
    .split(',')
    .map((c) => c.trim())
    .filter((c) => c !== '')
  return JSON.stringify(codes.length > 0 ? codes : ['__all__'])
}

export function CompanyProfileSection() {
  const [description, setDescription] = useState('')
  const [locales, setLocales] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)

  const load = useCallback(() => {
    void getNamespaceSettings('company')
      .then((entries) => {
        setDescription(valueFor(entries, 'description') ?? '')
        setLocales(parseLocales(valueFor(entries, 'name_locales')))
      })
      .catch((err: unknown) => {
        log.error('load company profile failed', { error: sanitizeForLog(getErrorMessage(err)) })
      })
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    void Promise.resolve().then(load)
  }, [load])

  const handleSave = useCallback(() => {
    setSaving(true)
    void Promise.all([
      updateSetting('company', 'description', { value: description.trim() }),
      updateSetting('company', 'name_locales', { value: serializeLocales(locales) }),
    ])
      .then(() => {
        setDirty(false)
        useToastStore.getState().add({ variant: 'success', title: 'Company profile saved' })
      })
      .catch((err: unknown) => {
        log.error('save company profile failed', { error: sanitizeForLog(getErrorMessage(err)) })
        useToastStore.getState().add({
          variant: 'error',
          ...getCrudErrorTitle(err, 'Could not save company profile'),
          description: getErrorMessage(err),
        })
      })
      .finally(() => setSaving(false))
  }, [description, locales])

  return (
    <SectionCard title="Company Profile" icon={Building2}>
      <div className="max-w-xl space-y-5">
        <InputField
          label="Description"
          multiline
          rows={3}
          value={description}
          disabled={loading}
          onChange={(e) => {
            setDescription(e.target.value)
            setDirty(true)
          }}
          hint="Short description of the organisation."
        />
        <InputField
          label="Name Locales"
          value={locales}
          disabled={loading}
          onChange={(e) => {
            setLocales(e.target.value)
            setDirty(true)
          }}
          placeholder="__all__"
          hint="Comma-separated Faker locales for agent names (e.g. en_GB, fr_FR), or __all__."
        />
        <Button onClick={handleSave} disabled={saving || loading || !dirty}>
          {saving && <Loader2 className="mr-2 size-4 animate-spin" />}
          Save Profile
        </Button>
      </div>
    </SectionCard>
  )
}
