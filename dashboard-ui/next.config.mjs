const apiOrigin = process.env.API_ORIGIN || "http://localhost:8000";

/** @type {import('next').NextConfig} */
const config = {
  output: "standalone",
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${apiOrigin}/api/v1/:path*` }];
  },
};

export default config;
