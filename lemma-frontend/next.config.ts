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
    return [
      // SuperTokens gateway path: SDK POSTs to /st/auth/* which the frontend
      // relays to the backend at /auth/* (backend mounts SuperTokens there).
      { source: "/st/auth/:path*", destination: `${BACKEND_INTERNAL_URL}/auth/:path*` },
      // Generic API passthrough so the same-origin SDK can reach the backend.
      { source: "/api/v1/:path*", destination: `${BACKEND_INTERNAL_URL}/api/v1/:path*` },
      { source: "/users/:path*", destination: `${BACKEND_INTERNAL_URL}/users/:path*` },
      { source: "/organizations/:path*", destination: `${BACKEND_INTERNAL_URL}/organizations/:path*` },
      { source: "/pods/:path*", destination: `${BACKEND_INTERNAL_URL}/pods/:path*` },
    ];
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
