import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const rootDir = path.dirname(fileURLToPath(import.meta.url))

// Build into webapp/static so FastAPI keeps serving / without a path change.
export default defineConfig({
  plugins: [react()],
  base: '/',
  build: {
    outDir: path.resolve(rootDir, '../static'),
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
