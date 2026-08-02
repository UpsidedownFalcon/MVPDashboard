import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev server proxies /api and /ws to a locally running api service so
// `npm run dev` works against the real backend. Production is built static
// and served by Caddy, which does the same proxying (deploy/Caddyfile).
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          echarts: ['echarts'],
          react: ['react', 'react-dom', 'react-router-dom', '@tanstack/react-query'],
          uplot: ['uplot'],
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
})
