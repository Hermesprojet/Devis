import { mkdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { expect, test, type Locator, type Page } from '@playwright/test'

/**
 * Le parcours PDF, capturé image par image, sur une géométrie dont on connaît
 * les dimensions.
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
 * **Le plan employé porte une vraie géométrie.** La version précédente
 * travaillait sur `plan_cote.pdf`, qui ne contient que des textes isolés : la
 * calibration y agrandissait « 00 » et la surface mesurait un fragment de
 * « Coupe A-A ». Les gestes étaient les bons, le résultat ne voulait rien
 * dire. `plan_batiment.pdf` porte une **ligne de cote à extrémités
 * matérialisées** et une **pièce fermée**, et sa vérité — 5 000 mm de cote,
 * 6 000 × 4 000 mm de pièce, 24,00 m² — est écrite dans `plan_batiment.json`
 * par la fabrique, en points de papier, **sans passer par le lecteur**.
 *
 * Le parcours calibre donc sur la cote, mesure la façade et le contour, et
 * **compare aux valeurs attendues**. Il échoue si l'écart dépasse 1 %.
 *
 * **Ce qui reste hors de portée de ce fichier.** Que Metreo mesure juste sur
 * un plan d'exécution RÉEL. Ce qui est démontré ici est que la chaîne complète
 * — écran, repère, calibration, calcul, décision — retombe sur une géométrie
 * connue. L'écart entre les deux est l'erreur de pointage d'un humain sur un
 * vrai dessin, et elle se mesure sur de vraies cotes : `docs/COTES_DE_REFERENCE.md`.
 */

const SORTIE = process.env.CAPTURES_DIR ?? '/tmp/parcours-pdf'
mkdirSync(SORTIE, { recursive: true })

const FIXTURES = join(__dirname, '..', '..', '..', 'fixtures', 'plans')
const PLAN = join(FIXTURES, 'plan_batiment.pdf')

/**
 * La vérité du dessin, lue et non recopiée.
 *
 * Elle est écrite par `scripts/fabriquer_plans_de_test.py` à côté du PDF.
 * Recopier ces chiffres ici ferait deux sources, et la seconde finirait par
 * mentir — exactement le genre d'écart qui fait passer un test faux.
 */
type VeriteDuPlan = {
  millimetres_par_point: number
  page: [number, number]
  cote: { premier: [number, number]; second: [number, number]; longueur_mm: number }
  piece: {
    coins: [number, number][]
    largeur_mm: number
    profondeur_mm: number
    surface_m2: number
  }
}
const VERITE: VeriteDuPlan = JSON.parse(
  readFileSync(join(FIXTURES, 'plan_batiment.json'), 'utf8'),
) as VeriteDuPlan

/** L'analyse est synchrone et dure des secondes ; la première tuile aussi. */
const DELAI_ANALYSE = 60_000

/** La loupe couvre 5 % de la page, comme `TAILLE_DE_LA_LOUPE` dans l'écran. */
const TAILLE_DE_LA_LOUPE = 0.05

const MOTIF_DE_CALIBRATION = 'Cote 5000 du plan RDC, pointée à ses deux extrémités.'
// Deux libellés sans mot commun : `hasText` de Playwright cherche une
// SOUS-CHAÎNE, et « Séjour » retrouvait « Façade du séjour ».
const LONGUEUR = 'Mur avant'
const SURFACE = 'Séjour'
const MOTIF_DE_CORRECTION = 'Relevé sur place au décamètre : 6,02 m et non 6,00.'
const VALEUR_CORRIGEE = '6020'
const MOTIF_DE_REJET = 'Contour pointé sur le mauvais local : cette surface ne veut rien dire.'

let numero = 0

/** Enregistre une capture numérotée, et dit à l'écran ce qu'elle montre. */
async function capturer(page: Page, nom: string, quoi: string): Promise<void> {
  numero += 1
  const fichier = join(SORTIE, `${String(numero).padStart(2, '0')}-${nom}.png`)
  // Remonté en haut AVANT la capture : la colonne du plan est collante, et
  // Chromium photographie un élément collant à sa position STUCK dans une
  // capture pleine page — il laisse alors un grand blanc là où il se trouvait.
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: fichier, fullPage: true })
  console.log(`  capture ${String(numero).padStart(2, '0')} — ${quoi}\n            ${fichier}`)
}

/**
 * D'un point du DESSIN (points PostScript) à une fraction de l'aperçu.
 *
 * La page de la fixture n'est ni décalée ni tournée : la conversion est celle
 * de `lecture_pdf._VERS_L_ECRAN` pour une rotation nulle — l'abscisse telle
 * quelle, l'ordonnée renversée, parce qu'un PDF compte depuis le bas et un
 * écran depuis le haut.
 */
