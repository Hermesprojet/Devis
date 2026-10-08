import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { expect, type Locator, type Page } from '@playwright/test'

/**
 * Mesurer un plan dont on connaît les dimensions, par le navigateur.
 *
 * **Pourquoi ce module existe.** Le scénario chiffré ne vivait que dans
 * `captures/parcours-pdf.spec.ts`, c'est-à-dire dans un fichier qu'aucune des
 * deux configurations de CI ne ramasse : il fallait le demander à la main. La
 * seule épreuve qui comparait la mesure affichée à une longueur connue ne
 * tombait donc jamais à la livraison, et une régression de pointage pouvait
 * passer les douze contrôles verts.
 *
 * Le scénario est écrit ici une fois, et appelé deux fois :
 *
 *  - `e2e-premier-devis/suite-mesure-juste-pdf.spec.ts` le joue **à chaque
 *    livraison** et n'en garde que les comparaisons ;
 *  - `captures/parcours-pdf.spec.ts` le joue à la demande et photographie
 *    chaque étape — une sortie complémentaire, jamais la preuve elle-même.
 *
 * **La vérité ne vient pas de Metreo.** `plan_batiment.json` est écrit par
 * `scripts/fabriquer_plans_de_test.py` à côté du PDF, en points de papier,
 * sans passer par le lecteur. Recopier ses chiffres dans un test ferait deux
 * sources, et la seconde finirait par mentir.
 *
 * **Ce que ce module ne prouve pas.** Que Metreo mesure juste sur un plan
 * d'exécution réel. Il prouve que la chaîne complète — écran, repère,
 * calibration, calcul, décision — retombe sur une géométrie connue. L'écart
 * entre les deux est l'erreur de pointage d'un humain sur un vrai dessin, et
 * elle se relève sur de vraies cotes : `docs/VALIDATION_SUR_PLANS_REELS.md`.
 */

const FIXTURES = join(__dirname, '..', '..', '..', 'fixtures', 'plans')

/** Le PDF coté, fabriqué par le dépôt et jamais commité. */
export const CHEMIN_DU_PLAN = join(FIXTURES, 'plan_batiment.pdf')

