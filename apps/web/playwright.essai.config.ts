/**
 * L'essai sur plans RÉELS, rejoué dans un vrai navigateur — jamais par la CI.
 *
 *     METREO_ESSAI_PLAN=~/.metreo-essai/plans/essai.json \
 *     METREO_ESSAI_CAPTURES=~/.metreo-essai/resultats/captures \
 *       npx playwright test --config=playwright.essai.config.ts
 *
 * **Il vise une pile déjà montée** par `ops/essai_local.sh up`, sur la boucle
 * locale : il ne migre rien, n'amorce rien, ne démarre rien.
 *
 * **Rien de ce qu'il produit n'entre dans le dépôt.** Les captures, les traces
 * d'échec et le PDF du devis montrent des morceaux de plans clients : ils vont
 * dans le dossier désigné par `METREO_ESSAI_CAPTURES`, et cette configuration
 * refuse de démarrer si ce dossier — ou le plan d'essai — est dans le dépôt.
 * C'est aussi pourquoi `outputDir` est déplacé : par défaut, Playwright écrit
 * ses traces dans `test-results/`, que la CI publie en artefact sur un échec.
 *
 * Aucune des configurations de CI ne le ramasse : elles lisent `./e2e` et
 * `./e2e-premier-devis`, celle-ci lit `./essai`.
 */
import { defineConfig, devices } from '@playwright/test'
import { existsSync, readdirSync } from 'node:fs'
import { join, resolve, sep } from 'node:path'

const RACINE_DEPOT = resolve(__dirname, '..', '..')

function horsDuDepot(variable: string): string {
  const valeur = process.env[variable]
  if (!valeur) {
    throw new Error(`${variable} est requise : le chemin PRIVÉ de l'essai, hors du dépôt.`)
  }
  const absolu = resolve(valeur)
  if (absolu === RACINE_DEPOT || absolu.startsWith(RACINE_DEPOT + sep)) {
    throw new Error(
      `${variable}=${absolu} est dans le dépôt. Les sorties de l'essai montrent des plans ` +
        'clients : désignez un dossier hors du dépôt.',
    )
  }
  return absolu
}

const CAPTURES = horsDuDepot('METREO_ESSAI_CAPTURES')
horsDuDepot('METREO_ESSAI_PLAN')

function chromium(): string | undefined {
  const racine = process.env.PLAYWRIGHT_BROWSERS_PATH
  if (!racine || !existsSync(racine)) return undefined
  for (const entree of readdirSync(racine)) {
    if (!entree.startsWith('chromium-')) continue
    const candidat = join(racine, entree, 'chrome-linux', 'chrome')
    if (existsSync(candidat)) return candidat
  }
  return undefined
}
const chrome = chromium()

export default defineConfig({
  testDir: './essai',
  outputDir: join(CAPTURES, '..', 'playwright-sorties'),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  timeout: 1_800_000,
  expect: { timeout: 60_000 },
  use: {
    baseURL: process.env.METREO_ESSAI_WEB ?? 'http://127.0.0.1:3071',
    viewport: { width: 1600, height: 1000 },
    actionTimeout: 60_000,
    navigationTimeout: 120_000,
    trace: 'off',
    screenshot: 'off',
    video: 'off',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1600, height: 1000 },
        ...(chrome ? { launchOptions: { executablePath: chrome } } : {}),
      },
    },
  ],
})
