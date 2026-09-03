import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The API and the static screenshot mount are proxied so the browser sees one
// origin. Without this every <img src="/files/..."> would need the API host
// baked into it, and the app would stop working the moment the port changed.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/files": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
