import { mkdirSync } from 'node:fs'
import { join } from 'node:path'

import { expect, test, type Locator, type Page } from '@playwright/test'

/**
 * Le parcours PDF, capturé image par image — la démonstration qu'on montre.
 *
 *     CAPTURES_DIR=/tmp/parcours-pdf npx playwright test \
 *       --config=playwright.captures.config.ts captures/parcours-pdf.spec.ts
 *
 * **Ce que ce fichier est, et ce qu'il n'est pas.** Il n'ajoute rien au
 * produit : il pilote l'application telle qu'elle est et photographie ce
 * qu'elle affiche. Les épreuves qui tombent à chaque livraison vivent dans
 * `e2e/` et `e2e-premier-devis/` ; celle-ci se demande par `--config` et
 * aucune des deux configurations de CI ne la ramasse.
 *
 * **Les sept gestes, dans l'ordre où une personne les fait** — dépôt, aperçu,
 * calibration, longueur, surface, correction, rejet — et une capture par
 * geste. Le scénario ÉCHOUE si l'écran ne montre pas ce que la capture est
 * censée prouver : une image produite par un parcours qui ne vérifie rien ne
 * démontre rien.
 *
 * **Ce que ces captures ne prouvent pas.** Que le nombre mesuré est JUSTE. Le
 * parcours clique sur un rendu ; aucun clic de souris ne vaut une cote de
 * référence. La fixture est un dessin fabriqué par le dépôt, pas un plan
 * d'exécution. Ce qui est démontré est que le geste aboutit, que le nombre
 * porte son unité, son incertitude et sa provenance, et que la décision
 * humaine s'inscrit À CÔTÉ de la proposition sans la réécrire. La justesse sur
 * de vrais plans reste à établir — c'est l'objet de `docs/COTES_DE_REFERENCE.md`.
 */

const SORTIE = process.env.CAPTURES_DIR ?? '/tmp/parcours-pdf'
mkdirSync(SORTIE, { recursive: true })

const PLAN = join(__dirname, '..', '..', '..', 'fixtures', 'plans', 'plan_cote.pdf')

/** L'analyse est synchrone et dure des secondes ; la première tuile aussi. */
const DELAI_ANALYSE = 60_000

/**
 * La fixture fait 300 points de large, la loupe en couvre 5 % — soit 15.
 *
 * C'est pourquoi les clics de calibration visent les BORDS de la loupe et non
 * ses quarts : en deçà de 10 points, `mesures_pdf` refuse la calibration, et
 * elle a raison de le faire.
 */
const DISTANCE_REELLE = '7500'
const MOTIF_DE_CALIBRATION = 'Cote lue au cartouche : 7500 mm sur la demi-largeur.'
const LONGUEUR = 'Façade sud'
const SURFACE = 'Dalle du séjour'
const MOTIF_DE_CORRECTION = 'Relevé sur place : la façade fait 3,80 m, pas la valeur pointée.'
const VALEUR_CORRIGEE = '3800,5'
const MOTIF_DE_REJET = 'Contour pointé sur le mauvais local : cette surface ne veut rien dire.'

let numero = 0

/** Enregistre une capture numérotée, et dit à l'écran ce qu'elle montre. */
async function capturer(page: Page, nom: string, quoi: string): Promise<void> {
  numero += 1
  const fichier = join(SORTIE, `${String(numero).padStart(2, '0')}-${nom}.png`)
  await page.screenshot({ path: fichier, fullPage: true })
  console.log(`  capture ${String(numero).padStart(2, '0')} — ${quoi}\n            ${fichier}`)
}

/**
 * Clique à une fraction de la boîte d'un élément.
 *
 * Des coordonnées absolues dépendraient de la taille de la fenêtre, et une
 * démonstration qui casse parce que l'écran a changé de résolution n'apprend
 * rien.
 */
async function cliquerA(element: Locator, fractionX: number, fractionY: number): Promise<void> {
  const boite = await element.boundingBox()
  expect(boite, "l'élément visé n'a pas de boîte : il n'est pas rendu").not.toBeNull()
  if (!boite) return
  await element.click({ position: { x: boite.width * fractionX, y: boite.height * fractionY } })
}

/**
 * Où ouvrir la loupe : sur du DESSIN, jamais sur du vide.
 *
 * Les fractions désignent des cotes de la fixture, relevées sur l'aperçu. Une
 * loupe ouverte au centre d'un plan tombe sur du papier blanc, et une capture
 * de papier blanc ne démontre pas qu'on y voit mieux — c'est précisément ce
 * que la loupe est censée prouver.
 */
const ZONES = {
  cote5000: { x: 0.177, y: 0.165 },
  cote2500: { x: 0.177, y: 0.437 },
  coupeAA: { x: 0.23, y: 0.713 },
} as const

