import { CSPProvider } from '@base-ui/react/csp-provider'
import { MotionConfig } from 'motion/react'
import { AppRouter } from '@/router'
import { getCspNonce } from '@/lib/csp'
import { ShortcutRegistryProvider } from '@/components/shortcut-registry-provider'

const nonce = getCspNonce()

export default function App() {
  return (
    <CSPProvider nonce={nonce}>
      <MotionConfig {...(nonce !== undefined ? { nonce } : {})}>
        <ShortcutRegistryProvider>
          <AppRouter />
        </ShortcutRegistryProvider>
      </MotionConfig>
    </CSPProvider>
  )
}
