/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  reactStrictMode: true,
  // L'API est un service distinct ; le navigateur lui parle directement avec
  // son jeton. Pas de proxy côté serveur, donc aucun endroit où le jeton
  // pourrait fuiter dans les journaux de Next.
  //
  // ATTENTION — cette valeur est FIGÉE DANS LE JAVASCRIPT au moment du build.
  // Elle n'est pas lue à l'exécution : définir NEXT_PUBLIC_API_URL sur un
  // conteneur déjà construit n'a aucun effet, le code compilé porte déjà
  // l'ancienne. Une image se construit donc par environnement, via
  // l'ARG du Dockerfile — voir infra/web.Dockerfile et docs/ARCHITECTURE.md.
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000/api/v1',
  },
}

export default nextConfig
