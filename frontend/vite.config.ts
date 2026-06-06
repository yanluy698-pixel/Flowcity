import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": process.env.FLOWCITY_API_TARGET ?? "http://localhost:8010",
      "/health": process.env.FLOWCITY_API_TARGET ?? "http://localhost:8010"
    }
  }
});
