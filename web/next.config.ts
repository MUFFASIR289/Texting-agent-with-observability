import type { NextConfig } from "next";

/* The UI holds the only public port. Everything under /api is forwarded to the
 * FastAPI service on the loopback, so the API, its schema and its docs are
 * reachable at the same address as the console - and the service itself is not
 * reachable from anywhere else.
 *
 * The console does not use this route. Its own calls happen server-side and go
 * straight to the service; the rewrite is for the browser, curl and /api/docs. */

const API = `http://127.0.0.1:${process.env.API_INTERNAL_PORT ?? 8001}`;

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/:path*` }];
  },
};

export default nextConfig;
