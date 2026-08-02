import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev proxy to the api container; production is served by Caddy which proxies
// the same paths (TRD §8), so the app always uses same-origin URLs.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
})
