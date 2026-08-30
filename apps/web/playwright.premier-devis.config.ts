import { defineConfig, devices } from '@playwright/test'

import { BASE_WEB, chromiumPreinstalle } from './e2e-premier-devis/banc'

/**
 * Le parcours « une organisation vide produit son premier devis ».
 *
 * Une seconde configuration, et non un ajout à la première, parce que les
 * conditions de départ sont incompatibles : `playwright.config.ts` sème un jeu
 * de démonstration et sert l'API en mode `dev`, alors que ce parcours doit
 * partir d'une base migrée puis seulement bootstrapée, derrière une connexion
 * OpenID Connect. Les mêler ferait passer ce test pour les mauvaises raisons.
 *
 * Les deux configurations construisent dans le même `.next` : elles se lancent
 * l'une après l'autre, jamais en parallèle.
 */

const chromium = chromiumPreinstalle()

export default defineConfig({
  testDir: './e2e-premier-devis',
  globalSetup: './e2e-premier-devis/banc.ts',
  globalTeardown: './e2e-premier-devis/rangement.ts',
  fullyParallel: false,
  // Les scénarios se suivent dans une organisation unique, qui se remplit au
  // fil du parcours : les jouer en parallèle rendrait chaque échec dépendant
  // d'un ordre.
  workers: 1,
  forbidOnly: !!process.env.CI,
  // Aucune reprise : ce parcours part d'une base vierge et la remplit. Le
  // rejouer sur une base déjà remplie masquerait précisément le défaut qu'il
  // cherche.
  retries: 0,
  // Les scénarios se suivent : le rôle relit le devis que le parcours a
  // produit. Poursuivre après un échec ferait attendre deux minutes à chaque
  // scénario suivant pour un diagnostic qui est déjà tombé.
  maxFailures: 1,
  reporter: process.env.CI ? [['github'], ['list']] : [['list']],
  timeout: 120_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: BASE_WEB,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        ...(chromium ? { launchOptions: { executablePath: chromium } } : {}),
      },
    },
  ],
})
