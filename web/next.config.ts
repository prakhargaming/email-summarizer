import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // In production the ngrok Traffic Policy routes /api/* to FastAPI; locally we do it here.
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    return [{ source: "/api/:path*", destination: "http://localhost:8000/api/:path*" }];
  },
};

export default nextConfig;
