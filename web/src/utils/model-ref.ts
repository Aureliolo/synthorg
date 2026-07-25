import { createLogger } from '@/lib/logger'

const log = createLogger('model-ref')

export interface ModelRefValue {
  provider: string
  modelId: string
}

/**
 * Decode a stored ``MODEL_REF`` setting value into a provider/model pair.
 *
 * Accepts the canonical ``{provider, model_id}`` JSON the pickers write and a
 * bare model string (read as model-only, provider empty, so a picker prompts
 * for an explicit provider selection).
 */
export function decodeModelRef(value: string): ModelRefValue {
  const text = value.trim()
  if (!text) return { provider: '', modelId: '' }
  if (text.startsWith('{')) {
    try {
      const parsed: unknown = JSON.parse(text)
      if (typeof parsed === 'object' && parsed !== null) {
        const { provider, model_id: modelId } = parsed as Record<string, unknown>
        return {
          provider: typeof provider === 'string' ? provider : '',
          modelId: typeof modelId === 'string' ? modelId : '',
        }
      }
    } catch (err) {
      log.warn('decodeModelRef: value is not valid JSON, treating as model-only', err)
    }
  }
  return { provider: '', modelId: value }
}

/** Serialize a provider/model pair to the canonical stored ``MODEL_REF`` JSON. */
export function encodeModelRef(provider: string, modelId: string): string {
  return JSON.stringify({ provider, model_id: modelId })
}

/**
 * Re-encode any accepted ``MODEL_REF`` spelling into one canonical string.
 *
 * The backend serializes with ``json.dumps`` (which pads after ``:`` and
 * ``,``) while the dashboard uses ``JSON.stringify`` (which does not), so the
 * same logical reference reaches a picker as two different strings depending
 * on which side last wrote it. Both parse identically server-side, but a
 * picker that preselects by string identity would show no selection for the
 * other spelling, so every ref is normalised before it is compared.
 */
export function normalizeModelRef(value: string): string {
  const { provider, modelId } = decodeModelRef(value)
  if (!provider || !modelId) return value
  return encodeModelRef(provider, modelId)
}
