import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The UI only reads. In development every API call is proxied to the FastAPI container.
const api = process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8100'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5273,
    strictPort: true,
    proxy: { '/api': api, '/health': api },
  },
})
