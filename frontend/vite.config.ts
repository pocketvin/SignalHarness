import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  base: '/demo-assets/',
  plugins: [react(), tailwindcss()],
  build: {
    outDir: '../src/signal_harness/ui/static',
    emptyOutDir: false,
    cssCodeSplit: false,
    rollupOptions: {
      input: 'demo.html',
      output: {
        entryFileNames: 'demo.js',
        chunkFileNames: 'demo-[name].js',
        assetFileNames: (assetInfo) => assetInfo.names?.some((name) => name.endsWith('.css')) ? 'demo.css' : 'demo-[name][extname]'
      }
    }
  }
})
