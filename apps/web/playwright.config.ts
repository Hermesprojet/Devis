import { defineConfig, devices } from '@playwright/test'
import { execSync } from 'node:child_process'
import { existsSync, mkdtempSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

/**
 * End-to-end configuration.
 *
 * The suite drives the real product: a real API on a real database, and the
 * built Next.js application — not a mocked client. That is the point. A green
 * unit suite says the pieces work; only this says the screens do.
 *
 * The database is a throwaway SQLite file, migrated and seeded before the
 * servers start, so a run never depends on what a previous one left behind.
 */

const API_PORT = 8011
const WEB_PORT = 3011
const REPO_ROOT = join(__dirname, '..', '..')
const API_DIR = join(REPO_ROOT, 'apps', 'api')

const databaseFile = join(mkdtempSync(join(tmpdir(), 'metreo-e2e-')), 'e2e.sqlite3')
const DATABASE_URL = `sqlite+pysqlite:///${databaseFile}`

const python = process.env.METREO_PYTHON ?? join(REPO_ROOT, '.venv', 'bin', 'python')

/** Path of a Chromium already present in the image, or undefined. */
function findPreinstalledChromium(): string | undefined {
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH
  if (!root || !existsSync(root)) return undefined
  for (const entry of readdirSync(root)) {
    if (!entry.startsWith('chromium-')) continue
    const candidate = join(root, entry, 'chrome-linux', 'chrome')
    if (existsSync(candidate)) return candidate
  }
  return undefined
}

const preinstalledChromium = findPreinstalledChromium()

const apiEnvironment = {
  METREO_DATABASE_URL: DATABASE_URL,
  METREO_ENVIRONMENT: 'test',
  METREO_AUTH_MODE: 'dev',
  METREO_JWT_SECRET: 'e2e-secret-not-used-anywhere-else-0123456789',
  METREO_CORS_ORIGINS: `http://127.0.0.1:${WEB_PORT}`,
  PYTHONPATH: join(API_DIR, 'src'),
}

const shellEnvironment = Object.entries(apiEnvironment)
  .map(([key, value]) => `${key}=${JSON.stringify(value)}`)
  .join(' ')

// Migrate and seed before anything is served: the specs assert on the demo
// data, so an empty database would fail them for the wrong reason.
execSync(
  `${shellEnvironment} ${python} -m alembic -c ${join(API_DIR, 'alembic.ini')} upgrade head`,
  { cwd: API_DIR, stdio: 'inherit' },
)
execSync(`${shellEnvironment} ${python} -m metreo_api.seed`, { cwd: API_DIR, stdio: 'inherit' })

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  // The specs share one database and one seeded organisation; running them
  // against each other would make failures depend on ordering.
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['list']] : [['list']],
  timeout: 30_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        // Some development images ship a Chromium whose build number does not
        // match the pinned @playwright/test. Point at it when it is there
        // rather than downloading a second copy; CI installs its own with
        // `npx playwright install --with-deps chromium` and this stays unset.
        ...(preinstalledChromium ? { launchOptions: { executablePath: preinstalledChromium } } : {}),
      },
    },
  ],
  webServer: [
    {
      command: `${shellEnvironment} ${python} -m uvicorn metreo_api.main:app --port ${API_PORT} --host 127.0.0.1`,
      cwd: API_DIR,
      url: `http://127.0.0.1:${API_PORT}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      // The build is part of the command on purpose: `NEXT_PUBLIC_*` is inlined
      // by Next.js at build time, so setting it only for `next start` would
      // serve a bundle still pointing at the default API URL — every call from
      // the browser would land nowhere and every spec would fail at sign-in.
      command: `npx next build && npx next start -p ${WEB_PORT} -H 127.0.0.1`,
      cwd: __dirname,
      url: `http://127.0.0.1:${WEB_PORT}`,
      reuseExistingServer: false,
      timeout: 240_000,
      env: { NEXT_PUBLIC_API_URL: `http://127.0.0.1:${API_PORT}/api/v1` },
    },
  ],
})
