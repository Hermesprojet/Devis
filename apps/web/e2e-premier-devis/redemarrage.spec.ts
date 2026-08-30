import { expect, test } from '@playwright/test'

import { ADMIN, PILE_EXTERNE, redemarrerApiEtWeb } from './banc'

/**
 * Le devis gelé survit-il à un redémarrage des serveurs ?
 *
 * C'est ce qui sépare « le service répond » de « les données sont écrites
 * quelque part ». Le scénario suit le parcours principal et relit ce qu'il a
 * produit ; il ne crée rien.
 *
 * Sauté quand la pile ne nous appartient pas : redémarrer les conteneurs
 * d'autrui appartient à qui les a montés — la répétition de préproduction le
 * fait elle-même, avec `docker compose restart`.
 */
test.skip(PILE_EXTERNE !== null, "le redémarrage appartient à qui a monté la pile")

test('le premier devis survit au redémarrage de l’API et du web', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: /compte de l'entreprise/ }).click()
  await page.locator('#email').fill(ADMIN)
  await page.getByRole('button', { name: 'Se connecter' }).click()
  await page.waitForURL(/\/projets$/)

  await page.getByRole('link', { name: 'PREM-001' }).click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}$/)
  await page.getByRole('link', { name: 'Ouvrir' }).first().click()
  await page.waitForURL(/\/estimations\//)
  const urlDevis = page.url()
  const avant = await page.locator('table.totals').innerText()
  expect(avant).toContain('23080.10')

  await redemarrerApiEtWeb()

  // Pas de reconnexion : la session doit survivre telle quelle. Un jeton qui
  // cesserait de valoir après un redémarrage trahirait un état gardé en
  // mémoire de processus — et deux instances derrière un répartiteur
  // déconnecteraient les gens au hasard.
  await page.goto(urlDevis)
  await expect(page.getByText('Gelée', { exact: true })).toBeVisible()
  // Les mêmes nombres, au caractère près : un devis remis au client ne change
  // pas parce qu'un processus a redémarré.
  expect(await page.locator('table.totals').innerText()).toBe(avant)
})
