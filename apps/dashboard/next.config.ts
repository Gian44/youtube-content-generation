import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: 'standalone',
  outputFileTracingExcludes: {
    '*': [
      '**/apps/worker/data/**/*',
      '**/*.db',
    ],
  },
};

export default nextConfig;
