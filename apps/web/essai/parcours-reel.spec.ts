import { execFileSync } from 'node:child_process'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

import { expect, test, type Locator, type Page } from '@playwright/test'

/**
 * L'essai sur un plan RÉEL, du dépôt du PDF au PDF du devis, photographié.
 *
 * **Ce qui est rejoué ici, et par qui.** Les gestes d'une personne : déposer,
 * lire, déclarer l'échelle dans la loupe, pointer, trancher, reprendre,
 * chiffrer, geler, émettre, télécharger. Les points visés viennent du plan
 * d'essai, un fichier PRIVÉ que l'opérateur a écrit ; le navigateur clique sur
 * un pixel ENTIER de la loupe, comme une souris, et c'est l'écran qui convertit
 * ce pixel en point du plan. Ce qui sépare ce clic de la cible est donc
 * l'erreur de quantification d'un pointage parfait — la plus petite erreur
 * possible. Une main en ajoute une autre, que cet essai ne simule pas.
 *
 * **Aucune assertion de précision.** Ce fichier ne juge pas les nombres : il
 * les lit à l'écran et les écrit dans `valeurs-lues.json`. Le jugement —
 * attendu, mesuré, écart — est rendu par `scripts/essai_sur_plans_reels.py
 * rapport`, contre des références que l'opérateur a écrites lui-même.
 *
 * Voir `playwright.essai.config.ts` pour ce qui garde ses sorties hors du dépôt.
 */

type Point = { x: number; y: number }
type Trace = { libelle: string; points: Point[] }

type PlanDEssai = {
  fichier_pdf: string
  projet: { reference: string; nom: string; client: string }
  calibration: {
    points: [Point, Point]
    distance: string
    unite: string
    motif: string
  }
  segments: Trace[]
  surface?: Trace
  repetitions: {
    libelle: string
    points: [Point, Point]
    decalages: [number, number][]
  }
  decisions: { libelle: string; motif: string }[]
  reprise: {
    libelle: string
    unite: string
    position: string
    designation: string
    prix_code: string
    /** L'écriture que la personne retient, dans le ± de la mesure — ex. « 6,38 ». */
    quantite_retenue?: string
  }
  emission: { valable_jusqu_au: string }
  dxf_url?: string
  email: string
}

const PLAN = JSON.parse(
  readFileSync(resolve(process.env.METREO_ESSAI_PLAN ?? ''), 'utf8'),
) as PlanDEssai
const SORTIE = resolve(process.env.METREO_ESSAI_CAPTURES ?? '')
mkdirSync(SORTIE, { recursive: true, mode: 0o700 })
const PYTHON =
  process.env.METREO_PYTHON ?? join(__dirname, '..', '..', '..', '.venv', 'bin', 'python')

/** L'analyse d'un grand plan, et la première tuile d'une page, prennent des secondes. */
const DELAI = 120_000

let numero = 0
const legendes: string[] = []
const lues: Record<string, Record<string, string>> = {}

async function photo(page: Page, nom: string, quoi: string): Promise<void> {
  numero += 1
  const fichier = join(SORTIE, `${String(numero).padStart(2, '0')}-${nom}.png`)
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: fichier, fullPage: true })
  legendes.push(`${String(numero).padStart(2, '0')} — ${quoi}`)
  console.log(`  capture ${String(numero).padStart(2, '0')} — ${quoi}`)
}

/**
 * Un clic sur un pixel ENTIER : une souris ne pointe pas entre deux pixels.
 *
 * La position demandée est arrondie dans le repère de la fenêtre, puis
 * rapportée à l'élément. Sans cet arrondi, le navigateur accepterait une
 * position fractionnaire, et l'essai mesurerait mieux qu'aucune main.
 */
async function cliquerAuPixel(
  element: Locator,
  fractionX: number,
  fractionY: number,
): Promise<void> {
  const boite = await element.boundingBox()
  expect(boite, "l'élément visé n'est pas rendu").not.toBeNull()
  if (!boite) return
  const x = Math.round(boite.x + boite.width * fractionX) - boite.x
  const y = Math.round(boite.y + boite.height * fractionY) - boite.y
  await element.click({ position: { x, y } })
}

