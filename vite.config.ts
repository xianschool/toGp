import { alphaTab } from "@coderline/alphatab-vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [alphaTab()],
  clearScreen: false,
  server: {
    host: "127.0.0.1",
    port: 1420,
    strictPort: true,
  },
  build: {
    chunkSizeWarningLimit: 1500,
  },
});
