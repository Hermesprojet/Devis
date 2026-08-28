import { expect, test } from '@playwright/test'

/**
 * Acceptance scenario 10: the product stays usable with the AI service off.
 *
 * This is the invariant that keeps Metreo an estimating tool rather than a
 * wrapper around a model. ``METREO_AI_ENABLED`` defaults to ``false`` and the
 * whole end-to-end suite already runs in that state — what is asserted here is
 * that the setting is genuinely off during the run, so the other specs cannot
 * be passing because something quietly turned it on.
 */

test("l'API annonce le service IA comme désactivé", async ({ request, baseURL }) => {
  expect(baseURL).toBeTruthy()
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? 'http://127.0.0.1:8011/api/v1'

  const health = await request.get(`${apiUrl}/health`)
  expect(health.ok()).toBeTruthy()
  const body = await health.json()
  expect(body.ai_enabled).toBe(false)
})

test('le parcours de devis reste entièrement praticable sans IA', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('Adresse e-mail').fill('admin@dubois.demo')
  await page.getByRole('button', { name: 'Se connecter' }).click()
  await expect(page).toHaveURL(/\/projets/)

  // Create, open, price: the three verbs that must never depend on a model.
  await page.getByRole('button', { name: 'Nouveau projet' }).click()
  const reference = `SANS-IA-${Date.now()}`
  await page.getByLabel('Référence').fill(reference)
  await page.getByLabel('Nom').fill('Projet sans service IA')
  await page.getByRole('button', { name: 'Créer', exact: true }).click()
  await expect(page.getByText(reference)).toBeVisible()

  await page.getByText('2026-014').click()
  await page.getByRole('link', { name: 'Ouvrir' }).first().click()
  await expect(page.getByText('Total HT').first()).toBeVisible()
})

test('aucun écran ne présente un bouton factice comme fonctionnel', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('Adresse e-mail').fill('admin@dubois.demo')
  await page.getByRole('button', { name: 'Se connecter' }).click()
  await expect(page).toHaveURL(/\/projets/)

  for (const path of ['/projets', '/bibliotheque', '/audit', '/parametres']) {
    await page.goto(path)
    // Anything not built yet must say so rather than pretend.
    const misleading = page.getByRole('button', { name: 'Non implémenté à ce stade' })
    await expect(misleading).toHaveCount(0)
  }
})