async function zoneRendue(page: Page): Promise<[number, number, number, number]> {
  const image = page.getByTestId('pdf-loupe-image')
  await expect(image).toHaveAttribute('data-zone-declaree', 'oui')
  const valeurs = ((await image.getAttribute('data-zone')) ?? '').split(',').map(Number)
  expect(valeurs).toHaveLength(4)
  return valeurs as [number, number, number, number]
}

/** Ouvre la loupe autour d'un point de l'aperçu — décalé, pour une répétition. */
async function loupeSur(
  page: Page,
  cible: Point,
  decalage: [number, number] = [0, 0],
): Promise<void> {
  await cliquerAuPixel(page.getByTestId('pdf-apercu'), cible.x + decalage[0], cible.y + decalage[1])
  await expect(page.getByTestId('pdf-loupe-panneau')).toBeVisible()
  await expect(page.getByTestId('pdf-loupe-image')).toHaveAttribute('data-prete', 'oui', {
    timeout: DELAI,
  })
  await expect(page.getByTestId('pdf-loupe-rendu')).toBeVisible({
    timeout: DELAI,
  })
}

/** Pointe la cible dans la loupe ouverte, dans le repère que l'image déclare couvrir. */
async function pointer(page: Page, cible: Point): Promise<void> {
  const [x0, y0, x1, y1] = await zoneRendue(page)
  const fx = (cible.x - x0) / (x1 - x0)
  const fy = (cible.y - y0) / (y1 - y0)
  expect(
    fx >= 0 && fx <= 1 && fy >= 0 && fy <= 1,
    `la cible ${cible.x}, ${cible.y} sort de la zone rendue ${x0}, ${y0}, ${x1}, ${y1}`,
  ).toBeTruthy()
  await cliquerAuPixel(page.getByTestId('pdf-loupe-image'), fx, fy)
}

function ligneDeMesure(page: Page, libelle: string): Locator {
  return page.getByTestId('pdf-mesure').filter({ hasText: libelle }).first()
}

async function lireLaMesure(page: Page, libelle: string): Promise<Record<string, string>> {
  const ligne = ligneDeMesure(page, libelle)
  await expect(ligne).toBeVisible({ timeout: DELAI })
  const lu = {
    valeur: (await ligne.getByTestId('pdf-valeur').innerText()).trim(),
    incertitude: (await ligne.getByTestId('pdf-incertitude').innerText()).trim(),
    fiabilite: (await ligne.getByTestId('pdf-fiabilite').innerText()).trim(),
  }
  lues[libelle] = lu
  return lu
}

async function tracer(
  page: Page,
  outil: string,
  trace: Trace,
  decalage?: [number, number],
): Promise<void> {
  await page.getByTestId(outil).click()
  for (const point of trace.points) {
    await loupeSur(page, point, decalage)
    await pointer(page, point)
  }
  const formulaire = page.getByTestId('pdf-formulaire-mesure')
  await expect(formulaire).toBeVisible()
  await formulaire.getByLabel('Ce que vous mesurez').fill(trace.libelle)
  await formulaire.getByTestId('pdf-mesurer').click()
  await lireLaMesure(page, trace.libelle)
}

