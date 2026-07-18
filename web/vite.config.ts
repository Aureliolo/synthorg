import { defineConfig, type Plugin } from 'vite'
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
    // Match on the package-directory boundary (trailing slash) so a prefix
    // like ``react`` cannot swallow ``react-markdown`` into ``vendor-react``.
    if (packages.some((pkg) => id.includes(`node_modules/${pkg}/`))) {
      return chunk
    }
  }
  return undefined
}

const DOCS_MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
  '.ico': 'image/x-icon',
  '.xml': 'application/xml',
  '.txt': 'text/plain; charset=utf-8',
}

const DOCS_NOT_BUILT_HTML =
  '<!doctype html><meta charset="utf-8"><title>Docs not built</title>' +
  '<body style="font-family:system-ui;padding:2rem;background:#0b0f17;color:#e5e7eb">' +
  '<h1>Documentation not built</h1>' +
  '<p>In production Caddy serves the static docs at <code>/docs/</code>. ' +
  'To preview them in local dev, build the site first:</p>' +
  '<pre style="background:#111827;padding:1rem;border-radius:8px">PYTHONPATH=. uv run zensical build</pre>' +
  '<p>Then reload this page.</p></body>'

/**
 * Serve the built documentation site at ``/docs/`` during local dev.
 *
 * In production Caddy serves ``_site/docs`` at ``/docs/``; the Vite dev
 * server has no such mapping, so the Docs nav 404s. This middleware mirrors
 * the production mapping (and shows a build hint when the site is absent)
 * so the link works the same in dev. Mounted on ``/docs`` -- Connect strips
 * that prefix, so ``req.url`` is already relative to the docs root.
 */
function devDocsPlugin(): Plugin {
  const docsRoot = path.resolve(PROJECT_ROOT, '..', '_site', 'docs')
  return {
    name: 'synthorg-dev-docs',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use('/docs', (req, res) => {
        let rel: string
        try {
          rel = decodeURIComponent((req.url ?? '/').split('?')[0] ?? '/')
        } catch {
          // A malformed escape (e.g. a bare ``%``) must not crash the dev
          // server's middleware chain.
          res.statusCode = 400
          res.end('Bad Request')
          return
        }
        if (rel.endsWith('/')) rel += 'index.html'
        const filePath = path.join(docsRoot, path.normalize(rel))
        // Exact root or a true child only: a prefix-only check would let a
        // sibling dir sharing the prefix (``_site/docs-secret``) escape.
        if (filePath !== docsRoot && !filePath.startsWith(docsRoot + path.sep)) {
          res.statusCode = 403
          res.end('Forbidden')
          return
        }
        readFile(filePath)
          .then((data) => {
            res.setHeader(
              'Content-Type',
              DOCS_MIME[path.extname(filePath).toLowerCase()] ??
                'application/octet-stream',
            )
            res.end(data)
          })
          .catch(() => {
            res.statusCode = 404
            res.setHeader('Content-Type', 'text/html; charset=utf-8')
            res.end(DOCS_NOT_BUILT_HTML)
          })
      })
    },
  }
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
    devDocsPlugin(),
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
