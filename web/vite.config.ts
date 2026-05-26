import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'
import { readFile, rm } from 'node:fs/promises'
import path from 'node:path'

/**
 * Project root (directory containing this config file).  Using
 * ``import.meta.url`` is the standard ESM replacement for the
 * CommonJS ``__dirname`` global; ``"type": "module"`` in
 * ``package.json`` means ``__dirname`` is not defined in the
 * module scope.  Derive the path once and reuse it for every
 * file-system lookup in this config.
 */
const PROJECT_ROOT = fileURLToPath(new URL('.', import.meta.url))

/** Vendor chunk groups for production bundle splitting. */
const VENDOR_CHUNKS: Record<string, readonly string[]> = {
  'vendor-react': ['react', 'react-dom', 'react-router'],
  'vendor-ui': ['@base-ui/react', 'cmdk-base', 'class-variance-authority', 'clsx', 'tailwind-merge', 'lucide-react'],
  'vendor-charts': ['recharts'],
  'vendor-flow': ['@xyflow/react', '@dagrejs/dagre', 'd3-force'],
  'vendor-editor': ['@codemirror/commands', '@codemirror/lang-json', '@codemirror/lang-yaml', '@codemirror/language', '@codemirror/state', '@codemirror/view'],
  'vendor-motion': ['motion'],
  'vendor-dnd': ['@dnd-kit/core', '@dnd-kit/sortable', '@dnd-kit/utilities'],
  'vendor-state': ['zustand', 'axios'],
} as const

function manualChunks(id: string): string | undefined {
  if (!id.includes('node_modules')) return undefined
  for (const [chunk, packages] of Object.entries(VENDOR_CHUNKS)) {
    if (packages.some((pkg) => id.includes(`node_modules/${pkg}`))) {
      return chunk
    }
  }
  return undefined
}

export default defineConfig(async () => {
  // Stamp the bundle with a build identifier so the client can detect
  // upgrades and clear stale cookies / localStorage on boot. Uses the
  // package.json version as the stable base; CI can override with
  // SYNTHORG_BUILD_ID (e.g. a git SHA) for finer granularity.
  const pkg = JSON.parse(
    await readFile(path.resolve(PROJECT_ROOT, 'package.json'), 'utf-8'),
  )
  const buildId = process.env.SYNTHORG_BUILD_ID ?? pkg.version

  const plugins = [
    react(),
    tailwindcss(),
    {
      name: 'remove-msw-worker',
      apply: 'build' as const,
      closeBundle: async () => {
        await rm(path.resolve(PROJECT_ROOT, 'dist', 'mockServiceWorker.js'), { force: true })
      },
    },
  ]

  if (process.env.VITE_ANALYZE) {
    const { visualizer } = await import('rollup-plugin-visualizer')
    plugins.push(visualizer({ filename: 'stats.html', open: true }) as ReturnType<typeof react>)
  }

  return {
    plugins,
    define: {
      'import.meta.env.VITE_APP_BUILD_ID': JSON.stringify(buildId),
    },
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: 'http://localhost:3001',
          changeOrigin: true,
          ws: true,
        },
      },
    },
    preview: {
      port: 4173,
      strictPort: true,
    },
    build: {
      // Never inline font files as data: URIs. Vite's default 4096 B threshold
      // inlines small unicode-range subsets (e.g. JetBrains Mono cyrillic-ext
      // at 2 KB), producing @font-face { src: url(data:font/woff2;base64,...) }
      // rules that violate the Caddy CSP (font-src 'self'; no data:). Inlined
      // fonts also bloat the render-blocking CSS bundle and can't be cached
      // independently of it.
      assetsInlineLimit: (filePath: string): boolean | undefined =>
        /\.(woff2?|ttf|otf|eot)$/i.test(filePath) ? false : undefined,
      rollupOptions: {
        output: {
          manualChunks,
        },
      },
    },
  }
})