export type VeriteDuPlan = {
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

export const VERITE: VeriteDuPlan = JSON.parse(
  readFileSync(join(FIXTURES, 'plan_batiment.json'), 'utf8'),
) as VeriteDuPlan

/** L'analyse est synchrone et dure des secondes ; la première tuile aussi. */
export const DELAI_ANALYSE = 60_000

const MOTIF_DE_CALIBRATION = 'Cote 5000 du plan RDC, pointée à ses deux extrémités.'
// Deux libellés sans mot commun : `hasText` de Playwright cherche une
// SOUS-CHAÎNE, et « Séjour » retrouvait « Façade du séjour ».
export const LONGUEUR = 'Mur avant'
export const SURFACE = 'Séjour'
const MOTIF_DE_CORRECTION = 'Relevé sur place au décamètre : 6,02 m et non 6,00.'
const VALEUR_CORRIGEE = '6020'
const MOTIF_DE_REJET = 'Contour pointé sur le mauvais local : cette surface ne veut rien dire.'

/**
 * Les tolérances, et d'où elles viennent.
 *
 * Elles ne sont pas des tolérances MÉTIER — le propriétaire n'a fixé la sienne
 * nulle part. Ce sont les bornes au-delà desquelles l'écart cesse d'être une
 * erreur de pointage d'un pixel et devient un défaut de la chaîne : un repère
 * inversé, une zone désynchronisée, un facteur appliqué deux fois.
 *
 * Mesuré sur la fixture : la loupe couvre 21 points de papier pour environ
 * 418 pixels affichés, soit 0,05 point par pixel, soit **1,25 mm d'ouvrage par
 * pixel** à l'échelle de ce plan. Quatre clics de surface en cumulent donc
 * quelques millimètres sur 6 000, c'est-à-dire **moins d'un millième**. Les
 * bornes ci-dessous laissent dix fois cette marge, et rien de plus.
 */
export const TOLERANCE_DE_LONGUEUR = 0.01
export const TOLERANCE_DE_SURFACE = 0.02

/**
 * Ce que le scénario a lu à l'écran, rendu à l'appelant.
 *
 * Rendu, et non seulement vérifié : le banc de captures écrit ces nombres dans
 * la légende de ses images, et le document de précision les recopie.
 */
export type MesuresLues = {
  longueurAffichee: string
  longueurEnMm: number
  surfaceAffichee: string
  surfaceEnM2: number
}

/** Ce que l'appelant fait d'une étape franchie. Le banc photographie ; la CI non. */
export type Jalon = (nom: string, quoi: string) => Promise<void>

export const SANS_JALON: Jalon = async () => {}

/**
 * D'un point du DESSIN (points PostScript) à une fraction de l'aperçu.
 *
 * La page de la fixture n'est ni décalée ni tournée : la conversion est celle
 * de `lecture_pdf._VERS_L_ECRAN` pour une rotation nulle — l'abscisse telle
 * quelle, l'ordonnée renversée, parce qu'un PDF compte depuis le bas et un
 * écran depuis le haut.
 */
export function versLEcran([u, v]: [number, number]): { x: number; y: number } {
  const [largeur, hauteur] = VERITE.page
  return { x: u / largeur, y: 1 - v / hauteur }
}

/** Clique à une fraction de la boîte d'un élément. */
export async function cliquerA(
  element: Locator,
  fractionX: number,
  fractionY: number,
): Promise<void> {
  const boite = await element.boundingBox()
  expect(boite, "l'élément visé n'a pas de boîte : il n'est pas rendu").not.toBeNull()
  if (!boite) return
  await element.click({ position: { x: boite.width * fractionX, y: boite.height * fractionY } })
}

/**
 * La zone que l'image AFFICHÉE couvre réellement, lue dans le DOM.
 *
 * **C'est la correction centrale de ce fichier.** L'ancienne version
 * recalculait la fenêtre de la loupe — centre ± 2,5 % de la page, borné aux
 * bords — et pointait dans ce repère supposé. Il ne coïncide pas avec celui du
 * rendu : un bitmap se compte en pixels entiers, et une zone de 21 × 16 points
 * demandée au facteur 24,381 revient en 511 × 389 pixels, soit 20,959 × 15,955
 * points. Le banc visait donc un point 0,2 % à côté de celui qu'il croyait
 * viser, systématiquement et sans jamais s'en apercevoir.
 *
 * L'écran publie désormais la zone que le serveur a déclarée pour cette image
 * précise (`data-zone`), et le banc pointe dans CE repère.
 */
export async function zoneRendue(page: Page): Promise<[number, number, number, number]> {
  const image = page.getByTestId('pdf-loupe-image')
  const declaree = await image.getAttribute('data-zone-declaree')
  expect(declaree, "l'agrandissement n'a pas déclaré la zone qu'il couvre").toBe('oui')
  const brut = await image.getAttribute('data-zone')
  const valeurs = (brut ?? '').split(',').map(Number)
  expect(valeurs, 'la zone déclarée doit porter quatre nombres').toHaveLength(4)
  const [x0, y0, x1, y1] = valeurs as [number, number, number, number]
  expect(x1 > x0 && y1 > y0, `zone déclarée vide : ${brut}`).toBeTruthy()
  return [x0, y0, x1, y1]
}

/**
 * Ouvre la loupe centrée sur un point du dessin, et attend **la bonne image**.
 *
 * Attendre « une image visible » ne suffisait pas, et c'est ce que l'ancienne
 * version faisait : pendant le chargement, l'image PRÉCÉDENTE restait affichée
 * et passait le contrôle. Le banc pointait alors sur un dessin qui n'était plus
 * celui de la zone demandée. L'écran marque maintenant l'image prête
 * (`data-prete="oui"`) seulement quand le rendu correspond à la page, à la
 * révision et à la zone courantes ; c'est cela qu'on attend ici.
 */
export async function loupeSur(page: Page, point: [number, number]): Promise<Locator> {
  const cible = versLEcran(point)
  await cliquerA(page.getByTestId('pdf-apercu'), cible.x, cible.y)
  await expect(page.getByTestId('pdf-loupe-panneau')).toBeVisible()
  const image = page.getByTestId('pdf-loupe-image')
  await expect(image).toHaveAttribute('data-prete', 'oui', { timeout: DELAI_ANALYSE })
  await expect(page.getByTestId('pdf-loupe-rendu')).toBeVisible({ timeout: DELAI_ANALYSE })
  return image
}

/**
 * Clique un point du DESSIN dans la loupe ouverte, dans le repère du rendu.
 *
 * Un point hors de la zone réellement rendue est une erreur du scénario, pas
 * de l'écran : on le dit, plutôt que de cliquer au bord et de mesurer autre
 * chose que ce qui était visé.
 */
export async function pointerDansLaLoupe(page: Page, cible: [number, number]): Promise<void> {
  const [x0, y0, x1, y1] = await zoneRendue(page)
  const point = versLEcran(cible)
  const fractionX = (point.x - x0) / (x1 - x0)
  const fractionY = (point.y - y0) / (y1 - y0)
  expect(
    fractionX >= 0 && fractionX <= 1 && fractionY >= 0 && fractionY <= 1,
    `le point ${cible} tombe hors de la zone rendue [${x0}, ${y0}, ${x1}, ${y1}]`,
  ).toBeTruthy()
  await cliquerA(page.getByTestId('pdf-loupe-image'), fractionX, fractionY)
}

/** Le nombre porté par un texte français : « 6 004,2 mm » → 6004.2 */
export function nombreDe(texte: string): number {
  const nettoye = texte.replace(/[  \s]/g, '').replace(',', '.')
  const trouve = /-?\d+(\.\d+)?/.exec(nettoye)
  expect(trouve, `aucun nombre dans « ${texte} »`).not.toBeNull()
  return Number(trouve?.[0])
}

export function ligneDeMesure(page: Page, libelle: string): Locator {
  return page.getByTestId('pdf-mesure').filter({ hasText: libelle }).first()
}

/**
 * Dépose le plan coté dans le projet ouvert et ouvre son écran de lecture.
 *
 * L'appelant a déjà ouvert un projet : la connexion diffère entre les deux
 * configurations — jeu de démonstration d'un côté, OpenID Connect de l'autre —
 * et c'est la seule chose qui diffère.
 */
export async function deposerEtLire(page: Page, libelle: string, jalon: Jalon): Promise<void> {
  const documents = page.getByTestId('documents')
  await expect(documents).toBeVisible()
  await documents.getByLabel('Catégorie').selectOption('Plan')
  await documents.getByLabel(/Libellé/).fill(libelle)
  await documents.getByLabel('Fichier à joindre').setInputFiles(CHEMIN_DU_PLAN)

  // Filtrée par le LIBELLÉ, qui est propre à l'appelant, et non par le nom de
  // fichier. Le même `plan_batiment.pdf` est déposé par plusieurs scénarios
  // dans la même organisation : `.first()` sur le nom de fichier retrouvait le
  // document d'un scénario PRÉCÉDENT, déjà analysé, et « Analyser » n'y
  // apparaissait jamais. Mesuré — trois minutes d'attente pour un locator
  // introuvable, et un diagnostic qui ne désignait pas la cause.
  const ligne = documents.locator('tr').filter({ hasText: libelle }).first()
  await expect(ligne).toBeVisible()
  // Et le nom de fichier est vérifié SUR cette ligne : c'est ce qui garantit
  // que le libellé et le fichier appartiennent bien au même dépôt.
  await expect(ligne.getByRole('cell', { name: 'plan_batiment.pdf' })).toBeVisible()
  await expect(ligne.getByTestId('documents-lire-plan')).toBeVisible()
  await jalon('depot', 'le PDF déposé, et le lien « Lire le plan » qui y mène')

  await ligne.getByTestId('documents-lire-plan').click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}\/plans\/[0-9a-f-]{36}\/[0-9a-f-]{36}/)
  await page.getByTestId('plan-analyser').click()

  await expect(page.getByTestId('pdf-lecture')).toBeVisible({ timeout: DELAI_ANALYSE })
  await expect(page.getByTestId('pdf-apercu-image')).toBeVisible({ timeout: DELAI_ANALYSE })
}

