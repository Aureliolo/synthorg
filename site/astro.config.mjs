import { defineConfig } from "astro/config";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  vite: {
    plugins: [tailwindcss()],
  },
  site: "https://synthorg.io",
  // Docs live at /docs (served by Zensical build output merged in CI)
  // Landing page is everything else
  build: {
    assets: "_assets",
  },
});
