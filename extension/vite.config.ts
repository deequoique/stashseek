import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: { input: ["index.html", "workspace.html", "sidepanel.html"] },
    outDir: "dist",
    emptyOutDir: false,
  },
});
