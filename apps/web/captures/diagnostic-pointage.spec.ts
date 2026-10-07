import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { expect, test, type Locator, type Page } from '@playwright/test'

/**
 * D'où vient l'écart entre ce qu'on vise et ce que Metreo enregistre.
 *
 *     CAPTURES_DIR=/tmp/diag npx playwright test \
 *       --config=playwright.captures.config.ts captures/diagnostic-pointage.spec.ts
 *
 * **Ce fichier ne vérifie rien : il MESURE.** Le parcours de captures relève
 * 6 006,3 mm pour 6 000 attendus — 0,105 % — et 24,060 m² pour 24,000. Un
 * écart de cet ordre a plusieurs causes possibles, et les additionner à vue
 * n'apprend rien. Celui-ci les sépare, en comparant trois choses :
 *
 * 1. le point du DESSIN qu'on vise, en points PostScript ;
 * 2. la fraction d'écran que le scénario calcule et où il clique ;
 * 3. **ce que l'API a réellement enregistré**, relu par `/plan/mesures`.
 *
 * Il imprime aussi la géométrie de la loupe telle que le navigateur l'affiche :
 * taille CSS de l'image, taille naturelle du PNG, et la zone demandée. C'est là
 * que se cache l'écart entre « la zone que j'ai demandée » et « la zone que
 * l'image couvre vraiment ».
 */

const FIXTURES = join(__dirname, '..', '..', '..', 'fixtures', 'plans')
const PLAN = join(FIXTURES, 'plan_batiment.pdf')

type VeriteDuPlan = {
  millimetres_par_point: number
  page: [number, number]
  cote: { premier: [number, number]; second: [number, number]; longueur_mm: number }
  piece: { coins: [number, number][]; largeur_mm: number; surface_m2: number }
}
const VERITE: VeriteDuPlan = JSON.parse(
  readFileSync(join(FIXTURES, 'plan_batiment.json'), 'utf8'),
) as VeriteDuPlan

const TAILLE_DE_LA_LOUPE = 0.05
const DELAI = 60_000

function versLEcran([u, v]: [number, number]): { x: number; y: number } {
  const [largeur, hauteur] = VERITE.page
  return { x: u / largeur, y: 1 - v / hauteur }
}

function fenetreDeLaLoupe(centre: [number, number]) {
  const c = versLEcran(centre)
  const demi = TAILLE_DE_LA_LOUPE / 2
  return {
    x0: Math.max(0, c.x - demi),
    y0: Math.max(0, c.y - demi),
    x1: Math.min(1, c.x + demi),
    y1: Math.min(1, c.y + demi),
  }
}

async function cliquerA(element: Locator, fx: number, fy: number): Promise<void> {
  const boite = await element.boundingBox()
  if (!boite) throw new Error('élément sans boîte')
  await element.click({ position: { x: boite.width * fx, y: boite.height * fy } })
}

async function rects(page: Page, enveloppe: string, image: string) {
  return page.evaluate(
    ([e, i]) => {
      const div = document.querySelector(`[data-testid="${e}"]`) as HTMLElement | null
      const img = document.querySelector(`[data-testid="${i}"]`) as HTMLImageElement | null
      const r = (n: Element | null) => {
        if (!n) return null
        const b = n.getBoundingClientRect()
        return { x: b.x, y: b.y, w: b.width, h: b.height }
      }
      return { enveloppe: r(div), image: r(img), naturelle: img ? { w: img.naturalWidth, h: img.naturalHeight } : null }
    },
    [enveloppe, image],
  )
}

async function loupeSur(page: Page, centre: [number, number]): Promise<void> {
  const c = versLEcran(centre)
  await cliquerA(page.getByTestId('pdf-apercu'), c.x, c.y)
  await expect(page.getByTestId('pdf-loupe-rendu')).toBeVisible({ timeout: DELAI })
}

async function pointer(page: Page, centre: [number, number], cible: [number, number]) {
  const f = fenetreDeLaLoupe(centre)
  const p = versLEcran(cible)
  const fx = (p.x - f.x0) / (f.x1 - f.x0)
  const fy = (p.y - f.y0) / (f.y1 - f.y0)
  const image = page.getByTestId('pdf-loupe-image')
  const boite = await image.boundingBox()
  const naturelle = await page
    .getByTestId('pdf-loupe-rendu')
    .evaluate((n) => ({ w: (n as HTMLImageElement).naturalWidth, h: (n as HTMLImageElement).naturalHeight }))
  await cliquerA(image, fx, fy)
  return {
    visee: { x: p.x, y: p.y },
    fraction: { fx, fy },
    boiteCss: { w: boite?.width ?? 0, h: boite?.height ?? 0 },
    naturelle,
    fenetre: f,
  }
}

