import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = `http://127.0.0.1:${process.env.MERCURY_PORT ?? "8765"}`;

export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    // Bind IPv4 explicitly: "localhost" may resolve to ::1 only, which Electron/wait-on won't find.
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: backend, ws: true, changeOrigin: false },
    },
  },
});
