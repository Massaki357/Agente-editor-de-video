import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Em dev, tudo que é da API passa pelo proxy (o código só usa caminhos relativos).
const API = process.env.API_URL ?? 'http://127.0.0.1:8000'
const proxy = {
  '/api': { target: API, changeOrigin: true },
  '/docs': { target: API, changeOrigin: true },
  '/openapi.json': { target: API, changeOrigin: true },
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy },
  preview: { proxy },
})
