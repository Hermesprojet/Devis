import { readFileSync } from 'node:fs'

import { expect, test, type Download, type Page } from '@playwright/test'

import { ADMIN } from './banc'
import { seConnecter, texteDuPdf } from './parcours'
import { LONGUEUR, mesurerLePlanConnu, SURFACE, ligneDeMesure } from '../mesure-pdf/plan-connu'

/**
 * Du plan au PDF du devis, en cliquant : le parcours entier, sans raccourci.
 *
 * **Ce que ce scénario établit, et que rien d'autre n'établit.** La reprise
 * d'une mesure dans un bordereau existait côté serveur — une route, un service,
 * quinze tests — et nulle part à l'écran. Une avancée qu'on ne peut pas suivre
 * n'est pas une avancée : elle est invérifiable par la personne qui chiffre.
 *
 * Six gestes, et chacun est celui d'un métreur :
 *
 *   plan déposé → échelle déclarée → mesure pointée → décision humaine
 *     → reprise dans un bordereau → devis émis → PDF remis au client
 *
 * **Les deux refus comptent autant que la réussite.** Une mesure REJETÉE
 * n'offre aucune commande de reprise — pas un bouton grisé : rien. Et la même
 * mesure ne se reprend pas DEUX FOIS dans un même bordereau : le double
 * comptage est l'erreur la plus coûteuse d'un métré, parce que chaque ligne y
 * est juste et que seul le total est faux.
 *
 * **La quantité n'est jamais saisie.** Elle vient de la mesure et de la
 * décision qui l'a retenue. Le scénario vérifie donc qu'elle arrive au
 * bordereau, puis au calcul, puis au PDF, sans que personne ne l'ait tapée —
 * et que la valeur CORRIGÉE l'emporte sur la proposition de la machine.
 *
 * Ce fichier suit `suite-devis-client-pdf.spec.ts` dans l'ordre de la suite et
 * s'appuie sur ce qu'il a laissé : une fiche client réutilisable. Il crée son
 * PROPRE chantier, son propre bordereau et sa propre étude — ajouter une ligne
 * au bordereau du parcours principal changerait les totaux que trois autres
 * scénarios vérifient.
 */

const CHANTIER = { reference: 'REPR-001', nom: 'Reprise d’une mesure au bordereau' }

/** Le poste que la reprise crée, et le prix qui le chiffre. */
const POSTE = '90.10'
const DESIGNATION = 'Mur avant, repris du plan RDC'
const PRIX = { code: 'ML-MUR', label: 'Mur au mètre linéaire', unite: 'm', unitaire: '25.00' }

/**
 * La valeur CORRIGÉE par la personne, en mètres.
 *
 * `mesurerLePlanConnu` corrige la façade à 6 020 mm — « relevé sur place au
 * décamètre : 6,02 m et non 6,00 ». C'est donc 6,02 m que la reprise doit
 * écrire, et non les 6 001,2 mm que Metreo avait calculés. La différence est
 * tout l'objet de la décision humaine.
 */
const QUANTITE_ATTENDUE = '6,02'

/**
 * La même quantité, telle que le PDF du devis l'écrit.
 *
 * **Avec un POINT**, et c'est un constat, pas un choix de ce scénario : le
 * devis imprime « 6.02 », « 25.00 » et « 150.50 EUR ». L'écran, lui, écrit en
 * français — « 6,02 m ». Les deux écritures coexistent dans le produit livré,
 * et le parcours principal l'assertait déjà ainsi (`MONTANTS.totalHT` y vaut
 * `'23080.10'`).
 *
 * Ce test ne tranche pas la question : il constate ce qui est imprimé. La
 * trancher reviendrait à changer le document remis au client, et c'est une
 * décision qui ne se prend pas dans un fichier de test.
 */
const QUANTITE_DANS_LE_PDF = '6.02'

async function octets(telechargement: Download): Promise<Buffer> {
  const chemin = await telechargement.path()
  expect(chemin, 'le téléchargement doit avoir abouti sur un fichier').toBeTruthy()
  return readFileSync(chemin as string)
}