function versLEcran([u, v]: [number, number]): { x: number; y: number } {
  const [largeur, hauteur] = VERITE.page
  return { x: u / largeur, y: 1 - v / hauteur }
}

/** Clique à une fraction de la boîte d'un élément. */
async function cliquerA(element: Locator, fractionX: number, fractionY: number): Promise<void> {
  const boite = await element.boundingBox()
  expect(boite, "l'élément visé n'a pas de boîte : il n'est pas rendu").not.toBeNull()
  if (!boite) return
  await element.click({ position: { x: boite.width * fractionX, y: boite.height * fractionY } })
}

/**
 * Ouvre la loupe CENTRÉE sur un point du dessin, et attend la tuile.
 *
 * Centrée sur ce qu'on veut pointer, et non « au milieu » : une loupe ouverte
 * au hasard tombe sur du papier blanc, et une capture de papier blanc ne
 * démontre pas qu'on y voit mieux.
 */
async function loupeSur(page: Page, point: [number, number]): Promise<Locator> {
  const cible = versLEcran(point)
  await cliquerA(page.getByTestId('pdf-apercu'), cible.x, cible.y)
  await expect(page.getByTestId('pdf-loupe-panneau')).toBeVisible()
  const rendu = page.getByTestId('pdf-loupe-image')
  await expect(rendu).toBeVisible({ timeout: DELAI_ANALYSE })
  await expect(page.getByTestId('pdf-loupe-rendu')).toBeVisible({ timeout: DELAI_ANALYSE })
  return rendu
}

/**
 * Clique un point du DESSIN dans la loupe ouverte.
 *
 * La loupe est une fenêtre carrée de `TAILLE_DE_LA_LOUPE` centrée sur le
 * dernier clic de l'aperçu, bornée aux bords de la page — exactement ce que
 * fait `cliquerSurLApercu`. La fraction interne se calcule donc depuis cette
 * fenêtre, et un point hors d'elle est une erreur du scénario, pas de l'écran.
 */
async function pointerDansLaLoupe(
  page: Page,
  centre: [number, number],
  cible: [number, number],
): Promise<void> {
  const c = versLEcran(centre)
  const demi = TAILLE_DE_LA_LOUPE / 2
  const x0 = Math.max(0, c.x - demi)
  const y0 = Math.max(0, c.y - demi)
  const x1 = Math.min(1, c.x + demi)
  const y1 = Math.min(1, c.y + demi)

  const point = versLEcran(cible)
  const fractionX = (point.x - x0) / (x1 - x0)
  const fractionY = (point.y - y0) / (y1 - y0)
  expect(
    fractionX >= 0 && fractionX <= 1 && fractionY >= 0 && fractionY <= 1,
    `le point ${cible} est hors de la loupe centrée sur ${centre}`,
  ).toBeTruthy()

  await cliquerA(page.getByTestId('pdf-loupe-image'), fractionX, fractionY)
}

/** Le nombre porté par un texte français : « 6 004,2 mm » → 6004.2 */
function nombreDe(texte: string): number {
  const nettoye = texte.replace(/[  \s]/g, '').replace(',', '.')
  const trouve = /-?\d+(\.\d+)?/.exec(nettoye)
  expect(trouve, `aucun nombre dans « ${texte} »`).not.toBeNull()
  return Number(trouve?.[0])
}

function ligneDeMesure(page: Page, libelle: string): Locator {
  return page.getByTestId('pdf-mesure').filter({ hasText: libelle }).first()
}

