import type { NextConfig } from "next";

// /api/* is proxied to FastAPI by app/api/[...path]/route.ts, which adds the signed token.
const nextConfig: NextConfig = {};

export default nextConfig;