async function telechargerLeDevis(page: Page): Promise<Buffer> {
  const [fichier] = await Promise.all([
    page.waitForEvent('download'),
    page.getByTestId('telecharger-le-devis').click(),
  ])
  return octets(fichier)
}

/** Un prix au mètre linéaire, créé s'il manque — la bibliothèque n'en a pas. */
async function unPrixAuMetre(page: Page): Promise<void> {
  await page.goto('/bibliotheque')
  const creer = page.getByRole('button', { name: 'Créer la bibliothèque' })
  const ajouter = page.getByRole('button', { name: 'Ajouter un prix' })
  await expect(creer.or(ajouter)).toBeVisible({ timeout: 15_000 })
  if (await creer.count()) await creer.click()
  await expect(ajouter).toBeVisible({ timeout: 15_000 })
  if ((await page.getByText(PRIX.code).count()) > 0) return
  await ajouter.click()
  await page.getByLabel('Code', { exact: true }).fill(PRIX.code)
  await page.getByLabel('Désignation').fill(PRIX.label)
  await page.getByLabel('Unité').selectOption(PRIX.unite)
  await page.getByLabel(/Prix unitaire HT/).fill(PRIX.unitaire)
  await page.getByRole('button', { name: 'Enregistrer le prix' }).click()
  await expect(page.getByText(PRIX.code).first()).toBeVisible()
}

