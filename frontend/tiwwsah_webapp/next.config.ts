import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The repository root has its own package-lock.json (dev tooling), so tell Turbopack this
  // folder is the app's root instead of letting it guess.
  turbopack: {
    root: path.resolve(__dirname),
  },
  allowedDevOrigins: ["10.0.0.16"],
  experimental: {
    agentFeedback: true,
  },
  cacheComponents: true,
  partialPrefetching: true,
};

export default nextConfig;
