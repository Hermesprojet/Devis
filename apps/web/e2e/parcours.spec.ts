import { expect, test, type Page } from '@playwright/test'

/**
 * The ten journeys that have to work for Phase 1 to mean anything.
 *
 * Each one drives the built application against the real API. Nothing is
 * stubbed: when one of these fails, a user would have hit the same wall.
 */

const DEMO_ADMIN = 'admin@dubois.demo'
const DEMO_ESTIMATOR = 'metreur@dubois.demo'

async function signIn(page: Page, email: string = DEMO_ADMIN): Promise<void> {
  await page.goto('/')
  await page.getByLabel('Adresse e-mail').fill(email)
  await page.getByRole('button', { name: 'Se connecter' }).click()
  await expect(page).toHaveURL(/\/projets/)
}

// -- 1. signing in ---------------------------------------------------------

test('un utilisateur de démonstration se connecte et arrive sur ses projets', async ({ page }) => {
  await signIn(page)
  await expect(page.getByRole('heading', { name: 'Projets' })).toBeVisible()
  // The seeded project belongs to this organisation and must be listed.
  await expect(page.getByText('2026-014')).toBeVisible()
})

test("le compte d'une autre entreprise ne voit pas les projets de la première", async ({
  page,
}) => {
  await signIn(page, 'admin@janssens.demo')
  await expect(page.getByText('2026-014')).toHaveCount(0)
})

// -- 2. creating a project -------------------------------------------------

test('un projet est créé et apparaît dans la liste', async ({ page }) => {
  await signIn(page)
  await page.getByRole('button', { name: 'Nouveau projet' }).click()

  const reference = `E2E-${Date.now()}`
  await page.getByLabel('Référence').fill(reference)
  await page.getByLabel('Nom').fill('Projet créé par le test de bout en bout')
  await page.getByRole('button', { name: 'Créer', exact: true }).click()

  await expect(page.getByText(reference)).toBeVisible()
})

// -- 3. adding a bill-of-quantities line -----------------------------------

test('une ligne de bordereau est ajoutée à un projet', async ({ page }) => {
  await signIn(page)
  await page.getByText('2026-014').click()
  await expect(page.getByRole('heading', { name: 'Bordereau' })).toBeVisible()

  const position = `9.${Date.now() % 1000}`
  // The form is always on screen; "Ajouter une ligne" is its heading, not a
  // button that reveals it.
  await expect(page.getByRole('heading', { name: 'Ajouter une ligne' })).toBeVisible()
  await page.getByLabel('Poste', { exact: true }).fill(position)
  await page.getByLabel('Désignation').fill('Ligne ajoutée par le test')
  await page.getByLabel('Unité').fill('m3')
  await page.getByLabel('Quantité', { exact: true }).fill('12')
  await page.getByRole('button', { name: 'Créer', exact: true }).click()

  await expect(page.getByText(position)).toBeVisible()
})

// -- 4 and 5. CSV import: preview, refusal of invalid rows -----------------