/**
 * Ce que l'écran annonce AVANT toute échelle, et la description de l'extraction.
 *
 * Le bandeau DXF n'a plus sa place ici : il annonçait « Aucune mesure n'est
 * exploitable » au-dessus d'un écran dont mesurer est l'unique objet. Et
 * l'en-tête ne porte plus le verdict « probablement scanné » : le PDF fabriqué
 * contient de la géométrie vectorielle et quatre textes, et leur petit nombre
 * ne permet aucune conclusion sur sa nature.
 */
export async function verifierCeQuiEstAnnonceAvantToute(page: Page): Promise<void> {
  await expect(page.getByTestId('plan-constat')).toHaveCount(0)
  await expect(page.getByTestId('plan-introduction')).toContainText('POINTEZ')
  await expect(page.getByTestId('pdf-echelle')).toContainText('rien ne peut être mesuré')
  await expect(page.getByTestId('pdf-etape-echelle')).toHaveAttribute('data-etat', 'en-cours')

  const extraction = page.getByTestId('pdf-extraction')
  await expect(extraction).toBeVisible()
  await expect(extraction).not.toContainText('scanné')

  // **Des comptes NON NULS, et pas seulement la phrase.** Les quatre faits de
  // l'extraction avaient été oubliés dans la recopie de l'artefact côté
  // serveur : `PlanLu` leur donne des défauts, et l'écran affichait
  // « 4 fragment(s), 0 caractère(s) · 0 tracé(s) vectoriel(s) » sur un plan qui
  // en porte vingt-sept et quatre. Une assertion sur la seule phrase passait.
  const texte = (await extraction.innerText()).trim()
  expect(texte, 'le compte de caractères extraits doit être non nul').toMatch(
    /[1-9]\d* caractère\(s\)/,
  )
  expect(texte, 'le compte de tracés vectoriels doit être non nul').toMatch(
    /[1-9]\d* tracé\(s\) vectoriel\(s\)/,
  )
}

