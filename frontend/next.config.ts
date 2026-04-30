import type { NextConfig } from "next";

const apiProxyTarget = process.env.API_PROXY_TARGET || "http://localhost:8000";
const proxyClientMaxBodySize = (process.env.NEXT_PROXY_CLIENT_MAX_BODY_SIZE || "200mb") as NonNullable<
  NextConfig["experimental"]
>["proxyClientMaxBodySize"];

const nextConfig: NextConfig = {
  experimental: {
    proxyClientMaxBodySize,
  },
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${apiProxyTarget}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
