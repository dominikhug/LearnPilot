import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: true,
    // In development the API runs separately; in production FastAPI serves the build.
    proxy: { '/api': 'http://localhost:8000' },
  },
})