/**
 * Calibre sur les deux extrémités de la ligne de cote du dessin.
 *
 * La cote fait 200 points et la loupe en couvre 21 : les deux extrémités
 * n'entrent pas dans la même fenêtre. On pointe la première, on DÉPLACE la
 * loupe, on pointe la seconde — et les points posés survivent au déplacement.
 * C'est le geste réel sur un plan de grand format.
 */
export async function calibrerSurLaCote(page: Page, jalon: Jalon): Promise<void> {
  const { premier, second, longueur_mm } = VERITE.cote
  await page.getByTestId('pdf-outil-calibrer').click()

  await loupeSur(page, premier)
  await pointerDansLaLoupe(page, premier)
  await jalon(
    'calibration-premier-point',
    'la première extrémité de la cote, pointée dans la loupe',
  )

  await loupeSur(page, second)
  await pointerDansLaLoupe(page, second)

  const formulaire = page.getByTestId('pdf-formulaire-calibration')
  await expect(formulaire).toBeVisible()
  await formulaire.getByLabel('Distance réelle entre les deux points').fill(String(longueur_mm))
  await formulaire.getByLabel('Unité').selectOption('mm')
  await formulaire.getByLabel('Sur quoi avez-vous calibré ?').fill(MOTIF_DE_CALIBRATION)
  await jalon('calibration', 'la seconde extrémité pointée, et la distance réelle déclarée à la main')

  await formulaire.getByTestId('pdf-calibrer').click()
  await expect(page.getByTestId('pdf-facteur')).toBeVisible({ timeout: DELAI_ANALYSE })
  await expect(page.getByTestId('pdf-facteur')).toContainText('mm par point')
  await expect(page.getByTestId('pdf-etape-echelle')).toHaveAttribute('data-etat', 'faite')
  await jalon('echelle-confirmee', 'l’échelle déclarée, son facteur et son motif')
}

function coinsDeLaPiece(): [number, number][] {
  const coins = VERITE.piece.coins
  expect(coins, 'la pièce de la fixture a quatre coins').toHaveLength(4)
  return coins
}

/** Mesure la façade — 6 000 mm attendus — et compare. */
export async function mesurerLaFacade(page: Page, jalon: Jalon): Promise<[string, number]> {
  const [coinA, coinB] = coinsDeLaPiece()
  if (!coinA || !coinB) throw new Error('coins manquants')

  await page.getByTestId('pdf-outil-segment').click()
  await loupeSur(page, coinA)
  await pointerDansLaLoupe(page, coinA)
  await loupeSur(page, coinB)
  await pointerDansLaLoupe(page, coinB)

  const formulaire = page.getByTestId('pdf-formulaire-mesure')
  await expect(formulaire).toBeVisible()
  await formulaire.getByLabel('Ce que vous mesurez').fill(LONGUEUR)
  await formulaire.getByTestId('pdf-mesurer').click()

  const ligne = ligneDeMesure(page, LONGUEUR)
  await expect(ligne).toBeVisible({ timeout: DELAI_ANALYSE })
  const affichee = (await ligne.getByTestId('pdf-valeur').innerText()).trim()
  expect(affichee, 'une longueur est rendue en millimètres').toContain('mm')
  const mesuree = nombreDe(affichee)
  const attendue = VERITE.piece.largeur_mm

  // **La comparaison qui fait de ce parcours autre chose qu'une démonstration.**
  expect(
    Math.abs(mesuree - attendue) / attendue,
    `la façade devrait mesurer ${attendue} mm ; l'écran affiche « ${affichee} »`,
  ).toBeLessThan(TOLERANCE_DE_LONGUEUR)

  // Mesurée mais non tranchée : elle n'alimente encore aucun bordereau.
  await expect(ligne.getByTestId('pdf-reprenable')).toHaveAttribute('data-reprenable', 'non')
  await expect(ligne.getByTestId('pdf-incertitude')).toContainText('±')
  await jalon(
    'longueur',
    `la façade mesurée — ${affichee} pour ${attendue} mm attendus — avec son incertitude`,
  )
  return [affichee, mesuree]
}

