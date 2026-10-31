import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// 构建产物输出到 frontend/dist/（FastAPI 静态托管 + 打进 .app）
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) }
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    assetsDir: 'assets'
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8791', changeOrigin: true },
      '/assets': { target: 'http://127.0.0.1:8791', changeOrigin: true }
    }
  }
})
