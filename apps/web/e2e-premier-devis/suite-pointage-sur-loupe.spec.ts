import { expect, test, type Page } from '@playwright/test'

import { ADMIN } from './banc'
import { seConnecter } from './parcours'
import {
  cliquerA,
  deposerEtLire,
  DELAI_ANALYSE,
  SANS_JALON,
  versLEcran,
  VERITE,
  zoneRendue,
} from '../mesure-pdf/plan-connu'

/**
 * Le pointage pendant que l'agrandissement charge, échoue, ou arrive en retard.
 *
 * **Le défaut corrigé, tel qu'il a été observé.** Une capture du parcours
 * montrait « Agrandissement en cours » AVEC une image encore affichée. Dans
 * `LecturePdf.tsx`, la zone changeait immédiatement, l'ancienne image restait
 * visible et le clic restait autorisé : un point posé à cet instant était
 * calculé sur la zone COURANTE alors que la personne visait ce que montrait
 * l'image PRÉCÉDENTE. Un point enregistré ailleurs que là où il a été vu, sans
 * un mot — et une calibration fausse en découle, qui multiplie ensuite toutes
 * les mesures de la page.
 *
 * **Pourquoi ces scénarios et pas d'autres.** Les trois cas qui produisent un
 * écart entre ce qui est vu et ce qui est désigné sont exactement :
 *
 * 1. le rendu est **en cours** — il n'y a rien à montrer, il ne doit y avoir
 *    rien à cliquer ;
 * 2. deux déplacements rapides lancent deux requêtes et la **première arrive
 *    après la seconde** — l'écran afficherait la mauvaise zone en se croyant à
 *    jour ;
 * 3. le rendu **échoue** — garder l'image précédente laisserait pointer sur une
 *    zone qu'on ne sert plus.
 *
 * Chacun est provoqué ici en interceptant la requête de tuile, et non en
 * espérant tomber sur le bon instant : un test de concurrence qui compte sur
 * la chance ne tombe que sur les machines lentes, et jamais sur celle qui
 * livre.
 */

/** La route que l'écran appelle pour un agrandissement. */
const TUILE = '**/plan/tuile?**'

async function ouvrirLeProjet(page: Page): Promise<void> {
  await page.getByRole('link', { name: 'PREM-001' }).click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}$/)
}

/**
 * Un plan analysé, prêt à être pointé, et l'outil de mesure choisi.
 *
 * L'outil est choisi parce que c'est lui qui ouvre le pointage : en mode
 * « naviguer », aucun clic ne pose de point, et le test passerait sans rien
 * éprouver.
 */
async function planPret(page: Page, libelle: string): Promise<void> {
  await seConnecter(page, ADMIN)
  await ouvrirLeProjet(page)
  await deposerEtLire(page, libelle, SANS_JALON)
  await page.getByTestId('pdf-outil-segment').click()
}

/** Combien de points l'écran dit avoir enregistrés. */
async function pointsPoses(page: Page): Promise<number> {
  const texte = await page.getByTestId('pdf-loupe-panneau').getByText(/point\(s\) posé/).innerText()
  const trouve = /(\d+)/.exec(texte)
  return Number(trouve?.[1] ?? -1)
}

/** Le centre de la zone déclarée par l'image affichée. */
async function centreDeLaZone(page: Page): Promise<{ x: number; y: number }> {
  const [x0, y0, x1, y1] = await zoneRendue(page)
  return { x: (x0 + x1) / 2, y: (y0 + y1) / 2 }
}

test('pendant le chargement, il n’y a aucune image à cliquer et l’attente est annoncée', async ({
  page,
}) => {
  test.setTimeout(180_000)

  // Le retard est IMPOSÉ, pas espéré. Cinq secondes : assez pour que les
  // assertions passent toutes pendant l'attente, et sous le délai du test.
  let retarder = false
  await page.route(TUILE, async (route) => {
    if (retarder) await new Promise((suite) => setTimeout(suite, 5_000))
    await route.continue()
  })

  await planPret(page, 'Plan — chargement lent')

  retarder = true
  const cible = versLEcran(VERITE.cote.premier)
  await cliquerA(page.getByTestId('pdf-apercu'), cible.x, cible.y)

  const panneau = page.getByTestId('pdf-loupe-panneau')
  await expect(panneau).toBeVisible()

  // L'attente est DITE, et elle dit sa cause : charger une page de PDF coûte
  // jusqu'à 5,3 secondes la première fois.
  await expect(panneau.getByText(/Agrandissement en cours/)).toBeVisible()

  // Et il n'y a rien à cliquer. C'est la correction : l'ancienne image partait
  // avant la nouvelle, au lieu de rester visible et pointable.
  await expect(page.getByTestId('pdf-loupe-image')).toHaveCount(0)
  expect(await pointsPoses(page)).toBe(0)

  // Puis l'image arrive, déclarée prête, avec sa zone.
  const image = page.getByTestId('pdf-loupe-image')
  await expect(image).toHaveAttribute('data-prete', 'oui', { timeout: DELAI_ANALYSE })
  await expect(image).toHaveAttribute('data-zone-declaree', 'oui')
})

