import { CSPProvider } from '@base-ui/react/csp-provider'
import { MotionConfig } from 'motion/react'
import { AppRouter } from '@/router'
import { getCspNonce } from '@/lib/csp'
import { DevAuthBootstrap } from '@/components/dev-auth-bootstrap'
import { ShortcutRegistryProvider } from '@/components/shortcut-registry-provider'

const nonce = getCspNonce()

export default function App() {
  return (
    <CSPProvider nonce={nonce}>
      <MotionConfig {...(nonce !== undefined ? { nonce } : {})}>
        <ShortcutRegistryProvider>
          <DevAuthBootstrap>
            <AppRouter />
          </DevAuthBootstrap>
        </ShortcutRegistryProvider>
      </MotionConfig>
    </CSPProvider>
  )
}
