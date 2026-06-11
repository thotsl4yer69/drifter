import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// DRIFTER cockpit v4 — offline-first build.
// base: './' so the dist can be served from any path the dashboard chooses
// (e.g. /opt/drifter/ui/v4/ behind the stdlib http.server). All assets
// (fonts, JS, CSS) are bundled locally — NO CDN at runtime (brief §2.2).
export default defineConfig({
  base: './',
  plugins: [react()],
  build: {
    outDir: 'dist',
    assetsInlineLimit: 0,
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        // keep arsenal/map/vivi lazy chunks separate so the DRIVE surface
        // paints first (brief §2.3 / perf)
        manualChunks: undefined,
      },
    },
  },
});
