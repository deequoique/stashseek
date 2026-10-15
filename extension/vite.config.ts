import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: { input: "workspace.html" },
    outDir: "dist",
    emptyOutDir: false,
  },
});
