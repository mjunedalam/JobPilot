import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Dev: the UI runs on :5173 and proxies /api to the FastAPI server on :8765.
// Prod: `npm run build` writes web/dist, which FastAPI serves at / on the same origin.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8765" } },
  build: { outDir: "dist", sourcemap: true },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/test/setup.ts"],
  },
});
