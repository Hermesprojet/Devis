import { join } from 'node:path'

import { expect, test, type Locator, type Page } from '@playwright/test'

import { ADMIN } from './banc'
import { seConnecter } from './parcours'

/**
 * Déposer un plan PDF, retrouver ses textes sur l'image, déclarer une échelle,
 * mesurer, puis corriger la valeur.
 *
 * **Ce que ce scénario prouve et que rien d'autre ne prouve.** Les tests de
 * l'API vérifient la géométrie, l'incertitude et la provenance ; le typage
 * vérifie que l'écran compile. Aucun des deux ne dit qu'une personne **voit**
 * l'information sur le plan et peut agir dessus. C'est le seul endroit où le
 * parcours entier passe par le navigateur : dépôt, aperçu, textes situés,
 * navigation entre pages, loupe, calibration, mesure, correction.
 *
 * **Pourquoi un PDF de DEUX pages.** La navigation entre pages est une des
 * choses qu'un écran peut faire semblant de faire : changer le numéro affiché
 * et resservir la même image. La fixture porte des textes DISTINCTS sur chaque
 * page, et le scénario cherche « DETAIL B », qui n'existe que sur la seconde.
 *
 * **Pourquoi les clics sont calculés et non codés en dur.** Les points se
 * posent dans la LOUPE, dont la taille dépend du rendu. Le scénario lit la
 * boîte de l'élément et clique à une fraction de sa largeur : des coordonnées
 * absolues dépendraient de la résolution du banc, et un parcours qui échoue
 * parce que la fenêtre a changé de taille n'apprend rien.
 *
 * **Ce qui n'est pas vérifié ici, et pourquoi.** La JUSTESSE du nombre mesuré
 * ne l'est pas : un clic de souris sur un rendu ne vaut pas une référence, et
 * c'est précisément ce que `docs/COTES_DE_REFERENCE.md` sert à établir. Ce qui
 * est vérifié est que le nombre existe, porte son unité, porte son
 * incertitude, dit sa fiabilité, et que la correction humaine s'affiche À CÔTÉ
 * de la proposition sans la réécrire.
 *
 * Mêmes pièges que le scénario DXF, et un de plus :
 *
 *  - Next maintient un `role="alert"` VIDE en permanence ; on vise des
 *    `data-testid`, jamais `getByRole('alert')` seul.
 *  - l'analyse est synchrone et dure plusieurs secondes.
 *  - **la première tuile coûte des secondes** — le chargement de la page par
 *    PDFium, mesuré jusqu'à 5,3 s sur un A0 — et les attentes qui la suivent
 *    portent donc leur propre délai.
 */

const PLAN = join(__dirname, '..', '..', '..', 'fixtures', 'plans', 'plan_cote.pdf')

/** Au-delà du délai ordinaire : l'analyse, puis le rendu de la tuile. */
const DELAI_ANALYSE = 60_000

/** La page de la fixture fait 300 points de large. La moitié en fait 150. */
const DISTANCE_REELLE = '7500'
const MOTIF_DE_CALIBRATION = 'Cote lue au cartouche : 7500 mm sur la demi-largeur.'
const LIBELLE = 'Longueur de la façade sud'
const MOTIF_DE_DECISION = 'Relevé sur place : le pointage était un peu court.'
const VALEUR_CORRIGEE = '3800,5'

async function ouvrirLeProjet(page: Page): Promise<void> {
  await page.getByRole('link', { name: 'PREM-001' }).click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}$/)
}

/**
 * Clique à une fraction de la boîte d'un élément.
 *
 * `position` de Playwright compte en pixels depuis le coin haut gauche de
 * l'élément ; la fraction est convertie ici pour que le scénario parle en
 * « au quart de la largeur » plutôt qu'en pixels d'un écran donné.
 */
async function cliquerA(element: Locator, fractionX: number, fractionY: number): Promise<void> {
  const boite = await element.boundingBox()
  expect(boite, "l'élément visé n'a pas de boîte : il n'est pas rendu").not.toBeNull()
  if (!boite) return
  await element.click({
    position: { x: boite.width * fractionX, y: boite.height * fractionY },
  })
}

