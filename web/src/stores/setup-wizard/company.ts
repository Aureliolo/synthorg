import { createCompany } from '@/api/endpoints/setup'
import { createLogger } from '@/lib/logger'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { getErrorCode, getErrorMessage } from '@/utils/errors'
import type { CompanySlice, SliceCreator } from './types'

const log = createLogger('setup-wizard:company')

export const createCompanySlice: SliceCreator<CompanySlice> = (set, get) => ({
  companyName: '',
  companyDescription: '',
  currency: DEFAULT_CURRENCY,
  budgetCapEnabled: false,
  budgetCap: null,
  companyResponse: null,
  companyLoading: false,
  companyError: null,
  companyErrorCode: null,

  setCompanyName(name) {
    set({ companyName: name })
  },

  setCompanyDescription(desc) {
    set({ companyDescription: desc })
  },

  setCurrency(currency) {
    set({ currency })
  },

  setBudgetCapEnabled(enabled) {
    set({ budgetCapEnabled: enabled })
  },

  setBudgetCap(cap) {
    set({ budgetCap: cap })
  },

  async submitCompany() {
    // Single in-flight guard: a programmatic re-entry (or a click
    // landing in the React render-commit window between when the
    // button's ``disabled`` flips and when the state observer sees it)
    // would otherwise issue a second POST /setup/company that lands
    // 409 or duplicates the template.
    if (get().companyLoading) return
    const { companyName, companyDescription, selectedTemplate } = get()
    set({ companyLoading: true, companyError: null, companyErrorCode: null })
    try {
      const response = await createCompany({
        company_name: companyName.trim(),
        description: companyDescription.trim() || null,
        template_name: selectedTemplate,
      })
      set({
        companyResponse: response,
        agents: [...response.agents],
        companyLoading: false,
      })
    } catch (err) {
      log.error('submitCompany failed:', getErrorMessage(err))
      set({
        companyError: getErrorMessage(err),
        companyErrorCode: getErrorCode(err),
        companyLoading: false,
      })
    }
  },

  clearCompanyError() {
    // No-op while a submit is in flight: a long-running submit that
    // completes after the user navigates away must still be allowed to
    // land its error in the store for the next CompanyStep mount to
    // surface. Clearing on mount instead would wipe such errors before
    // the user could see them.
    if (get().companyLoading) return
    set({ companyError: null, companyErrorCode: null })
  },
})
