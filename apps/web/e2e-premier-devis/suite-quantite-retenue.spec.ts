import { expect, test } from '@playwright/test'

import { ADMIN } from './banc'
import { seConnecter } from './parcours'
import {
  calibrerSurLaCote,
  deposerEtLire,
  ligneDeMesure,
  LONGUEUR,
  mesurerLaFacade,
  SANS_JALON,
} from '../mesure-pdf/plan-connu'

/**
 * La quantité RETENUE : proposée à la finesse de la mesure, choisie dans le ±.
 *
 * **Le défaut que ce parcours ferme, trouvé sur un plan réel.** Une surface
 * confirmée valait 6,3787950927 m² ± 0,041 m², et c'est ce nombre à dix
 * décimales qui est parti au bordereau, à l'étude et sur le PDF du devis. Dix
 * décimales affirment une précision que la mesure n'a pas.
 *
 * Désormais : la mesure brute et son ± restent visibles ; le serveur propose
 * la quantité à la finesse du ± ; la personne peut en retenir une autre
 * écriture, DANS le ± — et le serveur refuse au-delà, en le disant. Le nombre
 * retenu est ensuite identique au bordereau, dans le calcul et sur le PDF.
 *
 * Sur la fixture, la façade mesure 6 000 mm à ± 1,3 mm près : en mètres, la
 * proposition est « 6 m », « 6 » est une écriture admise, et « 6,05 » n'en est
 * pas une — 50 mm d'écart pour 1,3 mm de ±.
 */

const CHANTIER = { reference: 'RET-001', nom: 'Quantité retenue dans le ± de la mesure' }
const POSTE = '91.10'
const DESIGNATION = 'Façade, quantité retenue'
const MOTIF = 'Cote contrôlée sur le plan : conforme.'

test('la quantité reprise est proposée à la finesse de la mesure, choisie dans le ±, et refusée au-delà', async ({
  page,
}) => {
  test.setTimeout(420_000)

  await seConnecter(page, ADMIN)

  await page.goto('/projets')
  await page
    .getByRole('button', { name: /nouveau projet/i })
    .first()
    .click()
  await page.getByLabel(/référence/i).fill(CHANTIER.reference)
  await page
    .getByLabel(/^nom/i)
    .first()
    .fill(CHANTIER.nom)
  await page
    .getByRole('button', { name: /^créer$/i })
    .first()
    .click()
  await page.getByRole('link', { name: CHANTIER.reference }).first().click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}$/)
  const urlChantier = page.url()

  // Un bordereau vide, créé avant le plan.
  await page
    .getByRole('button', { name: /^créer$/i })
    .first()
    .click()
  await expect(page.getByLabel('Poste')).toBeVisible()

  await deposerEtLire(page, 'Plan RDC — quantité retenue', SANS_JALON)
  await calibrerSurLaCote(page, SANS_JALON)
  await mesurerLaFacade(page, SANS_JALON)

  // ---- Confirmer la mesure : elle garde son ±, donc une proposition.
  const mesure = ligneDeMesure(page, LONGUEUR)
  await mesure.getByTestId('pdf-motif-decision').fill(MOTIF)
  await mesure.getByTestId('pdf-confirmer-mesure').click()
  await expect(ligneDeMesure(page, LONGUEUR).getByTestId('pdf-decision')).toContainText(
    'confirmée',
    { timeout: 60_000 },
  )

  // ---- La reprise : brute et ± visibles, proposition pré-remplie.
  const confirmee = ligneDeMesure(page, LONGUEUR)
  await confirmee.getByTestId('pdf-ouvrir-reprise').click()
  const formulaire = page.getByTestId('pdf-formulaire-reprise')
  await expect(formulaire).toBeVisible()
  await formulaire.getByTestId('pdf-reprise-unite').selectOption('m')

  const brute = page.getByTestId('pdf-apercu-brute')
  await expect(brute).toContainText('±', { timeout: 20_000 })
  await expect(brute).toContainText('m')

  const retenue = formulaire.getByTestId('pdf-reprise-quantite')
  await expect(retenue).toBeEnabled({ timeout: 20_000 })
  await expect(retenue).toHaveValue(/^\d/)
  const proposition = await retenue.inputValue()
  const apercu = page.getByTestId('pdf-apercu-quantite')
  await expect(apercu).toHaveText(`${proposition} m`)

  // ---- Hors du ± : refusé, en clair, et rien ne peut être écrit.
  await retenue.fill('6,05')
  await expect(formulaire.getByTestId('notice-erreur')).toContainText('corrigez la mesure', {
    timeout: 20_000,
  })
  await expect(formulaire.getByTestId('pdf-reprendre')).toBeDisabled()

  // ---- Dans le ± : « 6 » est une écriture de 6 000 mm ± 1,3 mm.
  await retenue.fill('6')
  await expect(apercu).toHaveText('6 m', { timeout: 20_000 })
  await expect(formulaire.getByTestId('notice-erreur')).toHaveCount(0)

  await formulaire.getByTestId('pdf-reprise-position').fill(POSTE)
  await formulaire.getByTestId('pdf-reprise-designation').fill(DESIGNATION)
  await expect(formulaire.getByTestId('pdf-reprendre')).toBeEnabled()
  await formulaire.getByTestId('pdf-reprendre').click()
  await expect(confirmee.getByTestId('pdf-reprise-faite')).toBeVisible({ timeout: 30_000 })

  // ---- Le bordereau porte exactement ce nombre, avec sa provenance.
  await page.goto(urlChantier)
  const ligne = page.locator('tr').filter({ hasText: POSTE }).first()
  await expect(ligne).toBeVisible({ timeout: 20_000 })
  await expect(ligne).toContainText('6 m')
  await expect(ligne).not.toContainText('6,0')
  await expect(ligne.getByTestId('boq-provenance')).toBeVisible()
})