test('d’où vient l’écart : viser, cliquer, et relire ce qui a été enregistré', async ({ page }) => {
  test.setTimeout(600_000)

  await page.goto('/')
  await page.getByLabel('Adresse e-mail').fill('admin@dubois.demo')
  await page.getByRole('button', { name: 'Se connecter' }).click()
  await expect(page).toHaveURL(/\/projets/)
  await page.goto('/projets')
  await page.getByRole('button', { name: /nouveau projet/i }).first().click()
  await page.getByLabel(/référence/i).fill('DIAG-PDF')
  await page.getByLabel(/^nom/i).first().fill('Diagnostic du pointage')
  await page.getByRole('button', { name: /^créer$/i }).first().click()
  await page.getByRole('link', { name: 'DIAG-PDF' }).first().click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}$/)

  const documents = page.getByTestId('documents')
  await documents.getByLabel('Catégorie').selectOption('Plan')
  await documents.getByLabel(/Libellé/).fill('Plan RDC')
  await documents.getByLabel('Fichier à joindre').setInputFiles(PLAN)
  const ligne = documents
    .locator('tr')
    .filter({ has: page.getByRole('cell', { name: 'plan_batiment.pdf' }) })
    .first()
  await ligne.getByTestId('documents-lire-plan').click()
  await page.waitForURL(/\/plans\//)
  await page.getByTestId('plan-analyser').click()
  await expect(page.getByTestId('pdf-apercu-image')).toBeVisible({ timeout: DELAI })

  const releves: Record<string, unknown>[] = []

  console.log('\n===== LES BOÎTES : enveloppe contre image =====')
  console.log('aperçu :', JSON.stringify(await rects(page, 'pdf-apercu', 'pdf-apercu-image')))

  // ---- la calibration
  await page.getByTestId('pdf-outil-calibrer').click()
  for (const bout of [VERITE.cote.premier, VERITE.cote.second]) {
    await loupeSur(page, bout)
    releves.push({ quoi: 'calibration', ...(await pointer(page, bout, bout)) })
  }
  console.log('loupe  :', JSON.stringify(await rects(page, 'pdf-loupe-image', 'pdf-loupe-rendu')))

  const formulaire = page.getByTestId('pdf-formulaire-calibration')
  await formulaire.getByLabel('Distance réelle entre les deux points').fill(String(VERITE.cote.longueur_mm))
  await formulaire.getByLabel('Unité').selectOption('mm')
  await formulaire.getByLabel('Sur quoi avez-vous calibré ?').fill('Diagnostic')
  await formulaire.getByTestId('pdf-calibrer').click()
  await expect(page.getByTestId('pdf-facteur')).toBeVisible({ timeout: DELAI })

  // ---- la longueur
  const [coinA, coinB] = VERITE.piece.coins as [[number, number], [number, number]]
  await page.getByTestId('pdf-outil-segment').click()
  for (const coin of [coinA, coinB]) {
    await loupeSur(page, coin)
    releves.push({ quoi: 'longueur', ...(await pointer(page, coin, coin)) })
  }
  const mesure = page.getByTestId('pdf-formulaire-mesure')
  await mesure.getByLabel('Ce que vous mesurez').fill('Mur avant')
  await mesure.getByTestId('pdf-mesurer').click()
  await expect(page.getByTestId('pdf-mesure').first()).toBeVisible({ timeout: DELAI })

  // ---- ce que l'API a VRAIMENT enregistré
  const url = page.url()
  const [, documentId, revisionId] = /\/plans\/([0-9a-f-]{36})\/([0-9a-f-]{36})/.exec(url) ?? []
  const jeton = await page.evaluate(() => window.sessionStorage.getItem("metreo.token"))
  const reponse = await page.request.get(
    `http://127.0.0.1:8055/api/v1/documents/${documentId}/revisions/${revisionId}/plan/mesures`,
    { headers: { Authorization: `Bearer ${jeton}` } },
  )
  const corps = await reponse.json()

  console.log('\n===== CE QUI A ÉTÉ VISÉ, ET CE QUI A ÉTÉ CLIQUÉ =====')
  for (const r of releves) {
    const v = r.visee as { x: number; y: number }
    const f = r.fraction as { fx: number; fy: number }
    const b = r.boiteCss as { w: number; h: number }
    const n = r.naturelle as { w: number; h: number }
    console.log(
      `${String(r.quoi).padEnd(12)} visé (${v.x.toFixed(6)}, ${v.y.toFixed(6)})  ` +
        `fraction (${f.fx.toFixed(4)}, ${f.fy.toFixed(4)})  ` +
        `image CSS ${b.w.toFixed(2)}x${b.h.toFixed(2)}  PNG ${n.w}x${n.h}`,
    )
  }

  console.log('\n===== CE QUE L’API A ENREGISTRÉ =====')
  console.log('calibrations :', JSON.stringify(corps.calibrations, null, 1).slice(0, 1200))
  for (const m of corps.mesures) {
    console.log(`mesure « ${m.libelle} » : ${m.valeur} ${m.unite} (lisible ${m.valeur_lisible})`)
    console.log('  points enregistrés :', JSON.stringify(m.points))
  }

  console.log('\n===== CE QU’IL AURAIT FALLU =====')
  const [W] = VERITE.page
  for (const coin of [coinA, coinB]) {
    const e = versLEcran(coin)
    console.log(`  coin (${coin[0]}, ${coin[1]}) → écran (${e.x.toFixed(6)}, ${e.y.toFixed(6)})`)
  }
  console.log(`  page : ${VERITE.page[0]} x ${VERITE.page[1]} points`)
  console.log(`  attendu : ${VERITE.piece.largeur_mm} mm, soit ${(coinB[0] - coinA[0])} points`)
  console.log(`  largeur de page W = ${W}`)
})
