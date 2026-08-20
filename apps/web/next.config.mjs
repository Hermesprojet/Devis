/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  reactStrictMode: true,
  // The API is a separate service; the browser talks to it directly with the
  // bearer token. No server-side proxy, so no place for the token to leak into
  // Next.js logs.
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000/api/v1',
  },
}

export default nextConfig
