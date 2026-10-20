// @lovable.dev/vite-tanstack-config already includes the following — do NOT add them manually
// or the app will break with duplicate plugins:
//   - TanStack devtools (dev-only, first), tanstackStart, viteReact, tailwindcss, tsConfigPaths,
//     nitro (build-only using cloudflare as a default target), VITE_* env injection, @ path alias,
//     React/TanStack dedupe, error logger plugins, and sandbox detection (port/host/strictPort).
// You can pass additional config via defineConfig({ vite: { ... }, etc... }) if needed.
import { defineConfig } from "@lovable.dev/vite-tanstack-config";
import { loadEnv } from "vite";

export function resolveApiProxyTarget(env: Record<string, string | undefined> = process.env): string {
  return env.VITE_API_PROXY_TARGET || env.API_PROXY_TARGET || "http://127.0.0.1:8000";
}

const loadedEnv = loadEnv(process.env.NODE_ENV || "development", process.cwd(), "");
const apiProxyTarget = resolveApiProxyTarget({ ...loadedEnv, ...process.env });

export default defineConfig({
  // The application is hosted as static files in S3. TanStack Start's SPA
  // entry supplies the client-side router without publishing a Nitro server.
  nitro: false,
  tanstackStart: {
    // Redirect TanStack Start's bundled server entry to src/server.ts (our SSR error wrapper).
    // nitro/vite builds from this
    server: { entry: "server" },
    spa: { enabled: true },
  },
  vite: {
    server: {
      proxy: {
        "/api": {
          target: apiProxyTarget,
          changeOrigin: true,
        },
      },
    },
  },
});
