/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
//
// `base` stays at its default '/'. Built asset URLs do NOT need a '/static/'
// base: django-vite resolves manifest paths through
// `staticfiles_storage.url()`, which prepends STATIC_URL itself, and
// `collectstatic` picks up frontend/dist via STATICFILES_DIRS. Setting base to
// '/static/' instead moved the whole dev server under that prefix, which broke
// the pure-Vite HMR flow -- createBrowserRouter has no basename, so opening
// http://<host>:5173/static/ rendered the router's 404 error boundary. The
// dev-mode URL scheme is handled Django-side by
// apps/core/vite_client.py (RootOriginDevClient).
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