test('le parcours PDF en sept gestes, sur une géométrie dont on connaît les dimensions', async ({
  page,
}) => {
  test.setTimeout(600_000)

  await page.goto('/')
  await page.getByLabel('Adresse e-mail').fill('admin@dubois.demo')
  await page.getByRole('button', { name: 'Se connecter' }).click()
  await expect(page).toHaveURL(/\/projets/)

  // Un projet neuf. La configuration des captures ne joue que l'amorçage, qui
  // ne crée aucun chantier.
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
  await documents.getByLabel(/Libellé/).fill('Plan RDC — PDF')
  await documents.getByLabel('Fichier à joindre').setInputFiles(PLAN)

  const ligne = documents
    .locator('tr')
    .filter({ has: page.getByRole('cell', { name: 'plan_batiment.pdf' }) })
    .first()
  await expect(ligne).toBeVisible()
  await expect(ligne.getByTestId('documents-lire-plan')).toBeVisible()
  await capturer(page, 'depot', 'le PDF déposé, et le lien « Lire le plan » qui y mène')

  // ---- 2. l'aperçu, et ce que l'écran annonce AVANT toute échelle
  await ligne.getByTestId('documents-lire-plan').click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}\/plans\/[0-9a-f-]{36}\/[0-9a-f-]{36}/)
  await page.getByTestId('plan-analyser').click()

  const ecran = page.getByTestId('pdf-lecture')
  await expect(ecran).toBeVisible({ timeout: DELAI_ANALYSE })
  await expect(page.getByTestId('pdf-apercu-image')).toBeVisible({ timeout: DELAI_ANALYSE })

  // Le bandeau DXF n'a plus sa place ici : il annonçait « Aucune mesure n'est
  // exploitable » au-dessus d'un écran dont mesurer est l'unique objet.
  await expect(page.getByTestId('plan-constat')).toHaveCount(0)
  await expect(page.getByTestId('plan-introduction')).toContainText('POINTEZ')
  await expect(page.getByTestId('pdf-echelle')).toContainText('rien ne peut être mesuré')
  // Et le fil d'état dit où l'on en est, au lieu de le laisser deviner.
  await expect(page.getByTestId('pdf-etape-echelle')).toHaveAttribute('data-etat', 'en-cours')
  await capturer(
    page,
    'apercu',
    'l’aperçu du plan, le fil d’état à la première étape, et « aucune échelle »',
  )

  // ---- 3. la calibration, sur les DEUX EXTRÉMITÉS de la ligne de cote
  const premier = VERITE.cote.premier
  const second = VERITE.cote.second
  await page.getByTestId('pdf-outil-calibrer').click()

  // La cote fait 200 points et la loupe en couvre 21 : les deux extrémités
  // n'entrent pas dans la même fenêtre. On pointe la première, on DÉPLACE la
  // loupe, on pointe la seconde — et les points posés survivent au
  // déplacement. C'est le geste réel sur un plan de grand format, et c'est
  // aussi ce que l'écran annonce désormais.
  await loupeSur(page, premier)
  await pointerDansLaLoupe(page, premier, premier)
  await capturer(
    page,
    'calibration-premier-point',
    'la première extrémité de la cote, pointée dans la loupe',
  )

  await loupeSur(page, second)
  await pointerDansLaLoupe(page, second, second)

  const formulaireDeCalibration = page.getByTestId('pdf-formulaire-calibration')
  await expect(formulaireDeCalibration).toBeVisible()
  await formulaireDeCalibration
    .getByLabel('Distance réelle entre les deux points')
    .fill(String(VERITE.cote.longueur_mm))
  await formulaireDeCalibration.getByLabel('Unité').selectOption('mm')
  await formulaireDeCalibration.getByLabel('Sur quoi avez-vous calibré ?').fill(MOTIF_DE_CALIBRATION)
  await capturer(
    page,
    'calibration',
    'la seconde extrémité pointée, et la distance réelle déclarée à la main',
  )

  await formulaireDeCalibration.getByTestId('pdf-calibrer').click()
  await expect(page.getByTestId('pdf-facteur')).toBeVisible({ timeout: DELAI_ANALYSE })
  await expect(page.getByTestId('pdf-facteur')).toContainText('mm par point')
  await expect(page.getByTestId('pdf-etape-echelle')).toHaveAttribute('data-etat', 'faite')
  await capturer(page, 'echelle-confirmee', 'l’échelle déclarée, son facteur et son motif')

  // ---- 4. une longueur : la façade du séjour, 6 000 mm attendus
  const [coinA, coinB, coinC, coinD] = VERITE.piece.coins as [
    [number, number],
    [number, number],
    [number, number],
    [number, number],
  ]
  await page.getByTestId('pdf-outil-segment').click()
  await loupeSur(page, coinA)
  await pointerDansLaLoupe(page, coinA, coinA)
  await loupeSur(page, coinB)
  await pointerDansLaLoupe(page, coinB, coinB)

  const formulaireDeMesure = page.getByTestId('pdf-formulaire-mesure')
  await expect(formulaireDeMesure).toBeVisible()
  await formulaireDeMesure.getByLabel('Ce que vous mesurez').fill(LONGUEUR)
  await formulaireDeMesure.getByTestId('pdf-mesurer').click()

  const longueur = ligneDeMesure(page, LONGUEUR)
  await expect(longueur).toBeVisible({ timeout: DELAI_ANALYSE })
  const valeurLue = (await longueur.getByTestId('pdf-valeur').innerText()).trim()
  const mesuree = nombreDe(valeurLue)
  const attendue = VERITE.piece.largeur_mm
  // **La comparaison qui fait de ce parcours autre chose qu'une démonstration.**
  expect(
    Math.abs(mesuree - attendue) / attendue,
    `la façade devrait mesurer ${attendue} mm ; l'écran affiche « ${valeurLue} »`,
  ).toBeLessThan(0.01)
  await expect(longueur.getByTestId('pdf-reprenable')).toHaveAttribute('data-reprenable', 'non')
  await capturer(
    page,
    'longueur',
    `la façade mesurée — ${valeurLue} pour ${attendue} mm attendus — avec son incertitude`,
  )

  // ---- 5. une surface : le contour de la pièce, 24,00 m² attendus
  await page.getByTestId('pdf-outil-surface').click()
  for (const coin of [coinA, coinB, coinC, coinD]) {
    await loupeSur(page, coin)
    await pointerDansLaLoupe(page, coin, coin)
  }

  const formulaireDeSurface = page.getByTestId('pdf-formulaire-mesure')
  await expect(formulaireDeSurface).toBeVisible()
  await formulaireDeSurface.getByLabel('Ce que vous mesurez').fill(SURFACE)
  await formulaireDeSurface.getByTestId('pdf-mesurer').click()

  const surface = ligneDeMesure(page, SURFACE)
  await expect(surface).toBeVisible({ timeout: DELAI_ANALYSE })
  const surfaceLue = (await surface.getByTestId('pdf-valeur').innerText()).trim()
  expect(surfaceLue, 'une surface est rendue en mètres carrés').toContain('m²')
  const surfaceMesuree = nombreDe(surfaceLue)
  expect(
    Math.abs(surfaceMesuree - VERITE.piece.surface_m2) / VERITE.piece.surface_m2,
    `le séjour devrait faire ${VERITE.piece.surface_m2} m² ; l'écran affiche « ${surfaceLue} »`,
  ).toBeLessThan(0.02)
  await capturer(
    page,
    'surface',
    `le contour du séjour — ${surfaceLue} pour ${VERITE.piece.surface_m2} m² attendus`,
  )

  // ---- 6. vérifier le tracé avant de décider
  await longueur.getByTestId('pdf-montrer').click()
  await expect(page.getByTestId('pdf-mesure-regardee')).toBeVisible()
  await expect(page.getByTestId('pdf-sommet-mesure').first()).toBeVisible()
  await capturer(
    page,
    'verifier-le-trace',
    'le tracé et ses extrémités, affichés sur le plan et dans la loupe, à côté de la valeur',
  )

  // ---- 7. la correction : le motif est EXIGÉ, et la proposition survit
  await expect(longueur.getByTestId('pdf-corriger')).toBeDisabled()
  await longueur.getByTestId('pdf-motif-decision').fill(MOTIF_DE_CORRECTION)
  await longueur.getByTestId('pdf-correction').fill(VALEUR_CORRIGEE)
  await longueur.getByTestId('pdf-corriger').click()

  const corrigee = ligneDeMesure(page, LONGUEUR)
  await expect(corrigee.getByTestId('pdf-decision')).toContainText('corrigée', {
    timeout: DELAI_ANALYSE,
  })
  await expect(corrigee.getByTestId('pdf-valeur')).toHaveText(valeurLue)
  await expect(corrigee.getByTestId('pdf-valeur-retenue')).toContainText('020')
  await expect(corrigee.getByTestId('pdf-motif-retenu')).toContainText('décamètre')
  await expect(corrigee.getByTestId('pdf-reprenable')).toHaveAttribute('data-reprenable', 'oui')
  await capturer(
    page,
    'correction',
    'la mesure calculée ET la valeur retenue, en deux colonnes, avec le motif',
  )

  // ---- 8. le rejet : sans valeur de remplacement, mais avec un motif
  await expect(surface.getByTestId('pdf-rejeter')).toBeDisabled()
  await surface.getByTestId('pdf-motif-decision').fill(MOTIF_DE_REJET)
  await surface.getByTestId('pdf-rejeter').click()

  const rejetee = ligneDeMesure(page, SURFACE)
  await expect(rejetee.getByTestId('pdf-decision')).toContainText('rejetée', {
    timeout: DELAI_ANALYSE,
  })
  await expect(rejetee.getByTestId('pdf-valeur')).toHaveText(surfaceLue)
  // Rejetée, et DITE non reprenable : c'est la règle du produit, écrite à
  // l'écran et non seulement dans un guide.
  await expect(rejetee.getByTestId('pdf-reprenable')).toHaveAttribute('data-reprenable', 'non')
  await capturer(
    page,
    'rejet',
    'la surface rejetée : elle n’alimentera aucun bordereau, et reste lisible',
  )

  console.log(`\n  ${numero} captures dans ${SORTIE}`)
})
