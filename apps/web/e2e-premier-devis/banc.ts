/**
 * Le banc du parcours « première organisation ».
 *
 * Il diffère de `playwright.config.ts` sur trois points, et ces trois points
 * SONT le test :
 *
 *  1. la base est migrée puis passée au SEUL `bootstrap` — aucun jeu de
 *     démonstration, aucune insertion SQL. L'organisation n'a ni taux de
 *     taxe, ni bibliothèque, ni projet, ni second collaborateur ;
 *  2. l'authentification est en mode `oidc`, contre le faux fournisseur du
 *     dépôt. C'est le chemin de production, pas la connexion de
 *     développement ;
 *  3. les serveurs sont démarrés ici plutôt que par `webServer`, parce qu'un
 *     scénario doit pouvoir les REDÉMARRER pour éprouver que le devis
 *     survit au redémarrage.
 *
 * L'état du banc est écrit dans un fichier JSON à un chemin déterministe :
 * les workers Playwright sont des processus distincts de la mise en place, et
 * une variable de module ne leur parviendrait pas.
 */

import { spawn } from 'node:child_process'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

export const PORT_API = 8041
export const PORT_FOURNISSEUR = 8042
export const PORT_WEB = 3041

/**
 * Une pile DÉJÀ debout, à éprouver telle quelle.
 *
 * La répétition de préproduction monte la vraie composition Docker et veut y
 * jouer ce parcours-ci : sans cette variable, elle devrait redire le scénario
 * dans un second fichier, qui divergerait du premier. Quand elle est posée,
 * ce module ne migre rien, n'amorce rien, ne construit rien et ne démarre
 * rien — il se contente d'attendre que la pile réponde.
 */
export const PILE_EXTERNE = process.env.METREO_BANC_URL?.replace(/\/$/, '') || null

export const BASE_WEB = PILE_EXTERNE ?? `http://127.0.0.1:${PORT_WEB}`
export const BASE_API = PILE_EXTERNE ? `${PILE_EXTERNE}/api/v1` : `http://127.0.0.1:${PORT_API}/api/v1`

/** L'organisation que `bootstrap` crée, et la seule personne qu'elle contient. */
export const ORGANISATION = process.env.METREO_BANC_ORGANISATION ?? 'Entreprise neuve'
export const ADMIN = process.env.METREO_BANC_ADMIN ?? 'admin@neuve.example'

/** Où déposer les identifiants du devis produit, pour l'appelant qui en a besoin. */
export const CONSTAT = process.env.METREO_BANC_CONSTAT || null

/** Idem pour la pièce jointe : la répétition la relit après restauration. */
export const CONSTAT_DOCUMENTS = process.env.METREO_BANC_CONSTAT_DOCUMENTS || null

const RACINE_DEPOT = join(__dirname, '..', '..', '..')
const DOSSIER_API = join(RACINE_DEPOT, 'apps', 'api')
const DOSSIER_WEB = join(RACINE_DEPOT, 'apps', 'web')
const DOSSIER_BANC = join(tmpdir(), 'metreo-premier-devis')
const ETAT = join(DOSSIER_BANC, 'banc.json')
const BASE_SQLITE = join(DOSSIER_BANC, 'premier-devis.sqlite3')

const python = process.env.METREO_PYTHON ?? join(RACINE_DEPOT, '.venv', 'bin', 'python')

type Etat = { api: number; fournisseur: number; web: number }

const ENVIRONNEMENT_API: Record<string, string> = {
  METREO_DATABASE_URL: `sqlite+pysqlite:///${BASE_SQLITE}`,
  METREO_ENVIRONMENT: 'test',
  METREO_AUTH_MODE: 'oidc',
  METREO_JWT_SECRET: 'premier-devis-jetable-0123456789',
  METREO_CORS_ORIGINS: BASE_WEB,
  METREO_OIDC_ISSUER: `http://127.0.0.1:${PORT_FOURNISSEUR}`,
  METREO_OIDC_CLIENT_ID: 'metreo-premier-devis',
  METREO_OIDC_CLIENT_SECRET: 'jetable-sans-valeur-hors-de-ce-banc',
  // L'adresse de l'APPLICATION : c'est le navigateur qui revient, et il
  // revient sur une page, pas sur l'API.
  METREO_OIDC_REDIRECT_URI: `${BASE_WEB}/`,
  // Les sources du dépôt, et non seulement le paquet installé : la recette
  // doit éprouver ce que porte l'arbre de travail.
  PYTHONPATH: [
    join(DOSSIER_API, 'src'),
    join(RACINE_DEPOT, 'packages', 'domain', 'src'),
    join(RACINE_DEPOT, 'packages', 'contracts', 'src'),
  ].join(':'),
}