/** Mesure le contour de la pièce — 24,00 m² attendus — et compare. */
export async function mesurerLeSejour(page: Page, jalon: Jalon): Promise<[string, number]> {
  await page.getByTestId('pdf-outil-surface').click()
  for (const coin of coinsDeLaPiece()) {
    await loupeSur(page, coin)
    await pointerDansLaLoupe(page, coin)
  }

  const formulaire = page.getByTestId('pdf-formulaire-mesure')
  await expect(formulaire).toBeVisible()
  await formulaire.getByLabel('Ce que vous mesurez').fill(SURFACE)
  await formulaire.getByTestId('pdf-mesurer').click()

  const ligne = ligneDeMesure(page, SURFACE)
  await expect(ligne).toBeVisible({ timeout: DELAI_ANALYSE })
  const affichee = (await ligne.getByTestId('pdf-valeur').innerText()).trim()
  expect(affichee, 'une surface est rendue en mètres carrés').toContain('m²')
  const mesuree = nombreDe(affichee)
  const attendue = VERITE.piece.surface_m2
  expect(
    Math.abs(mesuree - attendue) / attendue,
    `le séjour devrait faire ${attendue} m² ; l'écran affiche « ${affichee} »`,
  ).toBeLessThan(TOLERANCE_DE_SURFACE)
  await jalon('surface', `le contour du séjour — ${affichee} pour ${attendue} m² attendus`)
  return [affichee, mesuree]
}

/**
 * Le tracé est montré SUR l'image, et dans la loupe, à côté de sa valeur.
 *
 * C'est la correspondance image–tracé : un nombre juste posé sur le mauvais
 * dessin reste un nombre faux, et la seule façon de le voir est de redessiner
 * la mesure là où elle a été prise.
 */
export async function verifierLeTrace(page: Page, jalon: Jalon): Promise<void> {
  const [coinA] = coinsDeLaPiece()
  if (!coinA) throw new Error('coin manquant')

  await ligneDeMesure(page, LONGUEUR).getByTestId('pdf-montrer').click()
  await expect(page.getByTestId('pdf-mesure-regardee')).toBeVisible()
  await expect(page.getByTestId('pdf-sommet-mesure').first()).toBeVisible()

  // **Dans la loupe aussi, et c'est là que la correspondance se vérifie.**
  //
  // Pas par `toBeVisible()` : un segment HORIZONTAL a une boîte englobante de
  // hauteur nulle, et Playwright le déclare alors caché. Mesuré — le tracé
  // existait, allait de x = −5,21 à x = 6,21 dans le repère de la loupe, et
  // l'assertion échouait pour une raison qui n'avait rien à voir avec ce
  // qu'elle prétendait vérifier.
  //
  // Ce qui est vérifié est la correspondance elle-même : la loupe est recentrée
  // sur un SOMMET de la mesure, et le sommet redessiné doit tomber au bon
  // endroit dans le repère de l'image affichée.
  await expect(page.getByTestId('pdf-loupe-trace-mesure')).toHaveCount(1)
  await loupeSur(page, coinA)

  const [x0, y0, x1, y1] = await zoneRendue(page)
  const attenduX = (versLEcran(coinA).x - x0) / (x1 - x0)
  const attenduY = (versLEcran(coinA).y - y0) / (y1 - y0)

  const sommets = page.getByTestId('pdf-loupe-sommet')
  const nombre = await sommets.count()
  expect(nombre, 'la mesure regardée redessine ses sommets dans la loupe').toBeGreaterThan(0)

  const ecarts: number[] = []
  for (let rang = 0; rang < nombre; rang += 1) {
    const sommet = sommets.nth(rang)
    const cx = Number(await sommet.getAttribute('cx'))
    const cy = Number(await sommet.getAttribute('cy'))
    ecarts.push(Math.hypot(cx - attenduX, cy - attenduY))
  }
  // Un pourcent de la loupe. La borne est choisie, pas arrondie au hasard :
  // le sommet enregistré est celui du clic, quantifié au pixel affiché, soit
  // environ 0,0012 dans ce repère ; un désaccord entre la zone DEMANDÉE et la
  // zone RENDUE vaudrait environ 0,04, soit quarante fois plus. La borne
  // sépare donc les deux sans ambiguïté.
  expect(
    Math.min(...ecarts),
    `aucun sommet redessiné ne tombe sur ${attenduX.toFixed(4)}, ${attenduY.toFixed(4)} ` +
      `(écarts : ${ecarts.map((e) => e.toFixed(4)).join(', ')})`,
  ).toBeLessThan(0.01)

  await jalon(
    'verifier-le-trace',
    'le tracé et ses extrémités, affichés sur le plan et dans la loupe, à côté de la valeur',
  )
}

