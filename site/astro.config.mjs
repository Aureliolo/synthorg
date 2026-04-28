import { defineConfig } from "astro/config";
import tailwindcss from "@tailwindcss/vite";
import sitemap from "@astrojs/sitemap";
import react from "@astrojs/react";

export default defineConfig({
  vite: {
    plugins: [tailwindcss()],
    // @tailwindcss/vite 4.2.3+ spreads vite.config.resolve into a rolldown
    // resolver-plugin config; rolldown rc.17 requires the tsconfigPaths field.
    // Populating it here satisfies the napi binding (no path aliases declared).
    resolve: { tsconfigPaths: true },
  },
  site: "https://synthorg.io",
  integrations: [sitemap(), react()],
  // Docs live at /docs (served by Zensical build output merged in CI)
  // Landing page is everything else
  build: {
    assets: "_assets",
    inlineStylesheets: "auto",
  },
});
