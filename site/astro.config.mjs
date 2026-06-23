import { defineConfig, fontProviders } from "astro/config";
import tailwindcss from "@tailwindcss/vite";
import sitemap from "@astrojs/sitemap";
import react from "@astrojs/react";

// Latin subset the self-hosted woff2 files are pre-subsetted to. Kept as the
// unicode-range so the browser only fetches a face when a glyph is in range.
const LATIN_SUBSET = [
  "U+0000-00FF", "U+0131", "U+0152-0153", "U+02BB-02BC", "U+02C6", "U+02DA",
  "U+02DC", "U+0304", "U+0308", "U+0329", "U+2000-206F", "U+20AC", "U+2122",
  "U+2191", "U+2193", "U+2212", "U+2215", "U+FEFF", "U+FFFD",
];

// `display: "optional"` keeps zero CLS: the browser uses the system fallback
// for the session if the face is not cached within ~100ms, with no swap.
const localFamily = (name, cssVariable, file, weight) => ({
  provider: fontProviders.local(),
  name,
  cssVariable,
  display: "optional",
  options: {
    variants: [
      {
        src: [`./src/assets/fonts/${file}`],
        weight,
        style: "normal",
        unicodeRange: LATIN_SUBSET,
      },
    ],
  },
});

export default defineConfig({
  vite: {
    plugins: [tailwindcss()],
  },
  site: "https://synthorg.io",
  integrations: [sitemap(), react()],
  fonts: [
    localFamily("Inter", "--font-inter", "inter-latin.woff2", "400 700"),
    localFamily("Geist", "--font-geist", "geist-latin.woff2", "400 700"),
    localFamily("Geist Mono", "--font-geist-mono", "geist-mono-latin.woff2", "400 500"),
    localFamily("JetBrains Mono", "--font-jetbrains-mono", "jetbrains-mono-latin.woff2", "400 500"),
  ],
  // Docs live at /docs (served by Zensical build output merged in CI)
  // Landing page is everything else
  build: {
    assets: "_assets",
    inlineStylesheets: "auto",
  },
  // Strict Content Security Policy, emitted as a <meta http-equiv> on every
  // page (the only header-free mechanism that works on GitHub Pages, our
  // production host). Astro auto-hashes every bundled + inline script/style, so
  // no 'unsafe-inline' is needed. The only third-party runtime dependency is
  // reCAPTCHA v3 (google.com + gstatic.com); GitHub buttons.js was removed.
  // NOTE: any new external or inline script must be registered here, or it will
  // be blocked. frame-ancestors / X-Frame-Options cannot be set via <meta> and
  // are delivered as response headers on the Cloudflare previews (public/_headers);
  // GitHub Pages cannot send them. CSP applies in `astro build`/`preview`, not dev.
  security: {
    csp: {
      directives: [
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self' https://www.google.com https://formcarry.com",
        "frame-src https://www.google.com",
        "form-action https://formcarry.com",
        "upgrade-insecure-requests",
      ],
      scriptDirective: {
        // 'self' (bundled JS) + reCAPTCHA; Astro appends per-script hashes.
        resources: ["'self'", "https://www.google.com", "https://www.gstatic.com"],
      },
      styleDirective: {
        // 'self' (bundled CSS); Astro appends per-style hashes. reCAPTCHA's
        // invisible v3 badge lives in a google.com iframe (frame-src), so no
        // style allowance is needed for it here.
        resources: ["'self'"],
      },
    },
  },
});