/** Corrige la longueur : le motif est EXIGÉ, et la proposition survit. */
export async function corrigerLaFacade(
  page: Page,
  longueurAffichee: string,
  jalon: Jalon,
): Promise<void> {
  const ligne = ligneDeMesure(page, LONGUEUR)
  await expect(ligne.getByTestId('pdf-corriger')).toBeDisabled()
  await ligne.getByTestId('pdf-motif-decision').fill(MOTIF_DE_CORRECTION)
  await ligne.getByTestId('pdf-correction').fill(VALEUR_CORRIGEE)
  await ligne.getByTestId('pdf-corriger').click()

  const corrigee = ligneDeMesure(page, LONGUEUR)
  await expect(corrigee.getByTestId('pdf-decision')).toContainText('corrigée', {
    timeout: DELAI_ANALYSE,
  })
  await expect(corrigee.getByTestId('pdf-valeur')).toHaveText(longueurAffichee)
  await expect(corrigee.getByTestId('pdf-valeur-retenue')).toContainText('020')
  await expect(corrigee.getByTestId('pdf-motif-retenu')).toContainText('décamètre')
  await expect(corrigee.getByTestId('pdf-reprenable')).toHaveAttribute('data-reprenable', 'oui')
  await jalon('correction', 'la mesure calculée ET la valeur retenue, en deux colonnes, avec le motif')
}

/** Rejette la surface : sans valeur de remplacement, mais avec un motif. */
export async function rejeterLeSejour(
  page: Page,
  surfaceAffichee: string,
  jalon: Jalon,
): Promise<void> {
  const ligne = ligneDeMesure(page, SURFACE)
  await expect(ligne.getByTestId('pdf-rejeter')).toBeDisabled()
  await ligne.getByTestId('pdf-motif-decision').fill(MOTIF_DE_REJET)
  await ligne.getByTestId('pdf-rejeter').click()

  const rejetee = ligneDeMesure(page, SURFACE)
  await expect(rejetee.getByTestId('pdf-decision')).toContainText('rejetée', {
    timeout: DELAI_ANALYSE,
  })
  await expect(rejetee.getByTestId('pdf-valeur')).toHaveText(surfaceAffichee)
  // Rejetée, et DITE non reprenable : c'est la règle du produit, écrite à
  // l'écran et non seulement dans un guide.
  await expect(rejetee.getByTestId('pdf-reprenable')).toHaveAttribute('data-reprenable', 'non')
  await jalon('rejet', 'la surface rejetée : elle n’alimentera aucun bordereau, et reste lisible')
}

/**
 * Le scénario complet, du dépôt à la décision, sur le projet déjà ouvert.
 *
 * Rend les deux nombres lus à l'écran : l'appelant les compare, les écrit dans
 * une légende, ou les reprend dans un bordereau.
 */
export async function mesurerLePlanConnu(
  page: Page,
  { libelle, jalon = SANS_JALON }: { libelle: string; jalon?: Jalon },
): Promise<MesuresLues> {
  await deposerEtLire(page, libelle, jalon)
  await verifierCeQuiEstAnnonceAvantToute(page)
  await jalon('apercu', 'l’aperçu du plan, le fil d’état à la première étape, et « aucune échelle »')
  await calibrerSurLaCote(page, jalon)

  const [longueurAffichee, longueurEnMm] = await mesurerLaFacade(page, jalon)
  const [surfaceAffichee, surfaceEnM2] = await mesurerLeSejour(page, jalon)

  await verifierLeTrace(page, jalon)
  await corrigerLaFacade(page, longueurAffichee, jalon)
  await rejeterLeSejour(page, surfaceAffichee, jalon)

  return { longueurAffichee, longueurEnMm, surfaceAffichee, surfaceEnM2 }
}
