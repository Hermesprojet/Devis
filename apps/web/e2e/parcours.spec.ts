import { expect, test, type Page } from '@playwright/test'

import { API_BASE_URL } from '../playwright.config'

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

  // Trois chiffres, toujours : `Date.now() % 1000` rend parfois « 5 », le poste
  // devient « 9.5 », et l'assertion ci-dessous matche AUSSI l'option d'un prix
  // unitaire à 9,50. Vu en échec, environ une fois sur cent.
  const position = `9.${String(Date.now() % 1000).padStart(3, '0')}`
  // The form is always on screen; "Ajouter une ligne" is its heading, not a
  // button that reveals it.
  await expect(page.getByRole('heading', { name: 'Ajouter une ligne' })).toBeVisible()
  await page.getByLabel('Poste', { exact: true }).fill(position)
  await page.getByLabel('Désignation').fill('Ligne ajoutée par le test')
  await page.getByLabel('Unité').fill('m3')
  await page.getByLabel('Quantité', { exact: true }).fill('12')
  await page.getByRole('button', { name: 'Créer', exact: true }).click()

  // Une CELLULE, exactement : `getByText` cherche une sous-chaîne n'importe où
  // dans la page, listes déroulantes comprises.
  await expect(page.getByRole('cell', { name: position, exact: true })).toBeVisible()
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

// -- permissions ----------------------------------------------------------

/**
 * Ce test précède la décision de masquer selon les permissions, et il ne
 * distingue pas les deux causes d'absence : la permission manquante, et la
 * version déjà gelée par un test antérieur de ce fichier. Il est conservé
 * comme garde de non-régression, mais ce sont les trois tests par rôle en fin
 * de fichier qui prouvent quelque chose sur les permissions.
 */
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

// -- la barre d'outils suit les permissions du rôle ------------------------

/**
 * Décision : une commande que le rôle ne pourra jamais exécuter est masquée ;
 * une commande autorisée mais bloquée par l'état de l'objet reste visible et
 * désactivée, avec l'explication. L'API reste l'autorité — elle refuse
 * toujours — mais l'interface cesse de proposer ce qui ne peut pas aboutir.
 *
 * Avant, un lecteur voyait trois boutons dont les trois menaient à un 403, et
 * le message affiché était « Erreur HTTP 403 » sans dire quelle permission
 * manquait.
 */
const DEMO_VIEWER = 'lecteur@dubois.demo'

async function openFirstEstimate(page: Page): Promise<void> {
  await page.getByText('2026-014').click()
  await page.getByRole('link', { name: 'Ouvrir' }).first().click()
  await expect(page.getByRole('heading', { name: /Étude de prix/ })).toBeVisible()
}

test("un administrateur voit les trois commandes de la barre d'outils", async ({ page }) => {
  await signIn(page, DEMO_ADMIN)
  await openFirstEstimate(page)

  await expect(page.getByRole('button', { name: 'Export CSV' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Aperçu du devis' })).toBeVisible()

  /**
   * Le gel est présent, qu'il soit actionnable ou non — et c'est le point de
   * la décision. Un test antérieur de ce fichier gèle la version ; le bouton
   * apparaît alors désactivé, avec l'explication, au lieu de disparaître.
   *
   * Sans cette distinction, ce test passerait pour une mauvaise raison : le
   * bouton absent parce que la version est gelée se confondrait avec le
   * bouton absent parce que la permission manque. C'est exactement ce qui
   * arrivait au test du métreur ci-dessus, qui ne prouvait donc rien sur les
   * permissions.
   */
  const freeze = page.getByRole('button', { name: 'Geler cette version' })
  await expect(freeze).toHaveCount(1)
  if (await freeze.isDisabled()) {
    await expect(page.getByText(/déjà gelée/)).toBeVisible()
  }
})

test('un métreur exporte mais ne gèle pas', async ({ page }) => {
  await signIn(page, DEMO_ESTIMATOR)
  await openFirstEstimate(page)

  // `export:client` et `export:internal` : accordés au métreur.
  await expect(page.getByRole('button', { name: 'Export CSV' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Aperçu du devis' })).toBeVisible()
  // `estimate:freeze` : refusé. Masqué, pas désactivé — il ne l'obtiendra
  // jamais dans ce rôle, donc le montrer grisé n'apprendrait rien.
  await expect(page.getByRole('button', { name: 'Geler cette version' })).toHaveCount(0)
})

test('un lecteur ne se voit proposer aucune commande qui échouerait', async ({ page }) => {
  await signIn(page, DEMO_VIEWER)
  await openFirstEstimate(page)

  // Le lecteur a `estimate:read` : la page s'affiche, avec ses montants.
  // `getByText('Total HT')` viserait deux éléments — l'en-tête de colonne et
  // la ligne du pied — et Playwright refuse un locator ambigu.
  await expect(page.getByRole('cell', { name: 'Total HT' })).toBeVisible()

  // Il n'a ni `export:client`, ni `export:internal`, ni `estimate:freeze`.
  await expect(page.getByRole('button', { name: 'Export CSV' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Export interne' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Aperçu du devis' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Geler cette version' })).toHaveCount(0)
})

test("l'API refuse toujours, même si l'interface a cessé de proposer", async ({ request }) => {
  /**
   * Deuxième barrière. Masquer un bouton n'est pas une protection : ce test
   * appelle l'API directement avec le jeton du lecteur, sans passer par
   * l'interface, et vérifie que le refus est toujours là — avec la permission
   * manquante nommée.
   */
  const apiUrl = API_BASE_URL
  const login = await request.post(`${apiUrl}/auth/dev-login`, {
    data: { email: DEMO_VIEWER },
  })
  expect(login.ok()).toBeTruthy()
  const token = (await login.json()).access_token as string
  const headers = { Authorization: `Bearer ${token}` }

  const estimates = await request.get(`${apiUrl}/estimates`, { headers })
  const estimateId = (await estimates.json())[0].id as string
  const versions = await request.get(`${apiUrl}/estimates/${estimateId}/versions`, { headers })
  const versionId = (await versions.json())[0].id as string

  const refused = await request.get(
    `${apiUrl}/estimates/${estimateId}/versions/${versionId}/export.csv`,
    { headers },
  )
  expect(refused.status()).toBe(403)
  const detail = (await refused.json()).detail
  expect(detail.code).toBe('permission_denied')
  expect(detail.required_permission).toBe('export:client')
})
