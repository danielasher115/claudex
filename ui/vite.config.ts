import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: "../codex_sessions/static", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8766" } },
  test: { environment: "jsdom" },
});