test('un plan PDF déposé montre ses textes, se calibre par deux points, se mesure, et la correction s’affiche à côté de la mesure', async ({
  page,
}) => {
  await seConnecter(page, ADMIN)
  await ouvrirLeProjet(page)

  const documents = page.getByTestId('documents')
  await expect(documents).toBeVisible()

  // ---- 1. le dépôt
  await documents.getByLabel('Catégorie').selectOption('Plan')
  await documents.getByLabel(/Libellé/).fill('Façade sud — PDF')
  await documents.getByLabel('Fichier à joindre').setInputFiles(PLAN)

  const ligne = documents
    .locator('tr')
    .filter({ has: page.getByRole('cell', { name: 'plan_cote.pdf' }) })
    .first()
  await expect(ligne).toBeVisible()

  // ---- 2. l'écran de lecture, puis l'analyse
  await ligne.getByTestId('documents-lire-plan').click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}\/plans\/[0-9a-f-]{36}\/[0-9a-f-]{36}/)
  await page.getByTestId('plan-analyser').click()

  // ---- 3. l'écran PDF, et non l'écran DXF : c'est le format qui décide
  const ecran = page.getByTestId('pdf-lecture')
  await expect(ecran).toBeVisible({ timeout: DELAI_ANALYSE })
  await expect(page.getByTestId('pdf-apercu-image')).toBeVisible({ timeout: DELAI_ANALYSE })

  // ---- 4. sans échelle, l'écran le DIT — c'est la première chose à savoir,
  //         puisque aucune mesure n'est possible avant
  await expect(page.getByTestId('pdf-echelle')).toContainText('rien ne peut être mesuré')

  // ---- 5. les textes sont situés SUR l'image, et chacun se nomme
  //
  // Neuf fragments sur la première page de la fixture. Le `<title>` porte le
  // texte lu : à deux pixels de haut, le surlignage dit qu'il y a une
  // information, et son nom dit laquelle.
  const textes = ecran.getByTestId('pdf-texte')
  await expect(textes).toHaveCount(9, { timeout: DELAI_ANALYSE })
  await expect(textes.filter({ has: page.locator('title', { hasText: 'Ech. 1:50' }) })).toHaveCount(
    1,
  )

  // ---- 6. la navigation entre pages change VRAIMENT de page
  //
  // « DETAIL B » n'existe que sur la seconde, et la seconde porte cinq
  // fragments au lieu de neuf. Un écran qui resservirait la même image
  // échouerait ici.
  await expect(page.getByTestId('pdf-page-courante')).toContainText('1')
  await page.getByTestId('pdf-page-suivante').click()
  await expect(page.getByTestId('pdf-page-courante')).toContainText('2')
  await expect(textes).toHaveCount(5, { timeout: DELAI_ANALYSE })
  await expect(textes.filter({ has: page.locator('title', { hasText: 'DETAIL B' }) })).toHaveCount(
    1,
  )

  // Et on revient : la mesure se fera sur la première page.
  await page.getByTestId('pdf-page-precedente').click()
  await expect(page.getByTestId('pdf-page-courante')).toContainText('1')
  await expect(textes).toHaveCount(9, { timeout: DELAI_ANALYSE })

  // ---- 7. la calibration : deux points POINTÉS DANS LA LOUPE
  //
  // Et non sur l'aperçu : mesuré sur des plans réels, un pixel d'aperçu vaut
  // 12 à 42 mm d'ouvrage. L'aperçu sert à trouver la zone, la loupe à pointer.
  await page.getByTestId('pdf-outil-calibrer').click()
  await cliquerA(page.getByTestId('pdf-apercu'), 0.5, 0.5)

  const loupe = page.getByTestId('pdf-loupe-panneau')
  await expect(loupe).toBeVisible()
  const rendu = page.getByTestId('pdf-loupe-image')
  await expect(rendu).toBeVisible({ timeout: DELAI_ANALYSE })

  // Aux bords, et non aux cinquièmes : la loupe couvre 5 % de la page, soit
  // 15 points sur la fixture de 300, et la calibration refuse à juste titre une
  // base de moins de 10 points. Sur un A0 les mêmes fractions en désignent 160.
  //
  // C'est ce calcul qui a révélé le défaut de marge : les mêmes clics
  // désignaient 9 points parce que la tuile rendait 85 % de la page tout en
  // laissant l'écran croire qu'elle en rendait 5.
  await cliquerA(rendu, 0.05, 0.5)
  await cliquerA(rendu, 0.95, 0.5)

  const calibration = page.getByTestId('pdf-formulaire-calibration')
  await expect(calibration).toBeVisible()
  await calibration.getByLabel('Distance réelle entre les deux points').fill(DISTANCE_REELLE)
  await calibration.getByLabel('Unité').selectOption('mm')
  await calibration.getByLabel('Sur quoi avez-vous calibré ?').fill(MOTIF_DE_CALIBRATION)
  await calibration.getByTestId('pdf-calibrer').click()

  // ---- 8. l'échelle confirmée s'affiche, avec son motif : une mesure sans
  //         provenance n'est pas auditable
  const facteur = page.getByTestId('pdf-facteur')
  await expect(facteur).toBeVisible({ timeout: DELAI_ANALYSE })
  // « N mm par point » : le facteur est rendu par le serveur comme une chaîne
  // à LIRE, et l'écran n'en refait pas l'arithmétique — deux facteurs
  // finiraient par diverger.
  await expect(facteur).toContainText('mm par point')
  // Et le motif déclaré est affiché à côté : c'est la provenance de l'échelle,
  // sans laquelle la mesure qui en découle n'est pas auditable.
  await expect(page.getByTestId('pdf-echelle')).toContainText('7500 mm sur la demi-largeur')

  // ---- 9. la mesure d'un segment
  await page.getByTestId('pdf-outil-segment').click()
  await cliquerA(page.getByTestId('pdf-apercu'), 0.5, 0.5)
  const rendu2 = page.getByTestId('pdf-loupe-image')
  await expect(rendu2).toBeVisible({ timeout: DELAI_ANALYSE })
  await cliquerA(rendu2, 0.25, 0.4)
  await cliquerA(rendu2, 0.75, 0.4)

  const formulaire = page.getByTestId('pdf-formulaire-mesure')
  await expect(formulaire).toBeVisible()
  await formulaire.getByLabel('Ce que vous mesurez').fill(LIBELLE)
  await formulaire.getByTestId('pdf-mesurer').click()

  // ---- 10. la mesure apparaît avec sa valeur, son unité et sa fiabilité
  const mesures = page.getByTestId('pdf-mesure')
  await expect(mesures.first()).toBeVisible({ timeout: DELAI_ANALYSE })
  const premiere = mesures.first()
  const valeur = (await premiere.getByTestId('pdf-valeur').innerText()).trim()
  expect(valeur, 'la mesure doit porter une valeur et son unité').toMatch(/\d/)
  expect(valeur).toContain('mm')
  // Écrite en français, et arrondie au rang de son incertitude. Le chiffre de
  // trop est ce que la capture du parcours avait rendu visible :
  // « 4180.6822810844 mm » à côté de « ± 26,4 mm ».
  expect(valeur, 'un nombre français : virgule ou espace insécable').toMatch(/[,\u202f]/)
  expect(valeur, 'plus de trois décimales annoncent une précision absente').not.toMatch(
    /[,.]\d{4}/,
  )
  await expect(premiere.getByTestId('pdf-incertitude')).toContainText('±')
  await expect(premiere.getByTestId('pdf-fiabilite')).not.toBeEmpty()
  await expect(premiere.getByTestId('pdf-decision')).toHaveText('—')
  // Tant que personne n'a tranché, RIEN ne peut alimenter un bordereau — et
  // l'écran le dit au lieu de le laisser deviner.
  await expect(premiere.getByTestId('pdf-reprenable')).toHaveAttribute(
    'data-reprenable',
    'non',
  )

  // ---- 11. la correction humaine. Le motif est exigé AVANT : le bouton reste
  //          inerte sans lui, parce qu'une décision sans raison n'est pas une
  //          décision.
  await expect(premiere.getByTestId('pdf-corriger')).toBeDisabled()
  await premiere.getByTestId('pdf-motif-decision').fill(MOTIF_DE_DECISION)
  await premiere.getByTestId('pdf-correction').fill(VALEUR_CORRIGEE)
  await premiere.getByTestId('pdf-corriger').click()

  // ---- 12. et c'est le cœur du parcours : l'écran montre LES DEUX.
  //
  // La proposition de la machine n'est jamais réécrite. Après la décision, la
  // ligne porte encore la valeur mesurée ET la valeur retenue par la personne.
  // N'en montrer qu'une rendrait le dossier inauditable : on ne saurait plus
  // ce que le programme avait proposé.
  const decidee = page
    .getByTestId('pdf-mesure')
    .filter({ hasText: 'corrigée' })
    .first()
  await expect(decidee).toBeVisible({ timeout: DELAI_ANALYSE })
  await expect(decidee.getByTestId('pdf-valeur')).toHaveText(valeur)
  // « 3 800,50 mm » : l'espace est insécable, donc on cherche les deux
  // morceaux plutôt que « 3800 », qui n'existe plus à l'écran.
  await expect(decidee.getByTestId('pdf-valeur-retenue')).toContainText('800,50 mm')
  await expect(decidee.getByTestId('pdf-decision')).toContainText('corrigée')
  // La décision porte sa raison, et la mesure devient reprenable.
  await expect(decidee.getByTestId('pdf-motif-retenu')).toContainText('Relevé sur place')
  await expect(decidee.getByTestId('pdf-reprenable')).toHaveAttribute(
    'data-reprenable',
    'oui',
  )
})
