import { defineConfig } from 'vite';

export default defineConfig({
  base: '/admin/',
  server: {
    host: '0.0.0.0',
    proxy: { '/api': 'http://localhost:10000' },
  },
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false },
});