test('une mesure corrigée sur un plan devient une ligne de bordereau, puis un montant de devis, puis une ligne du PDF', async ({
  page,
}) => {
  // Le parcours enchaîne deux rendus de plan, quatre tuiles, un gel et une
  // émission. Chacun coûte des secondes, et la première tuile jusqu'à cinq.
  test.setTimeout(420_000)

  await seConnecter(page, ADMIN)
  await unPrixAuMetre(page)

  // ---- 1. le chantier, sa fiche client, son bordereau
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

  // La fiche client vient du scénario précédent : un devis s'adresse à
  // quelqu'un, et l'émission le refuse sans destinataire.
  await page.getByTestId('selecteur-client').selectOption({ index: 1 })
  await page.getByRole('button', { name: 'Rattacher' }).click()
  await expect(page.getByTestId('client-du-chantier')).toBeVisible()

  // Le bordereau, VIDE : c'est la reprise qui le remplira, et rien d'autre.
  await page
    .getByRole('button', { name: /^créer$/i })
    .first()
    .click()
  await expect(page.getByLabel('Poste')).toBeVisible()

  // ---- 2. le plan : déposé, calibré, mesuré, tranché
  //
  // Le même scénario que la vérification chiffrée, et c'est voulu : il laisse
  // la façade CORRIGÉE — donc reprenable — et la surface REJETÉE — donc
  // jamais reprenable. Les deux états dont ce fichier a besoin.
  const lues = await mesurerLePlanConnu(page, { libelle: 'Plan RDC — reprise' })
  expect(lues.longueurAffichee, 'la façade a bien été mesurée').toMatch(/mm/)

  // ---- 3. une mesure REJETÉE n'offre rien
  //
  // Pas un bouton grisé : rien. Un bouton désactivé laisserait chercher ce
  // qu'il faudrait faire pour l'activer, alors que la réponse est « rien » —
  // une mesure rejetée a été écartée, et elle ne reviendra pas.
  const rejetee = ligneDeMesure(page, SURFACE)
  await expect(rejetee.getByTestId('pdf-reprenable')).toHaveAttribute('data-reprenable', 'non')
  await expect(rejetee.getByTestId('pdf-ouvrir-reprise')).toHaveCount(0)

  // ---- 4. la mesure CORRIGÉE se reprend, et le nombre est MONTRÉ avant d'être écrit
  const corrigee = ligneDeMesure(page, LONGUEUR)
  await expect(corrigee.getByTestId('pdf-reprenable')).toHaveAttribute('data-reprenable', 'oui')
  await corrigee.getByTestId('pdf-ouvrir-reprise').click()

  const formulaire = page.getByTestId('pdf-formulaire-reprise')
  await expect(formulaire).toBeVisible()

  // L'unité du poste se choisit : la mesure est en millimètres, un métré se
  // lit en mètres. La conversion est faite par le SERVEUR, et le résultat
  // s'affiche avant toute écriture.
  await formulaire.getByTestId('pdf-reprise-unite').selectOption('m')
  const apercu = page.getByTestId('pdf-apercu-quantite')
  await expect(apercu).toContainText(QUANTITE_ATTENDUE, { timeout: 20_000 })
  await expect(apercu).toContainText('m')

  // Et la provenance dit d'où vient ce nombre : la page, et le fait qu'une
  // personne l'a corrigé. Sans elle, « 6,02 m » est un nombre sans auteur.
  const provenance = page.getByTestId('pdf-apercu-provenance')
  await expect(provenance).toContainText('page 1')
  await expect(provenance).toContainText('corrigée')

  await formulaire.getByTestId('pdf-reprise-position').fill(POSTE)
  await formulaire.getByTestId('pdf-reprise-designation').fill(DESIGNATION)
  await formulaire.getByTestId('pdf-reprendre').click()

  // L'écran dit où la mesure est partie, et offre d'y aller.
  const faite = corrigee.getByTestId('pdf-reprise-faite')
  await expect(faite).toBeVisible({ timeout: 30_000 })
  await expect(faite).toContainText(POSTE)

  // ---- 5. la MÊME mesure ne se reprend pas deux fois
  //
  // L'écran replie son formulaire après une reprise ; le refus, lui, vient du
  // serveur et doit se VOIR. On recharge donc la page — ce que ferait
  // quelqu'un qui revient dessus plus tard — et on recommence.
  await page.reload()
  const aNouveau = ligneDeMesure(page, LONGUEUR)
  await aNouveau.getByTestId('pdf-ouvrir-reprise').click()
  const second = page.getByTestId('pdf-formulaire-reprise')

  // Le formulaire rouvert repart sur l'unité de la MESURE — ici le millimètre —
  // et c'est correct : il n'a pas de mémoire, et le choix précédent appartient
  // à une ligne qui existe déjà. Ce qui est éprouvé ici n'est pas le nombre,
  // c'est le refus ; on attend donc que l'aperçu soit arrivé, sans supposer
  // dans quelle unité.
  await expect(second.getByTestId('pdf-reprendre')).toBeDisabled()
  await second.getByTestId('pdf-reprise-position').fill('90.20')
  await second.getByTestId('pdf-reprise-designation').fill('Le même mur, une seconde fois')
  await expect(second.getByTestId('pdf-reprendre')).toBeEnabled({ timeout: 20_000 })
  await second.getByTestId('pdf-reprendre').click()
  await expect(second.getByTestId('notice-erreur')).toContainText('déjà', { timeout: 20_000 })

  // ---- 6. la ligne est au bordereau, avec sa quantité, son unité et sa provenance
  await page.goto(urlChantier)
  const ligne = page.locator('tr').filter({ hasText: POSTE }).first()
  await expect(ligne).toBeVisible({ timeout: 20_000 })
  await expect(ligne).toContainText(DESIGNATION)
  await expect(ligne).toContainText(PRIX.unite)
  // La quantité, écrite par personne : elle vient de la décision humaine.
  await expect(ligne).toContainText('6.02')
  await expect(ligne.getByTestId('boq-provenance')).toBeVisible()

  // ---- 7. le prix, l'étude, le gel, l'émission
  // La colonne « Prix » est en LECTURE tant qu'on n'a pas demandé à en
  // changer : le poste affiche « sans prix », et les deux sélecteurs
  // n'existent pas encore dans le DOM. C'est ce que ce test attendait, et
  // c'est pourquoi il tombait sur un locator introuvable.
  await ligne.getByRole('button', { name: 'Changer' }).click()
  const source = page.getByTestId(`source-poste-${POSTE}`)
  await expect(source).toBeVisible()

  // Le prix est désigné par son CODE, et l'option est retrouvée par son texte
  // avant d'être choisie par sa valeur. Choisir par index attraperait le
  // premier venu — la bibliothèque en porte deux — et un prix au mètre CUBE
  // sur un poste au mètre linéaire ferait échouer le calcul sur une
  // incompatibilité de dimension, loin d'ici. Choisir par libellé exact
  // dépendrait, lui, de l'écriture du prix unitaire, que l'API rend sous sa
  // forme canonique.
  await source.locator('select').first().selectOption('library')
  const choixDuPrix = source.locator('select').nth(1)
  const option = choixDuPrix.locator('option', { hasText: PRIX.code })
  await expect(option).toHaveCount(1)
  await choixDuPrix.selectOption((await option.getAttribute('value')) ?? '')
  await source.getByRole('button', { name: 'Enregistrer' }).click()
  await expect(ligne).toContainText(PRIX.code, { timeout: 20_000 })

  await page.getByRole('button', { name: 'Créer une étude de prix' }).click()
  await page.getByRole('link', { name: 'Ouvrir' }).first().click()
  await page.waitForURL(/\/estimations\//)

  // **6,02 m × 25,00 € = 150,50 € de déboursé sec.** Un nombre qui se
  // recalcule de tête, et c'est le seul moyen de dire que la quantité REPRISE
  // est bien celle qui chiffre — et non les 6 001,2 mm que Metreo proposait,
  // ni une quantité tapée à la main.
  //
  // Le déboursé sec, et non le total HT : celui-ci porte en plus les frais de
  // chantier, les frais généraux, les aléas et la marge, dont les taux
  // appartiennent à l'organisation et n'ont pas à être figés dans un scénario.
  await page.locator('table.totals tr').first().waitFor({ timeout: 30_000 })
  const lignes = await page.locator('table.totals tr').all()
  const totaux: Record<string, string> = {}
  for (const ligne of lignes) {
    const [intitule, montant] = await ligne.locator('td').allInnerTexts()
    if (intitule !== undefined && montant !== undefined) {
      totaux[intitule.trim()] = montant.replace(/\s*EUR$/, '').trim()
    }
  }
  expect(
    Object.entries(totaux).map(([cle, valeur]) => `${cle}=${valeur}`).join(' | '),
  ).toContain('150.50')

  await page.getByRole('button', { name: 'Geler cette version' }).click()
  await page.getByRole('button', { name: /confirmer/i }).click()
  await expect(page.getByText('Gelée', { exact: true })).toBeVisible()

  const emission = page.getByTestId('emission-du-devis')
  await emission.getByRole('button', { name: 'Émettre le devis' }).click()
  await page.getByLabel('Valable jusqu’au').fill('2027-12-31')
  await page.getByTestId('confirmer-l-emission').click()
  await expect(page.getByTestId('devis-emis')).toBeVisible({ timeout: 30_000 })

  // ---- 8. le PDF remis au client porte la ligne, sa quantité et son unité
  const pdf = await telechargerLeDevis(page)
  expect(pdf.subarray(0, 5).toString('latin1'), 'un PDF commence par %PDF-').toBe('%PDF-')
  const texte = texteDuPdf(pdf)
  for (const attendu of [POSTE, DESIGNATION, QUANTITE_DANS_LE_PDF, PRIX.unite]) {
    expect(texte, `le PDF doit imprimer « ${attendu} »`).toContain(attendu)
  }
  // La provenance, elle, n'y est PAS : un client n'a pas à lire nos décisions
  // internes. Elle reste en base, dans l'empreinte, et au journal d'audit.
  for (const interdit of ['probablement', 'incertitude', 'proposal']) {
    expect(texte.toLowerCase(), `« ${interdit} » n'a rien à faire sur un devis`).not.toContain(
      interdit,
    )
  }
})
