import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// In dev the browser talks to /api, proxied to the FastAPI server (no CORS needed locally).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      // The time-lapse demo runs its own cloud (its own database): only the Live cluster calls go there, the rest to the normal API.
      ...(process.env.VITE_LIVE_TARGET
        ? { '/api/sites': { target: process.env.VITE_LIVE_TARGET, rewrite: (p: string) => p.replace(/^\/api/, '') } }
        : {}),
      '/api': {
        target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000',
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
  test: { environment: 'node' },
})
