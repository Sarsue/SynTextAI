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
    // 5173, Vite's own default, because the container holds 3000 and serves the
    // API there. This defaulted to 3000, which is the one port the dev server
    // can never have while the stack it proxies to is running: starting it
    // collided with Colima's forwarder every time, and tooling that reads this
    // file for the port concluded the dev server wanted a busy port and
    // refused. Overridable with VITE_DEV_PORT either way.
    port: Number(process.env.VITE_DEV_PORT ?? 5173),
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