test('deux déplacements rapides affichent la DERNIÈRE zone, même si la première réponse arrive après', async ({
  page,
}) => {
  test.setTimeout(180_000)

  // La PREMIÈRE tuile de la rafale est retardée, les suivantes non : c'est
  // exactement l'inversion qui faisait afficher la mauvaise zone.
  let rafale = 0
  await page.route(TUILE, async (route) => {
    rafale += 1
    if (rafale === 1) await new Promise((suite) => setTimeout(suite, 4_000))
    await route.continue()
  })

  await planPret(page, 'Plan — déplacements rapides')
  // L'aperçu pleine page passe par `renduDeLaPage`, un autre point d'entrée :
  // cette route ne voit donc QUE les agrandissements. Le compteur est remis à
  // zéro par prudence, pour que le scénario ne dépende pas de cette séparation.
  rafale = 0

  const premier = versLEcran(VERITE.cote.premier)
  const second = versLEcran(VERITE.cote.second)
  await cliquerA(page.getByTestId('pdf-apercu'), premier.x, premier.y)
  // Sans attendre : c'est tout l'objet du scénario.
  await cliquerA(page.getByTestId('pdf-apercu'), second.x, second.y)

  const image = page.getByTestId('pdf-loupe-image')
  await expect(image).toHaveAttribute('data-prete', 'oui', { timeout: DELAI_ANALYSE })

  // La zone affichée est celle du SECOND clic. La tolérance d'un demi-pourcent
  // couvre la troncature du rendu en pixels entiers, et rien de plus : les deux
  // extrémités de la cote sont séparées de plus de la moitié de la page.
  const centre = await centreDeLaZone(page)
  expect(
    Math.abs(centre.x - second.x),
    `zone centrée en ${centre.x}, attendue autour de ${second.x} (premier clic : ${premier.x})`,
  ).toBeLessThan(0.005)
  expect(Math.abs(centre.x - premier.x)).toBeGreaterThan(0.1)

  // Et la réponse en retard, quand elle arrive, ne remplace pas celle-ci.
  await page.waitForTimeout(5_000)
  const apres = await centreDeLaZone(page)
  expect(Math.abs(apres.x - second.x)).toBeLessThan(0.005)
})

test('un agrandissement qui échoue n’affiche rien, le dit, et refuse le pointage', async ({
  page,
}) => {
  test.setTimeout(180_000)

  let echouer = false
  await page.route(TUILE, async (route) => {
    if (echouer) {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ detail: { code: 'rendu_indisponible', message: 'Rendu refusé.' } }),
      })
      return
    }
    await route.continue()
  })

  await planPret(page, 'Plan — rendu en échec')

  echouer = true
  const cible = versLEcran(VERITE.cote.premier)
  await cliquerA(page.getByTestId('pdf-apercu'), cible.x, cible.y)

  const panneau = page.getByTestId('pdf-loupe-panneau')
  await expect(panneau).toBeVisible()

  // Le refus est MONTRÉ. `notice-erreur` et non `getByRole('alert')` : Next.js
  // maintient en permanence un `role="alert"` vide, l'annonceur de route.
  await expect(panneau.getByTestId('notice-erreur')).toBeVisible({ timeout: DELAI_ANALYSE })

  // Rien à cliquer, et aucun point enregistré : garder l'image précédente
  // après un échec laisserait pointer sur une zone qu'on ne sert plus.
  await expect(page.getByTestId('pdf-loupe-image')).toHaveCount(0)
  expect(await pointsPoses(page)).toBe(0)

  // Et le rendu revient dès que le serveur répond de nouveau : l'échec n'a
  // laissé aucun état bloquant derrière lui.
  echouer = false
  await page.getByTestId('pdf-loupe-fermer').click()
  await cliquerA(page.getByTestId('pdf-apercu'), cible.x, cible.y)
  await expect(page.getByTestId('pdf-loupe-image')).toHaveAttribute('data-prete', 'oui', {
    timeout: DELAI_ANALYSE,
  })
})
