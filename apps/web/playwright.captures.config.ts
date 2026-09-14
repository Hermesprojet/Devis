/**
 * Le parcours TÉMOIN, lancé à la demande — jamais par la CI.
 *
 *     CAPTURES_DIR=/tmp/temoin npx playwright test --config=playwright.captures.config.ts
 *
 * Il produit un devis dont chaque montant se recalcule de tête, en pilotant
 * l'application réelle dans un vrai navigateur, et en garde le PDF et les
 * captures. C'est la preuve qu'on montre à quelqu'un, là où les deux suites de
 * `e2e/` et `e2e-premier-devis/` sont les preuves qu'on exécute à chaque
 * livraison.
 *
 * Aucune des deux configurations de CI ne le ramasse : `playwright.config.ts`
 * lit `./e2e`, `playwright.premier-devis.config.ts` lit `./e2e-premier-devis`,
 * et ce fichier-ci lit `./captures`. Il faut le demander par `--config`.
 */
import { defineConfig, devices } from '@playwright/test'
import { execSync } from 'node:child_process'
import { existsSync, mkdtempSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const API_PORT = 8055
const WEB_PORT = 3055
const REPO_ROOT = join(__dirname, '..', '..')
const API_DIR = join(REPO_ROOT, 'apps', 'api')
const databaseFile = join(mkdtempSync(join(tmpdir(), 'metreo-captures-')), 'demo.sqlite3')
const DATABASE_URL = `sqlite+pysqlite:///${databaseFile}`
const python = process.env.METREO_PYTHON ?? join(REPO_ROOT, '.venv', 'bin', 'python')

function chromium(): string | undefined {
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH
  if (!root || !existsSync(root)) return undefined
  for (const entry of readdirSync(root)) {
    if (!entry.startsWith('chromium-')) continue
    const candidate = join(root, entry, 'chrome-linux', 'chrome')
    if (existsSync(candidate)) return candidate
  }
  return undefined
}
const chrome = chromium()

const apiEnvironment = {
  METREO_DATABASE_URL: DATABASE_URL,
  METREO_ENVIRONMENT: 'development',
  METREO_AUTH_MODE: 'dev',
  METREO_JWT_SECRET: 'captures-locales-sans-valeur-0123456789',
  METREO_CORS_ORIGINS: `http://127.0.0.1:${WEB_PORT}`,
  METREO_STORAGE_ROOT: join(tmpdir(), 'metreo-captures-storage'),
  PYTHONPATH: join(API_DIR, 'src'),
}
const shellEnvironment = Object.entries(apiEnvironment)
  .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
  .join(' ')

execSync(
  `${shellEnvironment} ${python} -m alembic -c ${join(API_DIR, 'alembic.ini')} upgrade head`,
  { cwd: API_DIR, stdio: 'inherit' },
)
execSync(`${shellEnvironment} ${python} -m metreo_api.seed`, { cwd: API_DIR, stdio: 'inherit' })

export default defineConfig({
  testDir: './captures',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  timeout: 300_000,
  expect: { timeout: 20_000 },
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    viewport: { width: 1440, height: 960 },
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 960 },
        ...(chrome ? { launchOptions: { executablePath: chrome } } : {}),
      },
    },
  ],
  webServer: [
    {
      command: `${shellEnvironment} ${python} -m uvicorn metreo_api.main:app --port ${API_PORT} --host 127.0.0.1`,
      cwd: API_DIR,
      url: `http://127.0.0.1:${API_PORT}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `npx next build && npx next start -p ${WEB_PORT} -H 127.0.0.1`,
      cwd: __dirname,
      url: `http://127.0.0.1:${WEB_PORT}`,
      reuseExistingServer: false,
      timeout: 300_000,
      env: { NEXT_PUBLIC_API_URL: `http://127.0.0.1:${API_PORT}/api/v1` },
    },
  ],
})
