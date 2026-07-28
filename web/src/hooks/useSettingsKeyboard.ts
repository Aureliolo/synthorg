import { useEffect } from 'react'
import { normalisedKey } from '@/utils/keyboard'

export interface UseSettingsKeyboardOptions {
  onSave: () => void
  onSearchFocus: () => void
  canSave: boolean
}

export function useSettingsKeyboard({
  onSave,
  onSearchFocus,
  canSave,
}: UseSettingsKeyboardOptions): void {
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.defaultPrevented || e.repeat) return
      const mod = e.metaKey || e.ctrlKey
      if (!mod) return

      if (normalisedKey(e) === 's') {
        e.preventDefault()
        if (canSave) onSave()
      } else if (e.key === '/') {
        e.preventDefault()
        onSearchFocus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onSave, onSearchFocus, canSave])
}
