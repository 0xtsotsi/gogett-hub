import type { NextConfig } from "next";
import path from "node:path";

const devOrigins: string[] = [
  "localhost",
  "127.0.0.1",
  "127.0.0.2",
  "127.0.0.3",
  "127.0.1.1",
  "127.0.2.2",
  "127.0.2.3",
  "127.1",
  "127.0.0.1.nip.io",
  "127-0-0-1.sslip.io",
  "127-0-0-2.sslip.io",
  "127-0-0-3.sslip.io",
];

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL;
if (siteUrl) {
  try {
    devOrigins.push(new URL(siteUrl).hostname);
  } catch {}
}

const authUrl = process.env.NEXT_PUBLIC_AUTH_URL;
if (authUrl) {
  try {
    devOrigins.push(new URL(authUrl).hostname);
  } catch {}
}

const apiUrl = process.env.NEXT_PUBLIC_API_URL;
if (apiUrl) {
  try {
    devOrigins.push(new URL(apiUrl).hostname);
  } catch {}
}

// gogett: proxy SuperTokens + backend API routes to the upstream FastAPI.
// Without these rewrites the SDK POSTs to /st/auth/* land on Next.js's static
// catch-all and return HTML (404-equivalent). The upstream lemma-frontend image
// assumes this rewriting is wired up — locally we have to add it explicitly.
const BACKEND_INTERNAL_URL = process.env.BACKEND_INTERNAL_URL || "http://backend:8000";

const nextConfig: NextConfig = {
  allowedDevOrigins: devOrigins,
  output: "standalone",
  async rewrites() {
    return {
      // The CLI's bootstrap-info + refresh endpoints are backend-only
      // (/auth/cli/info, /auth/cli/refresh). The /auth/[[...path]] page
      // serves SuperTokens, including the CLI login screen at
      // /auth/cli/login (a React route). If we rewrite the whole /auth/cli/*
      // namespace here the login page would proxy to FastAPI and 404. So we
      // rewrite only the JSON endpoints; the login page must fall through
      // to the React portal page. Listing them in beforeFiles is still
      // needed so they beat the /auth/[[...path]] page match.
      beforeFiles: [
        // All CLI device-link endpoints live on the backend. Listing each
        // explicitly (instead of `/auth/cli/:path*`) keeps the CLI login page
        // at /auth/cli/login from accidentally proxying to FastAPI and 404'ing
        // -- that path is a React portal handled by the fallback /auth/[[...path]]
        // page below.
        { source: "/auth/cli/info", destination: `${BACKEND_INTERNAL_URL}/auth/cli/info` },
        { source: "/auth/cli/refresh", destination: `${BACKEND_INTERNAL_URL}/auth/cli/refresh` },
        { source: "/auth/cli/session-tokens", destination: `${BACKEND_INTERNAL_URL}/auth/cli/session-tokens` },
        // Empty-path forms of API resource roots. These MUST live in beforeFiles:
        // the chat UI has app/(chat)/pods/page.tsx and app/(chat)/organizations/...
        // page routes that return 405 on POST (no POST handler). Next.js's
        // `fallback` rewrites only run on 404 from the page router, so a 405 swallows
        // them. Putting these in beforeFiles overrides the page route entirely
        // for non-GET methods, letting POST/PATCH/DELETE reach the FastAPI backend.
        { source: "/users", destination: `${BACKEND_INTERNAL_URL}/users` },
        { source: "/organizations", destination: `${BACKEND_INTERNAL_URL}/organizations` },
        { source: "/pods", destination: `${BACKEND_INTERNAL_URL}/pods` },
      ],
      fallback: [
        // SuperTokens gateway path: SDK POSTs to /st/auth/* which the frontend
        // relays to the backend at /auth/* (backend mounts SuperTokens there).
        { source: "/st/auth/:path*", destination: `${BACKEND_INTERNAL_URL}/auth/:path*` },
        // Generic API passthrough so the same-origin SDK can reach the backend.
        { source: "/api/v1/:path*", destination: `${BACKEND_INTERNAL_URL}/api/v1/:path*` },
        { source: "/users/:path*", destination: `${BACKEND_INTERNAL_URL}/users/:path*` },
        { source: "/organizations/:path*", destination: `${BACKEND_INTERNAL_URL}/organizations/:path*` },
        { source: "/pods/:path*", destination: `${BACKEND_INTERNAL_URL}/pods/:path*` },
        // User daemon WebSocket: the CLI daemon's ggcoder/agent discovery +
        // run-stream round-trips use /me/agent-runtime/daemon/ws on the backend.
        // Without this rewrite the request hits the Next.js catch-all (404) and
        // the daemon stays "OFFLINE" forever in the Local harnesses panel.
        { source: "/me/agent-runtime/:path*", destination: `${BACKEND_INTERNAL_URL}/me/agent-runtime/:path*` },
        // Public daemon harness list (used by the chat UI's "Local harnesses"
        // picker to enumerate which harnesses this daemon can run).
        { source: "/agent-runtime/:path*", destination: `${BACKEND_INTERNAL_URL}/agent-runtime/:path*` },
      ],
    };
  },
  transpilePackages: ["lemma-sdk"],
  serverExternalPackages: ["esbuild"],
  turbopack: {
    root: path.resolve(process.cwd(), ".."),
  },
  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "logos.composio.dev",
      },
      {
        protocol: "https",
        hostname: "picsum.photos",
      },
    ],
    // Composio logos are SVGs; Next blocks SVG optimization unless explicitly enabled.
    dangerouslyAllowSVG: true,
    contentDispositionType: "attachment",
    contentSecurityPolicy: "default-src 'self'; script-src 'none'; sandbox;",
  },
};

export default nextConfig;
