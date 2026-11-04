import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    environment: "jsdom",
    globals: true,
    // ChatArea mounts the whole store module graph; a single case measures
    // ~9s when the machine is busy, which is past the 5s default.
    testTimeout: 15000,
  },
});