/** Un Chromium déjà présent dans l'image, s'il y en a un. */
export function chromiumPreinstalle(): string | undefined {
  const racine = process.env.PLAYWRIGHT_BROWSERS_PATH
  if (!racine || !existsSync(racine)) return undefined
  for (const entree of readdirSync(racine)) {
    if (!entree.startsWith('chromium-')) continue
    const candidat = join(racine, entree, 'chrome-linux', 'chrome')
    if (existsSync(candidat)) return candidat
  }
  return undefined
}

async function attendre(url: string, secondes = 90): Promise<void> {
  const limite = Date.now() + secondes * 1000
  let dernier = ''
  while (Date.now() < limite) {
    try {
      const reponse = await fetch(url)
      if (reponse.ok) return
      dernier = `HTTP ${reponse.status}`
    } catch (erreur) {
      dernier = String(erreur)
    }
    await new Promise((resoudre) => setTimeout(resoudre, 500))
  }
  throw new Error(`${url} n'a pas répondu en ${secondes}s (${dernier})`)
}

function lancer(commande: string, arguments_: string[], options: { cwd: string; env?: Record<string, string> }): number {
  const enfant = spawn(commande, arguments_, {
    cwd: options.cwd,
    env: { ...process.env, ...(options.env ?? {}) },
    // Son propre groupe de processus : on le tuera en entier, sinon
    // `next start` laisse derrière lui le serveur qu'il a engendré et le port
    // reste pris au redémarrage suivant.
    detached: true,
    stdio: 'ignore',
  })
  enfant.unref()
  if (enfant.pid === undefined) throw new Error(`${commande} n'a pas démarré`)
  return enfant.pid
}

function tuer(pid: number | undefined): void {
  if (!pid) return
  try {
    process.kill(-pid, 'SIGTERM')
  } catch {
    // Déjà parti : c'est le résultat recherché.
  }
}

function lireEtat(): Etat | null {
  try {
    return JSON.parse(readFileSync(ETAT, 'utf8')) as Etat
  } catch {
    return null
  }
}

function demarrerApi(): number {
  return lancer(
    python,
    ['-m', 'uvicorn', 'metreo_api.main:app', '--port', String(PORT_API), '--host', '127.0.0.1'],
    { cwd: DOSSIER_API, env: ENVIRONNEMENT_API },
  )
}

function demarrerFournisseur(): number {
  return lancer(
    python,
    [
      '-m',
      'metreo_api.dev_oidc_provider',
      `--port=${PORT_FOURNISSEUR}`,
      '--host=127.0.0.1',
      `--issuer=http://127.0.0.1:${PORT_FOURNISSEUR}`,
      '--client-id=metreo-premier-devis',
    ],
    // Le fournisseur refuse de démarrer hors `development`/`test` : c'est un
    // outil de recette, et il accepte n'importe quelle adresse sans mot de
    // passe.
    { cwd: DOSSIER_API, env: { ...ENVIRONNEMENT_API, METREO_ENVIRONMENT: 'test' } },
  )
}

function demarrerWeb(): number {
  return lancer('npx', ['next', 'start', '-p', String(PORT_WEB), '-H', '127.0.0.1'], {
    cwd: DOSSIER_WEB,
  })
}

/**
 * Redémarre l'API et le web, sans toucher à la base.
 *
 * Le seul moyen honnête de vérifier qu'un devis gelé est écrit quelque part
 * plutôt que gardé en mémoire d'un processus.
 */
export async function redemarrerApiEtWeb(): Promise<void> {
  if (PILE_EXTERNE) {
    throw new Error(
      'Ce banc ne pilote pas une pile externe : son redémarrage appartient à qui l’a montée.',
    )
  }
  const etat = lireEtat()
  tuer(etat?.api)
  tuer(etat?.web)
  await new Promise((resoudre) => setTimeout(resoudre, 1500))
  const api = demarrerApi()
  const web = demarrerWeb()
  writeFileSync(ETAT, JSON.stringify({ ...(etat as Etat), api, web }))
  await attendre(`${BASE_API}/health`)
  await attendre(BASE_WEB)
}