test('un plan réel, de son dépôt au PDF du devis', async ({ page }) => {
  // ---- Connexion, chantier, client, bordereau vide
  await page.goto('/')
  await page.getByLabel('Adresse e-mail').fill(PLAN.email)
  await page.getByRole('button', { name: 'Se connecter' }).click()
  await expect(page).toHaveURL(/\/projets/)

  await page.goto('/projets')
  await page
    .getByRole('button', { name: /nouveau projet/i })
    .first()
    .click()
  await page.getByLabel(/référence/i).fill(PLAN.projet.reference)
  await page.getByLabel(/^nom/i).first().fill(PLAN.projet.nom)
  await page
    .getByRole('button', { name: /^créer$/i })
    .first()
    .click()
  await page.getByRole('link', { name: PLAN.projet.reference }).first().click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}$/)
  const urlChantier = page.url()

  // L'option porte le nom ET la ville : on la retrouve par son texte, pas par son libellé exact.
  const selecteur = page.getByTestId('selecteur-client')
  const fiche = selecteur.locator('option', { hasText: PLAN.projet.client })
  await expect(fiche).toHaveCount(1)
  await selecteur.selectOption((await fiche.getAttribute('value')) ?? '')
  await page.getByRole('button', { name: 'Rattacher' }).click()
  await expect(page.getByTestId('client-du-chantier')).toBeVisible()
  await page
    .getByRole('button', { name: /^créer$/i })
    .first()
    .click()
  await expect(page.getByLabel('Poste')).toBeVisible()

  // ---- Dépôt et lecture
  const documents = page.getByTestId('documents')
  await documents.getByLabel('Catégorie').selectOption('Plan')
  await documents.getByLabel(/Libellé/).fill('Plan réel — PDF')
  await documents.getByLabel('Fichier à joindre').setInputFiles(PLAN.fichier_pdf)
  const depot = documents.locator('tr').filter({ hasText: 'Plan réel — PDF' }).first()
  await expect(depot.getByTestId('documents-lire-plan')).toBeVisible({
    timeout: DELAI,
  })
  await photo(page, 'depot', 'le PDF déposé dans le chantier fictif, et le lien « Lire le plan »')

  await depot.getByTestId('documents-lire-plan').click()
  await page.waitForURL(/\/plans\/[0-9a-f-]{36}\/[0-9a-f-]{36}/)
  await page.getByTestId('plan-analyser').click()
  await expect(page.getByTestId('pdf-apercu-image')).toBeVisible({
    timeout: DELAI,
  })
  lues.extraction = {
    texte: (await page.getByTestId('pdf-extraction').innerText()).trim(),
  }
  await photo(page, 'lecture', 'le plan lu : aperçu, extraction annoncée, aucune échelle encore')

  // ---- L'échelle, sur une cote connue
  const [premier, second] = PLAN.calibration.points
  await page.getByTestId('pdf-outil-calibrer').click()
  await loupeSur(page, premier)
  await pointer(page, premier)
  await photo(
    page,
    'calibration-premier-point',
    'la première extrémité de la cote, pointée dans la loupe',
  )
  await loupeSur(page, second)
  await pointer(page, second)
  const calibration = page.getByTestId('pdf-formulaire-calibration')
  await expect(calibration).toBeVisible()
  await calibration
    .getByLabel('Distance réelle entre les deux points')
    .fill(PLAN.calibration.distance)
  await calibration.getByLabel('Unité').selectOption(PLAN.calibration.unite)
  await calibration.getByLabel('Sur quoi avez-vous calibré ?').fill(PLAN.calibration.motif)
  await calibration.getByTestId('pdf-calibrer').click()
  await expect(page.getByTestId('pdf-facteur')).toBeVisible({ timeout: DELAI })
  lues.echelle = {
    facteur: (await page.getByTestId('pdf-facteur').innerText()).trim(),
  }
  await photo(page, 'echelle', 'l’échelle déclarée sur la cote connue, et le facteur rendu')

  // ---- Les cotes de contrôle, puis la surface
  for (const segment of PLAN.segments) {
    await tracer(page, 'pdf-outil-segment', segment)
    await photo(
      page,
      `mesure-${numero + 1}`,
      `${segment.libelle} — ${lues[segment.libelle]?.valeur}`,
    )
  }
  if (PLAN.surface) {
    await tracer(page, 'pdf-outil-surface', PLAN.surface)
    await photo(page, 'surface', `${PLAN.surface.libelle} — ${lues[PLAN.surface.libelle]?.valeur}`)
  }

  // ---- La même cote, cinq fois, la loupe rouverte ailleurs à chaque fois
  for (const [rang, decalage] of PLAN.repetitions.decalages.entries()) {
    await tracer(
      page,
      'pdf-outil-segment',
      {
        libelle: `${PLAN.repetitions.libelle} ${rang + 1}`,
        points: PLAN.repetitions.points,
      },
      decalage,
    )
  }
  await photo(
    page,
    'repetitions',
    'cinq pointages de la même cote, la loupe rouverte à chaque fois',
  )

  // ---- Regarder une mesure : son tracé sur le plan et dans la loupe
  const regardee = PLAN.segments[0]
  if (regardee) {
    await ligneDeMesure(page, regardee.libelle).getByTestId('pdf-montrer').click()
    await expect(page.getByTestId('pdf-mesure-regardee')).toBeVisible()
    const depart = regardee.points[0]
    if (depart) await loupeSur(page, depart)
    await photo(
      page,
      'trace',
      `le tracé de « ${regardee.libelle} », redessiné sur le plan et dans la loupe`,
    )
  }

  // ---- Trancher
  for (const decision of PLAN.decisions) {
    const ligne = ligneDeMesure(page, decision.libelle)
    await ligne.getByTestId('pdf-motif-decision').fill(decision.motif)
    await ligne.getByTestId('pdf-confirmer-mesure').click()
    await expect(ligneDeMesure(page, decision.libelle).getByTestId('pdf-decision')).toContainText(
      'confirmée',
      { timeout: DELAI },
    )
  }
  await photo(page, 'decisions', 'les mesures retenues, confirmées avec leur motif')

  // ---- Reprendre au bordereau : le nombre est montré AVANT d'être écrit
  const retenue = ligneDeMesure(page, PLAN.reprise.libelle)
  await retenue.getByTestId('pdf-ouvrir-reprise').click()
  const reprise = page.getByTestId('pdf-formulaire-reprise')
  await expect(reprise).toBeVisible()
  await reprise.getByTestId('pdf-reprise-unite').selectOption(PLAN.reprise.unite)
  await reprise.getByTestId('pdf-reprise-position').fill(PLAN.reprise.position)
  await reprise.getByTestId('pdf-reprise-designation').fill(PLAN.reprise.designation)
  const apercu = page.getByTestId('pdf-apercu-quantite')
  await expect(apercu).toContainText(PLAN.reprise.unite === 'm2' ? 'm' : PLAN.reprise.unite, {
    timeout: DELAI,
  })
  const champRetenue = reprise.getByTestId('pdf-reprise-quantite')
  await expect(champRetenue).toBeEnabled({ timeout: DELAI })
  lues.reprise = {
    brute: (await page.getByTestId('pdf-apercu-brute').innerText()).trim(),
    proposee: await champRetenue.inputValue(),
    apercu: (await apercu.innerText()).trim(),
    provenance: (await page.getByTestId('pdf-apercu-provenance').innerText()).trim(),
  }
  await photo(
    page,
    'reprise-proposee',
    'la mesure brute et son ±, et la quantité proposée à sa finesse, avant toute écriture',
  )
  if (PLAN.reprise.quantite_retenue) {
    await champRetenue.fill(PLAN.reprise.quantite_retenue)
    // Le serveur écrit à la belge, sans zéro de fin : « 6.38 » ou « 6,380 »
    // dans le plan d'essai s'affichent « 6,38 ».
    const ecritureAttendue = PLAN.reprise.quantite_retenue
      .trim()
      .replace(/[\s\u00a0]/g, '')
      .replace(',', '.')
      .replace(/(\.\d*?)0+$/, '$1')
      .replace(/\.$/, '')
      .replace('.', ',')
    await expect(apercu).toContainText(ecritureAttendue, { timeout: DELAI })
    await expect(reprise.getByTestId('notice-erreur')).toHaveCount(0)
    lues.reprise.retenue = PLAN.reprise.quantite_retenue
    lues.reprise.apercu = (await apercu.innerText()).trim()
    lues.reprise.provenance = (await page.getByTestId('pdf-apercu-provenance').innerText()).trim()
    await photo(
      page,
      'reprise-retenue',
      'la quantité retenue par la personne, dans le ± de la mesure, avant toute écriture',
    )
  }
  await reprise.getByTestId('pdf-reprendre').click()
  await expect(retenue.getByTestId('pdf-reprise-faite')).toBeVisible({
    timeout: DELAI,
  })

  // ---- Le bordereau, le prix FICTIF, l'étude, le gel, l'émission
  await page.goto(urlChantier)
  const ligne = page.locator('tr').filter({ hasText: PLAN.reprise.position }).first()
  await expect(ligne.getByTestId('boq-provenance')).toBeVisible({
    timeout: DELAI,
  })
  lues.bordereau = {
    ligne: (await ligne.innerText()).replace(/\s+/g, ' ').trim(),
  }
  await photo(page, 'bordereau', 'la ligne au bordereau, avec le badge « mesure de plan »')

  await ligne.getByRole('button', { name: 'Changer' }).click()
  const source = page.getByTestId(`source-poste-${PLAN.reprise.position}`)
  await source.locator('select').first().selectOption('library')
  const choix = source.locator('select').nth(1)
  const option = choix.locator('option', { hasText: PLAN.reprise.prix_code })
  await expect(option).toHaveCount(1)
  await choix.selectOption((await option.getAttribute('value')) ?? '')
  await source.getByRole('button', { name: 'Enregistrer' }).click()
  await expect(ligne).toContainText(PLAN.reprise.prix_code, { timeout: DELAI })
  await photo(page, 'prix-fictif', 'le poste chiffré par un PRIX FICTIF de la bibliothèque d’essai')

  await page.getByRole('button', { name: 'Créer une étude de prix' }).click()
  await page.getByRole('link', { name: 'Ouvrir' }).first().click()
  await page.waitForURL(/\/estimations\//)
  await page.locator('table.totals tr').first().waitFor({ timeout: DELAI })
  await photo(page, 'etude', 'l’étude de prix calculée sur la quantité reprise')

  await page.getByRole('button', { name: 'Geler cette version' }).click()
  await page.getByRole('button', { name: /confirmer/i }).click()
  await expect(page.getByText('Gelée', { exact: true })).toBeVisible({
    timeout: DELAI,
  })
  const emission = page.getByTestId('emission-du-devis')
  await emission.getByRole('button', { name: 'Émettre le devis' }).click()
  await page.getByLabel('Valable jusqu’au').fill(PLAN.emission.valable_jusqu_au)
  await page.getByTestId('confirmer-l-emission').click()
  await expect(page.getByTestId('devis-emis')).toBeVisible({ timeout: DELAI })
  await photo(page, 'devis-emis', 'la version gelée, et le devis émis')

  const [telechargement] = await Promise.all([
    page.waitForEvent('download'),
    page.getByTestId('telecharger-le-devis').click(),
  ])
  const pdf = join(SORTIE, 'devis.pdf')
  await telechargement.saveAs(pdf)
  // La première page du devis, rendue en image pour le compte rendu.
  execFileSync(PYTHON, [
    '-I',
    '-c',
    'import sys, pypdfium2 as p; d = p.PdfDocument(sys.argv[1]); ' +
      'd[0].render(scale=2).to_pil().save(sys.argv[2])',
    pdf,
    join(SORTIE, `${String(numero + 1).padStart(2, '0')}-devis-pdf.png`),
  ])
  numero += 1
  legendes.push(`${String(numero).padStart(2, '0')} — la première page du PDF du devis`)

  // ---- Le DXF du même dessin, tel que le lecteur l'a compris
  if (PLAN.dxf_url) {
    await page.goto(PLAN.dxf_url)
    await expect(
      page.getByTestId('plan-constat').or(page.getByTestId('plan-mesures')).first(),
    ).toBeVisible({
      timeout: DELAI,
    })
    await photo(page, 'dxf', 'le DXF du même dessin : unité, entités et cotations proposées')
  }

  writeFileSync(join(SORTIE, 'valeurs-lues.json'), JSON.stringify(lues, null, 2), { mode: 0o600 })
  writeFileSync(join(SORTIE, 'legendes.txt'), legendes.join('\n') + '\n', {
    mode: 0o600,
  })
})
