import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Backend FastAPI runs on :5001 in development (see README). The proxy avoids
// CORS in dev. The legacy validator API lives at root paths (/policies, /claims,
// /rules, /documents) that are ALSO client-side routes, so browser page
// navigations (Accept: text/html) are served by the SPA and only API calls
// (fetch, Accept: application/json) are proxied. nginx does the same in production.
const BACKEND = process.env.VITE_BACKEND_URL || 'http://127.0.0.1:5001'
const apiOnly = {
  target: BACKEND,
  bypass: (req) => (req.headers.accept && req.headers.accept.includes('text/html') ? req.url : undefined),
}

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': BACKEND,
      '/health': BACKEND,
      '/policies': apiOnly,
      '/documents': apiOnly,
      '/rules': apiOnly,
      '/claims': apiOnly,
    },
  },
})