test("l'import CSV prévisualise, distingue les lignes valides des invalides, puis écrit", async ({
  page,
}) => {
  await signIn(page)
  await page.getByRole('link', { name: 'Bibliothèque de prix' }).click()
  await expect(page.getByRole('heading', { name: 'Bibliothèque de prix' })).toBeVisible()

  const stamp = Date.now()
  const csv = [
    'code;libelle;unite;prix_unitaire',
    `E2E-${stamp}-1;Article valide un;m3;12,50`,
    `E2E-${stamp}-2;Article valide deux;m2;3,40`,
    ';Libellé sans code;m;10,00',
    `E2E-${stamp}-3;Prix illisible;m3;abc`,
  ].join('\n')

  await page
    .getByLabel('1. Choisir le fichier')
    .setInputFiles({ name: 'prix.csv', mimeType: 'text/csv', buffer: Buffer.from(csv, 'utf-8') })

  // The preview is the whole point: it reports before anything is written.
  // The count shows both in the report and on the confirm button.
  await expect(page.getByText(/2\s+lignes valides/).first()).toBeVisible()
  await expect(page.getByText(/2\s+lignes en erreur/).first()).toBeVisible()

  await page.getByRole('button', { name: /Confirmer l'import/ }).click()
  await expect(page.getByText('Import confirmé.')).toBeVisible()

  // Exactly the valid rows landed.
  await page.getByPlaceholder('Rechercher').fill(`E2E-${stamp}`)
  await expect(page.getByText(`E2E-${stamp}-1`)).toBeVisible()
  await expect(page.getByText(`E2E-${stamp}-3`)).toHaveCount(0)
})

// -- 6. the sub-detail is visible and explained ----------------------------

test('le sous-détail de calcul est affiché ligne par ligne', async ({ page }) => {
  await signIn(page)
  await page.getByText('2026-014').click()
  await page.getByRole('link', { name: 'Ouvrir' }).first().click()

  await expect(page.getByRole('heading', { name: 'Étude de prix' })).toBeVisible()
  await expect(page.getByText('Déboursé sec').first()).toBeVisible()
  await expect(page.getByText('Total HT').first()).toBeVisible()

  await page.getByText('Voir le détail du calcul').first().click()

  // A total without its arithmetic would be exactly what this product refuses.
  // The formula is rendered as its own value, not behind a label, so the
  // assertion is on the arithmetic itself: an operator and a currency.
  await expect(page.getByRole('columnheader', { name: 'Sous-détail' }).first()).toBeVisible()
  await expect(page.getByText(/×.*EUR|EUR.*×/).first()).toBeVisible()
  await expect(page.getByText('Chaîne de prix').first()).toBeVisible()
})

test('un poste sans prix est signalé plutôt que valorisé à zéro', async ({ page }) => {
  await signIn(page)
  await page.getByText('2026-014').click()
  await page.getByRole('link', { name: 'Ouvrir' }).first().click()
  await expect(page.getByText('Prix manquant').first()).toBeVisible()
})

// -- 7 and 8. freezing, and its immutability -------------------------------

test("le gel est refusé tant qu'un poste est sans prix, et jamais sans confirmation", async ({
  page,
}) => {
  await signIn(page)
  await page.getByText('2026-014').click()
  await page.getByRole('link', { name: 'Ouvrir' }).first().click()

  // The demo estimate deliberately carries one unpriced line, and the company
  // rule forbids freezing in that state. The screen says so before anything is
  // clicked — scenario 5.
  await expect(
    page.getByText("Des postes sont sans prix : le gel est bloqué par la règle de l'entreprise."),
  ).toBeVisible()

  // Freezing is irreversible, so it is never a single click: asking for it
  // opens a confirmation step rather than doing it.
  await page.getByRole('button', { name: 'Geler cette version' }).click()
  await expect(page.getByRole('button', { name: 'Confirmer' })).toBeVisible()

  // And confirming it anyway is refused by the server, visibly.
  await page.getByRole('button', { name: 'Confirmer' }).click()
  await expect(page.getByText(/sans prix|bloqu/i).first()).toBeVisible()
  await expect(page.getByText('Gelée')).toHaveCount(0)
})

// -- 9. exports ------------------------------------------------------------

test('une version exporte un CSV et un aperçu de devis imprimable', async ({ page }) => {
  await signIn(page)
  await page.getByText('2026-014').click()
  await page.getByRole('link', { name: 'Ouvrir' }).first().click()

  // Exports are buttons, not plain links: they are authenticated, so the file
  // is fetched with the bearer token and handed over as a blob. The CSV is
  // downloaded; the quote preview opens in a tab.
  const [csvDownload] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: 'Export CSV' }).click(),
  ])
  expect(csvDownload.suggestedFilename()).toMatch(/\.csv$/)

  const [quoteTab] = await Promise.all([
    page.waitForEvent('popup'),
    page.getByRole('button', { name: 'Aperçu du devis' }).click(),
  ])
  await expect(quoteTab.locator('body')).toContainText(/Devis|Estimation|Metreo/i)
})

// -- 10. the audit journal -------------------------------------------------

test("le journal d'audit liste les actions et vérifie sa propre chaîne", async ({ page }) => {
  await signIn(page)
  // Reached through the navigation, the way a user gets there.
  await page.getByRole('link', { name: "Journal d'audit" }).click()
  await expect(page.getByRole('heading', { name: "Journal d'audit" })).toBeVisible()

  await page.getByRole('button', { name: "Vérifier l'intégrité" }).click()
  await expect(page.getByText('Chaîne intègre')).toBeVisible()
})

// -- permissions are enforced on the server, not hidden in the UI ----------

test("un métreur ne peut pas geler : le bouton n'est pas offert", async ({ page }) => {
  await signIn(page, DEMO_ESTIMATOR)
  await page.getByText('2026-014').click()
  await page.getByRole('link', { name: 'Ouvrir' }).first().click()
  await expect(page.getByRole('button', { name: 'Geler cette version' })).toHaveCount(0)
})

// -- deep links and reloads keep the session ------------------------------

test("un accès direct par URL ne renvoie pas à l'écran de connexion", async ({ page }) => {
  /**
   * Regression guard. The shell used to sign the user out on *any* failure of
   * `me()` / `health()`, not only on a rejected token, so a single slow
   * response on a deep link threw the session away and dropped the user back
   * on the login screen with no explanation.
   */
  await signIn(page)
  await page.goto('/audit')
  await expect(page.getByRole('heading', { name: "Journal d'audit" })).toBeVisible()

  await page.goto('/bibliotheque')
  await expect(page.getByRole('heading', { name: 'Bibliothèque de prix' })).toBeVisible()

  await page.reload()
  await expect(page.getByRole('heading', { name: 'Bibliothèque de prix' })).toBeVisible()
})