/** Ce que la base contient juste après le bootstrap — et donc ce qu'elle NE contient PAS. */
function verifierBaseVierge(): void {
  const compte = execFileSync(
    python,
    [
      '-c',
      [
        'import sqlite3, json, sys',
        'c = sqlite3.connect(sys.argv[1])',
        'tables = ["organizations","users","memberships","tax_rates","price_books",',
        '          "price_book_versions","price_items","projects","bills_of_quantities","estimates"]',
        'print(json.dumps({t: c.execute(f"select count(*) from {t}").fetchone()[0] for t in tables}))',
      ].join('\n'),
      BASE_SQLITE,
    ],
    { encoding: 'utf8' },
  )
  const lignes = JSON.parse(compte) as Record<string, number>
  const attendu: Record<string, number> = {
    organizations: 1,
    users: 1,
    memberships: 1,
    tax_rates: 0,
    price_books: 0,
    price_book_versions: 0,
    price_items: 0,
    projects: 0,
    bills_of_quantities: 0,
    estimates: 0,
  }
  const ecarts = Object.entries(attendu)
    .filter(([table, valeur]) => lignes[table] !== valeur)
    .map(([table, valeur]) => `${table}: ${lignes[table]} au lieu de ${valeur}`)
  if (ecarts.length > 0) {
    throw new Error(
      'La base de départ n\'est pas celle d\'une organisation neuve. Le parcours ne prouverait ' +
        `rien s'il partait de données déjà présentes : ${ecarts.join(', ')}`,
    )
  }
}

export default async function preparer(): Promise<void> {
  if (PILE_EXTERNE) {
    // Rien à monter : on vérifie seulement que la pile est là avant de
    // reprocher au produit ce qui serait un défaut de banc.
    await attendre(`${BASE_API}/health`)
    await attendre(BASE_WEB)
    return
  }
  rmSync(DOSSIER_BANC, { recursive: true, force: true })
  mkdirSync(DOSSIER_BANC, { recursive: true })

  const shell = Object.entries(ENVIRONNEMENT_API)
    .map(([cle, valeur]) => `${cle}=${JSON.stringify(valeur)}`)
    .join(' ')

  // Les migrations, et RIEN d'autre : `metreo_api.seed` n'est jamais appelé.
  execFileSync('sh', ['-c', `${shell} ${python} -m alembic -c ${join(DOSSIER_API, 'alembic.ini')} upgrade head`], {
    cwd: DOSSIER_API,
    stdio: 'inherit',
  })
  execFileSync(
    'sh',
    [
      '-c',
      `${shell} ${python} -m metreo_api.bootstrap --organization ${JSON.stringify(ORGANISATION)} ` +
        `--admin-email ${ADMIN} --admin-name "Alice Admin"`,
    ],
    { cwd: DOSSIER_API, stdio: 'inherit' },
  )
  verifierBaseVierge()

  // `NEXT_PUBLIC_API_URL` est figée à la compilation : un paquet construit
  // pour un autre port appellerait une API qui n'écoute pas ici.
  execFileSync('npx', ['next', 'build'], {
    cwd: DOSSIER_WEB,
    stdio: 'inherit',
    env: { ...process.env, NEXT_PUBLIC_API_URL: BASE_API },
  })

  const fournisseur = demarrerFournisseur()
  const api = demarrerApi()
  const web = demarrerWeb()
  writeFileSync(ETAT, JSON.stringify({ api, fournisseur, web } satisfies Etat))

  await attendre(`http://127.0.0.1:${PORT_FOURNISSEUR}/.well-known/openid-configuration`)
  await attendre(`${BASE_API}/health`)
  await attendre(BASE_WEB)
}

export async function ranger(): Promise<void> {
  if (PILE_EXTERNE) return
  const etat = lireEtat()
  tuer(etat?.api)
  tuer(etat?.fournisseur)
  tuer(etat?.web)
  rmSync(DOSSIER_BANC, { recursive: true, force: true })
}
