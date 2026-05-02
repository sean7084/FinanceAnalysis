/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  server: {
    host: '0.0.0.0', // 这会监听所有地址
    port: 5173,       // 可指定端口，默认为 5173
    strictPort: true  // 若端口被占用则报错，避免自动切换[reference:1]
  },
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
  },
})
