import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Served by the Python backend under /app/ (same origin as /api/v1).
export default defineConfig({
  base: "/app/",
  plugins: [react()],
  build: { target: "es2020", sourcemap: false, chunkSizeWarningLimit: 400 },
  server: { proxy: { "/api": "http://localhost:8080" } },
});
