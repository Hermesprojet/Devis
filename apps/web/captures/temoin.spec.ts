import { expect, test, type Download } from '@playwright/test'
import { createHash } from 'node:crypto'
import { mkdirSync, writeFileSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'

/**
 * Le PDF TÉMOIN du candidat : un devis à trois lignes dont chaque montant se
 * recalcule de tête, produit par l'application réelle dans un vrai navigateur.
 *
 *     01.10  120   × 18,50 = 2 220,00
 *     01.20   85   × 24,40 = 2 074,00
 *     01.30   42,5 × 31,20 = 1 326,00
 *                      HT  = 5 620,00
 *               TVA 21 %   = 1 180,20
 *                     TTC  = 6 800,20
 */

const SORTIE = process.env.CAPTURES_DIR ?? '/tmp/temoin'
mkdirSync(SORTIE, { recursive: true })

const LIGNES = [
  { position: '01.10', designation: 'Déblai en terrain meuble', unite: 'm3', quantite: '120', prix: '18.50', total: '2220.00' },
  { position: '01.20', designation: 'Remblai compacté par couches', unite: 'm3', quantite: '85', prix: '24.40', total: '2074.00' },
  { position: '01.30', designation: 'Évacuation en centre agréé', unite: 't', quantite: '42.5', prix: '31.20', total: '1326.00' },
]
const HT = '5620.00'
const TVA = '1180.20'
const TTC = '6800.20'

async function octets(d: Download): Promise<Buffer> {
  const chemin = await d.path()
  return chemin ? readFile(chemin) : Buffer.alloc(0)
}

test('le devis témoin, du bordereau au PDF, avec des montants qui tombent juste', async ({
  page,
  browser,
}) => {
  test.setTimeout(300_000)

  // Frais et marge à zéro AVANT tout : le prix de vente doit être le prix
  // saisi, sans quoi l'attendu ne serait plus calculable à la main. La chaîne
  // commerciale a ses propres tests ; ici elle brouillerait la démonstration.
  //
  // Par l'API, parce que l'écran des réglages montre ces taux sans les rendre
  // modifiables — et c'est voulu : une marge ne se change pas d'un clic.
  const API = process.env.TEMOIN_API ?? 'http://127.0.0.1:8055/api/v1'
  const connexion = await page.request.post(`${API}/auth/dev-login`, {
    data: { email: 'admin@dubois.demo' },
  })
  expect(connexion.ok(), await connexion.text()).toBeTruthy()
  const jeton = (await connexion.json()).access_token as string
  const regles = await page.request.patch(`${API}/organization/settings`, {
    headers: { Authorization: `Bearer ${jeton}` },
    data: {
      site_overheads_rate: '0',
      general_overheads_rate: '0',
      contingency_rate: '0',
      margin_rate: '0',
    },
  })
  expect(regles.ok(), await regles.text()).toBeTruthy()

  // Connexion à l'écran
  await page.goto('/')
  await page.getByLabel('Adresse e-mail').fill('admin@dubois.demo')
  await page.getByRole('button', { name: 'Se connecter' }).click()
  await expect(page).toHaveURL(/\/projets/)

  // La bibliothèque : trois prix aux valeurs voulues
  await page.goto('/bibliotheque')
  for (const ligne of LIGNES) {
    await page.getByRole('button', { name: 'Ajouter un prix' }).click()
    await page.getByLabel('Code', { exact: true }).fill(`TEM-${ligne.position}`)
    await page.getByLabel('Désignation').fill(ligne.designation)
    await page.getByLabel('Unité').selectOption(ligne.unite)
    await page.getByLabel(/Prix unitaire HT/).fill(ligne.prix)
    await page.getByRole('button', { name: 'Enregistrer le prix' }).click()
    await expect(page.getByText(`TEM-${ligne.position}`).first()).toBeVisible()
  }

  // Le client
  await page.goto('/clients')
  await page.getByRole('button', { name: 'Nouveau client' }).click()
  await page.getByLabel('Nom', { exact: true }).fill('Commune fictive de Jodoigne')
  await page.getByLabel(/Numéro d.entreprise/).fill('BE 0207.363.192')
  await page.getByLabel('Adresse de facturation').fill('Grand-Place 1')
  await page.getByLabel('Code postal').fill('1370')
  await page.getByLabel('Localité').fill('Jodoigne')
  await page.getByRole('button', { name: 'Enregistrer' }).click()
  await expect(page.getByRole('cell', { name: 'Commune fictive de Jodoigne' })).toBeVisible()

  // Le chantier et son bordereau
  await page.goto('/projets')
  await page.getByRole('button', { name: /nouveau projet/i }).first().click()
  await page.getByLabel(/référence/i).fill('TEM-2026-001')
  await page.getByLabel(/^nom/i).first().fill('Chantier témoin')
  await page.getByRole('button', { name: /^créer$/i }).first().click()
  await page.getByRole('link', { name: 'TEM-2026-001' }).first().click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}$/)
  const urlChantier = page.url()
  await page
    .getByTestId('selecteur-client')
    .selectOption({ label: 'Commune fictive de Jodoigne — Jodoigne' })
  await page.getByRole('button', { name: 'Rattacher' }).click()

  await page.getByRole('button', { name: /^créer$/i }).first().click()
  for (const ligne of LIGNES) {
    await page.getByLabel('Poste').fill(ligne.position)
    await page.getByLabel('Désignation').fill(ligne.designation)
    await page.getByLabel('Unité').fill(ligne.unite)
    await page.getByLabel('Quantité').fill(ligne.quantite)
    await page.locator('#source-nouveau').selectOption('library')
    const options = await page.locator('#prix-nouveau option').allInnerTexts()
    const cible = options.find((o) => o.startsWith(`TEM-${ligne.position}`))
    expect(cible, `prix TEM-${ligne.position} introuvable`).toBeTruthy()
    await page.locator('#prix-nouveau').selectOption({ label: cible as string })
    await page.getByRole('button', { name: /^créer$/i }).first().click()
    await expect(page.getByTestId(`prix-poste-${ligne.position}`)).toBeVisible()
  }

  // L'étude
  await page.goto(urlChantier)
  await page.getByRole('button', { name: 'Créer une étude de prix' }).click()
  await page.getByRole('link', { name: 'Ouvrir' }).first().click()
  await page.waitForURL(/\/estimations\//)
  await expect(page.getByRole('row', { name: `Total HT ${HT} EUR` })).toBeVisible({
    timeout: 30_000,
  })
  await page.screenshot({ path: join(SORTIE, 'temoin-chiffrage.png'), fullPage: true })

  // Gel puis émission
  await page.getByRole('button', { name: 'Geler cette version' }).click()
  await page.getByRole('button', { name: /confirmer/i }).click()
  await expect(page.getByText('Gelée', { exact: true })).toBeVisible()
  const emission = page.getByTestId('emission-du-devis')
  await emission.getByRole('button', { name: 'Émettre le devis' }).click()
  await page.getByLabel('Valable jusqu’au').fill('2027-06-30')
  await page.getByTestId('confirmer-l-emission').click()
  await expect(page.getByTestId('devis-emis')).toBeVisible({ timeout: 60_000 })
  const numero = (await page.getByTestId('numero-du-devis').innerText()).trim()

  const [telechargement] = await Promise.all([
    page.waitForEvent('download'),
    page.getByTestId('telecharger-le-devis').click(),
  ])
  const pdf = await octets(telechargement)
  const empreinte = createHash('sha256').update(pdf).digest('hex')
  writeFileSync(join(SORTIE, 'devis-temoin.pdf'), pdf)
  console.log(`TEMOIN numero=${numero} octets=${pdf.length} sha256=${empreinte}`)

  // Le lien client, et le même fichier de l'autre côté
  await page.goto('/devis-emis')
  await page.getByRole('link', { name: numero }).first().click()
  await page.waitForURL(/\/devis-emis\/[0-9a-f-]{36}/)
  await page.getByTestId('creer-le-lien').click()
  const url = await page.getByTestId('url-du-lien').inputValue()

  const contexteClient = await browser.newContext({ viewport: { width: 1200, height: 1000 } })
  const pageClient = await contexteClient.newPage()
  await pageClient.goto(url)
  await expect(pageClient.getByTestId('devis-public')).toBeVisible()
  await expect(pageClient.getByTestId('total-ttc-public')).toContainText(TTC)
  await pageClient.screenshot({ path: join(SORTIE, 'temoin-page-client.png'), fullPage: true })
  const pdfClient = await octets(
    (
      await Promise.all([
        pageClient.waitForEvent('download'),
        pageClient.getByTestId('telecharger-public').click(),
      ])
    )[0],
  )
  expect(createHash('sha256').update(pdfClient).digest('hex')).toBe(empreinte)
  console.log(`TEMOIN client identique=true`)
  await contexteClient.close()
})
