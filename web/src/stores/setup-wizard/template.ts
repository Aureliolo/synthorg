import { listTemplates } from '@/api/endpoints/setup'
import { createLogger } from '@/lib/logger'
import { getErrorMessage } from '@/utils/errors'
import type { SliceCreator, TemplateSlice } from './types'

const log = createLogger('setup-wizard:template')
const MAX_COMPARE = 3

export const createTemplateSlice: SliceCreator<TemplateSlice> = (set, get) => ({
  templates: [],
  templatesLoading: false,
  templatesError: null,
  selectedTemplate: null,
  blankSelected: false,
  comparedTemplates: [],
  templateVariables: {},

  async fetchTemplates() {
    set({ templatesLoading: true, templatesError: null })
    try {
      const templates = await listTemplates()
      set({ templates, templatesLoading: false })
    } catch (err) {
      log.error('fetchTemplates failed:', getErrorMessage(err))
      set({ templatesError: getErrorMessage(err), templatesLoading: false })
    }
  },

  selectTemplate(name) {
    set({ selectedTemplate: name, blankSelected: false })
  },

  selectBlank() {
    set({ selectedTemplate: null, blankSelected: true })
  },

  toggleCompare(name) {
    const { comparedTemplates } = get()
    if (comparedTemplates.includes(name)) {
      set({ comparedTemplates: comparedTemplates.filter((n) => n !== name) })
      return true
    }
    if (comparedTemplates.length >= MAX_COMPARE) return false
    set({ comparedTemplates: [...comparedTemplates, name] })
    return true
  },

  clearComparison() {
    set({ comparedTemplates: [] })
  },

  setTemplateVariable(key, value) {
    set((s) => ({ templateVariables: { ...s.templateVariables, [key]: value } }))
  },
})
