/**
 * Le symbole d'une unité de poste, tel qu'un lecteur le lit : « m² » pour m2,
 * « m³ » pour m3. Le serveur l'écrit lui-même (`lisible.unite_affichee`) dans
 * `unit_lisible` ; cette table sert là où une réponse ne le porte pas encore —
 * un instantané de devis émis, une API plus ancienne.
 */
const SYMBOLES: Record<string, string> = { m2: 'm²', m3: 'm³' }

export function uniteLisible(code: string | null | undefined): string {
  if (!code) return ''
  return SYMBOLES[code] ?? code
}
