/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
//
// `base: '/static/'` aligns built asset URLs with Django's STATIC_URL so that
// WhiteNoise can serve them straight from STATIC_ROOT after `collectstatic`.
// It also means the dev server exposes every Vite-served module under the same
// `/static/` prefix, so django-vite's dev-mode URLs (which concatenate
// STATIC_URL + the asset path) resolve correctly against Vite.
//
// `server.origin` is required so that Django-rendered pages (served from :8000)
// still resolve the Vite dev-server URLs (HMR client, React Refresh preamble,
// module scripts) as absolute http://localhost:5173/... references when
// DJANGO_VITE_DEV_MODE=True.
//
// `build.rollupOptions.input` pins the entry to `src/main.tsx` instead of the
// default `index.html`. Without this, Vite keys the manifest entry as
// `index.html` and django-vite cannot resolve `src/main.tsx` in prod mode --
// see apps/templates/frontend/index.html which uses `{% vite_asset 'src/main.tsx' %}`.
// The dev server still serves frontend/index.html at `/` for the pure-Vite HMR
// flow (http://localhost:5173), because that behaviour is independent of
// rollupOptions.input.
//
// `build.manifest` writes dist/.vite/manifest.json, which django-vite reads to
// emit hashed asset tags in production mode.
export default defineConfig({
  base: '/static/',
  server: {
    host: '0.0.0.0', // 这会监听所有地址
    port: 5173,       // 可指定端口，默认为 5173
    strictPort: true,  // 若端口被占用则报错，避免自动切换[reference:1]
    origin: 'http://localhost:5173',
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    manifest: true,
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: fileURLToPath(new URL('./src/main.tsx', import.meta.url)),
      },
    },
  },
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
  },
})
