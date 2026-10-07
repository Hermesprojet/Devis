import { mkdirSync } from 'node:fs'
import { join } from 'node:path'

import { expect, test, type Page } from '@playwright/test'

import { mesurerLePlanConnu, VERITE, type Jalon } from '../mesure-pdf/plan-connu'

/**
 * Le parcours PDF, capturé image par image, sur une géométrie connue.
 *
 *     CAPTURES_DIR=/tmp/parcours-pdf npx playwright test \
 *       --config=playwright.captures.config.ts captures/parcours-pdf.spec.ts
 *
 * **Ce que ce fichier est, et ce qu'il n'est plus.** Il ne porte plus le
 * scénario : celui-ci vit dans `mesure-pdf/plan-connu.ts` et tombe désormais à
 * **chaque livraison**, dans `e2e-premier-devis/suite-mesure-juste-pdf.spec.ts`.
 * Ce fichier-ci en est la **sortie complémentaire** : les mêmes gestes, les
 * mêmes comparaisons, et une photographie à chaque étape.
 *
 * C'était le défaut à corriger. La seule épreuve qui comparait la mesure
 * affichée à une longueur connue ne vivait que dans ce fichier, qu'aucune des
 * deux configurations de CI ne ramasse : une régression de pointage pouvait
 * passer les douze contrôles verts.
 *
 * **Ce qui reste hors de portée.** Que Metreo mesure juste sur un plan
 * d'exécution RÉEL. Ce qui est démontré ici est que la chaîne complète — écran,
 * repère, calibration, calcul, décision — retombe sur une géométrie connue.
 * L'écart entre les deux est l'erreur de pointage d'un humain sur un vrai
 * dessin, et elle se relève sur de vraies cotes :
 * `docs/VALIDATION_SUR_PLANS_REELS.md`.
 */

const SORTIE = process.env.CAPTURES_DIR ?? '/tmp/parcours-pdf'
mkdirSync(SORTIE, { recursive: true })

let numero = 0

/** Enregistre une capture numérotée, et dit à l'écran ce qu'elle montre. */
function photographe(page: Page): Jalon {
  return async (nom, quoi) => {
    numero += 1
    const fichier = join(SORTIE, `${String(numero).padStart(2, '0')}-${nom}.png`)
    // Remonté en haut AVANT la capture : la colonne du plan est collante, et
    // Chromium photographie un élément collant à sa position STUCK dans une
    // capture pleine page — il laisse alors un grand blanc là où il se trouvait.
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: fichier, fullPage: true })
    console.log(`  capture ${String(numero).padStart(2, '0')} — ${quoi}\n            ${fichier}`)
  }
}

test('le parcours PDF en dix captures, sur une géométrie dont on connaît les dimensions', async ({
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

  const lues = await mesurerLePlanConnu(page, {
    libelle: 'Plan RDC — PDF',
    jalon: photographe(page),
  })

  console.log(
    `\n  ${numero} captures dans ${SORTIE}\n` +
      `  longueur : ${lues.longueurAffichee} pour ${VERITE.piece.largeur_mm} mm attendus ` +
      `(écart ${(((lues.longueurEnMm - VERITE.piece.largeur_mm) / VERITE.piece.largeur_mm) * 100).toFixed(4)} %)\n` +
      `  surface  : ${lues.surfaceAffichee} pour ${VERITE.piece.surface_m2} m² attendus ` +
      `(écart ${(((lues.surfaceEnM2 - VERITE.piece.surface_m2) / VERITE.piece.surface_m2) * 100).toFixed(4)} %)`,
  )
})
