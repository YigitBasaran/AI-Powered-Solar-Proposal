/** @type {import('next').NextConfig} */
const API_ORIGIN = process.env.API_BASE_URL ?? "http://localhost:8000";

const nextConfig = {
  reactStrictMode: true,
  // The satellite raster is proxied through this origin so the Konva stage
  // stays same-origin and can be exported to PNG without tainting the canvas.
  // It also keeps the Google Maps key server-side.
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${API_ORIGIN}/api/v1/:path*` }];
  },

  webpack: (config) => {
    // Konva ships a Node build that requires the optional native `canvas`
    // package. The stage is client-only (dynamic import with ssr: false), so
    // that path is never taken at runtime — but webpack still has to resolve
    // it. Marking it external keeps the build working without dragging in a
    // native dependency that would also break the Docker image.
    config.externals = [...(config.externals ?? []), { canvas: "canvas" }];
    return config;
  },
};

export default nextConfig;
