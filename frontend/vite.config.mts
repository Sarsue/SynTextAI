import path from 'path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    // Keep the same output directory CRA used, so the Dockerfile and
    // api/app.py's static-file mount don't need to change.
    outDir: 'build',
  },
  server: {
    // Overridable so a dev server can run beside the Docker stack rather than
    // instead of it: the container already holds port 3000 and serves the API
    // on it, so the automated sign-in harness runs Vite on 5173 and proxies to
    // the container. Defaults are unchanged for anyone running the API alone.
    port: Number(process.env.VITE_DEV_PORT ?? 3000),
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET ?? 'http://localhost:5000',
        changeOrigin: true,
        secure: false,
      },
      '/ws': {
        target: (process.env.VITE_API_TARGET ?? 'http://localhost:5000').replace(/^http/, 'ws'),
        ws: true,
        changeOrigin: true,
      },
    },
  },
});