/** Ouvre la loupe sur une zone de l'aperçu et attend que la tuile soit rendue. */
async function ouvrirLaLoupe(
  page: Page,
  zone: { readonly x: number; readonly y: number },
): Promise<Locator> {
  await cliquerA(page.getByTestId('pdf-apercu'), zone.x, zone.y)
  await expect(page.getByTestId('pdf-loupe-panneau')).toBeVisible()
  const rendu = page.getByTestId('pdf-loupe-image')
  await expect(rendu).toBeVisible({ timeout: DELAI_ANALYSE })
  // La tuile elle-même, et pas seulement son cadre : la première coûte des
  // secondes, et une capture prise avant son arrivée montrerait du blanc.
  await expect(page.getByTestId('pdf-loupe-rendu')).toBeVisible({ timeout: DELAI_ANALYSE })
  return rendu
}

/** La ligne du tableau des mesures portant ce libellé. */
function ligneDeMesure(page: Page, libelle: string): Locator {
  return page.getByTestId('pdf-mesure').filter({ hasText: libelle }).first()
}

test('le parcours PDF en sept gestes, capturés', async ({ page }) => {
  test.setTimeout(600_000)

  await page.goto('/')
  await page.getByLabel('Adresse e-mail').fill('admin@dubois.demo')
  await page.getByRole('button', { name: 'Se connecter' }).click()
  await expect(page).toHaveURL(/\/projets/)

  // Un projet neuf. La configuration des captures ne joue que l'amorçage, qui
  // ne crée aucun chantier : le projet `PREM-001` que les suites de livraison
  // reprennent est l'œuvre de `premier-devis.spec.ts`, et il n'existe pas ici.
  await page.goto('/projets')
  await page
    .getByRole('button', { name: /nouveau projet/i })
    .first()
    .click()
  await page.getByLabel(/référence/i).fill('DEMO-PDF')
  await page
    .getByLabel(/^nom/i)
    .first()
    .fill('Démonstration du parcours PDF')
  await page
    .getByRole('button', { name: /^créer$/i })
    .first()
    .click()
  await page.getByRole('link', { name: 'DEMO-PDF' }).first().click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}$/)

  // ---- 1. le dépôt
  const documents = page.getByTestId('documents')
  await expect(documents).toBeVisible()
  await documents.getByLabel('Catégorie').selectOption('Plan')
  await documents.getByLabel(/Libellé/).fill('Façade sud — PDF')
  await documents.getByLabel('Fichier à joindre').setInputFiles(PLAN)

  const ligne = documents
    .locator('tr')
    .filter({ has: page.getByRole('cell', { name: 'plan_cote.pdf' }) })
    .first()
  await expect(ligne).toBeVisible()
  // Le lien existe POUR UN PDF : c'est lui qui manquait, et sans lui l'écran
  // de lecture était inatteignable depuis l'application.
  await expect(ligne.getByTestId('documents-lire-plan')).toBeVisible()
  await capturer(page, 'depot', 'le PDF déposé, et le lien « Lire le plan » qui y mène')

  // ---- 2. l'aperçu, et les textes situés dessus
  await ligne.getByTestId('documents-lire-plan').click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}\/plans\/[0-9a-f-]{36}\/[0-9a-f-]{36}/)
  await page.getByTestId('plan-analyser').click()

  const ecran = page.getByTestId('pdf-lecture')
  await expect(ecran).toBeVisible({ timeout: DELAI_ANALYSE })
  await expect(page.getByTestId('pdf-apercu-image')).toBeVisible({ timeout: DELAI_ANALYSE })
  await expect(ecran.getByTestId('pdf-texte')).toHaveCount(9, { timeout: DELAI_ANALYSE })
  // Avant toute calibration, l'écran DIT qu'il n'y a rien à mesurer.
  await expect(page.getByTestId('pdf-echelle')).toContainText('rien ne peut être mesuré')
  await capturer(
    page,
    'apercu',
    'l’aperçu, les neuf textes situés, et « aucune échelle : rien ne peut être mesuré »',
  )

  // ---- 3. la calibration, pointée DANS la loupe
  await page.getByTestId('pdf-outil-calibrer').click()
  const loupe = await ouvrirLaLoupe(page, ZONES.cote5000)
  await cliquerA(loupe, 0.05, 0.5)
  await cliquerA(loupe, 0.95, 0.5)

  const formulaireDeCalibration = page.getByTestId('pdf-formulaire-calibration')
  await expect(formulaireDeCalibration).toBeVisible()
  await formulaireDeCalibration
    .getByLabel('Distance réelle entre les deux points')
    .fill(DISTANCE_REELLE)
  await formulaireDeCalibration.getByLabel('Unité').selectOption('mm')
  await formulaireDeCalibration
    .getByLabel('Sur quoi avez-vous calibré ?')
    .fill(MOTIF_DE_CALIBRATION)
  await capturer(
    page,
    'calibration',
    'les deux points dans la loupe, et la distance réelle déclarée à la main',
  )

  await formulaireDeCalibration.getByTestId('pdf-calibrer').click()
  const facteur = page.getByTestId('pdf-facteur')
  await expect(facteur).toBeVisible({ timeout: DELAI_ANALYSE })
  await expect(facteur).toContainText('mm par point')
  await expect(page.getByTestId('pdf-echelle')).toContainText('7500 mm sur la demi-largeur')
  await capturer(page, 'echelle-confirmee', 'l’échelle déclarée, son facteur et son motif')

  // ---- 4. une longueur
  await page.getByTestId('pdf-outil-segment').click()
  const loupeSegment = await ouvrirLaLoupe(page, ZONES.cote2500)
  await cliquerA(loupeSegment, 0.25, 0.4)
  await cliquerA(loupeSegment, 0.75, 0.4)

  const formulaireDeMesure = page.getByTestId('pdf-formulaire-mesure')
  await expect(formulaireDeMesure).toBeVisible()
  await formulaireDeMesure.getByLabel('Ce que vous mesurez').fill(LONGUEUR)
  await formulaireDeMesure.getByTestId('pdf-mesurer').click()

  const longueur = ligneDeMesure(page, LONGUEUR)
  await expect(longueur).toBeVisible({ timeout: DELAI_ANALYSE })
  const valeurMesuree = (await longueur.getByTestId('pdf-valeur').innerText()).trim()
  expect(valeurMesuree, 'la longueur doit porter sa valeur et son unité').toContain('mm')
  await expect(longueur.getByTestId('pdf-decision')).toHaveText('—')
  await capturer(
    page,
    'longueur',
    `la longueur mesurée — ${valeurMesuree} — avec son incertitude, sa fiabilité et sa provenance`,
  )

  // ---- 5. une surface
  await page.getByTestId('pdf-outil-surface').click()
  const loupeSurface = await ouvrirLaLoupe(page, ZONES.coupeAA)
  await cliquerA(loupeSurface, 0.2, 0.2)
  await cliquerA(loupeSurface, 0.8, 0.25)
  await cliquerA(loupeSurface, 0.75, 0.8)
  await cliquerA(loupeSurface, 0.2, 0.75)

  const formulaireDeSurface = page.getByTestId('pdf-formulaire-mesure')
  await expect(formulaireDeSurface).toBeVisible()
  await formulaireDeSurface.getByLabel('Ce que vous mesurez').fill(SURFACE)
  await formulaireDeSurface.getByTestId('pdf-mesurer').click()

  const surface = ligneDeMesure(page, SURFACE)
  await expect(surface).toBeVisible({ timeout: DELAI_ANALYSE })
  const valeurDeLaSurface = (await surface.getByTestId('pdf-valeur').innerText()).trim()
  // Le dépôt ne connaît pas de millimètre carré : une surface sort en m².
  expect(valeurDeLaSurface, 'une surface est rendue en mètres carrés').toContain('m2')
  await capturer(
    page,
    'surface',
    `la surface mesurée — ${valeurDeLaSurface} — rendue en mètres carrés`,
  )

  // ---- 6. la correction : le motif est EXIGÉ, et la proposition survit
  await expect(longueur.getByTestId('pdf-corriger')).toBeDisabled()
  await longueur.getByTestId('pdf-motif-decision').fill(MOTIF_DE_CORRECTION)
  await longueur.getByTestId('pdf-correction').fill(VALEUR_CORRIGEE)
  await longueur.getByTestId('pdf-corriger').click()

  const corrigee = ligneDeMesure(page, LONGUEUR)
  await expect(corrigee.getByTestId('pdf-valeur-retenue')).toBeVisible({ timeout: DELAI_ANALYSE })
  // Le cœur du parcours : LES DEUX nombres sont là. La proposition de la
  // machine n'est jamais réécrite.
  await expect(corrigee.getByTestId('pdf-valeur')).toHaveText(valeurMesuree)
  await expect(corrigee.getByTestId('pdf-valeur-retenue')).toContainText('3800')
  await expect(corrigee.getByTestId('pdf-decision')).toHaveText('corrigée')
  await capturer(
    page,
    'correction',
    'la valeur proposée ET la valeur retenue, côte à côte, avec « corrigée »',
  )

  // ---- 7. le rejet : sans valeur de remplacement, mais avec un motif
  await expect(surface.getByTestId('pdf-rejeter')).toBeDisabled()
  await surface.getByTestId('pdf-motif-decision').fill(MOTIF_DE_REJET)
  await surface.getByTestId('pdf-rejeter').click()

  const rejetee = ligneDeMesure(page, SURFACE)
  await expect(rejetee.getByTestId('pdf-decision')).toHaveText('rejetée', {
    timeout: DELAI_ANALYSE,
  })
  // Rejetée, et pourtant toujours lisible : c'est ce qui permet de savoir, dans
  // six mois, ce que le programme avait proposé et pourquoi on l'a écarté.
  await expect(rejetee.getByTestId('pdf-valeur')).toHaveText(valeurDeLaSurface)
  await expect(rejetee.getByTestId('pdf-valeur-retenue')).toHaveCount(0)
  await capturer(
    page,
    'rejet',
    'la surface rejetée : la décision est inscrite, la proposition reste lisible',
  )

  console.log(`\n  ${numero} captures dans ${SORTIE}`)
})
