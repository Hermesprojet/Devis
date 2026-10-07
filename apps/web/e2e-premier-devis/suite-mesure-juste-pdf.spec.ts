import { expect, test, type Page } from '@playwright/test'

import { ADMIN } from './banc'
import { seConnecter } from './parcours'
import {
  mesurerLePlanConnu,
  TOLERANCE_DE_LONGUEUR,
  TOLERANCE_DE_SURFACE,
  VERITE,
} from '../mesure-pdf/plan-connu'

/**
 * La vérification CHIFFRÉE du parcours PDF, jouée à chaque livraison.
 *
 * **Pourquoi ce fichier existe.** `suite-plan-pdf-mesures.spec.ts`, qui tombe
 * déjà en CI, vérifie que la mesure affichée porte un nombre, une unité, une
 * incertitude et une fiabilité — pas que le nombre soit JUSTE. Il travaille sur
 * `plan_cote.pdf`, qui ne contient que des textes isolés : rien, sur ce plan,
 * ne permet de savoir ce qu'une mesure devrait valoir.
 *
 * La seule épreuve qui comparait à une longueur connue vivait dans
 * `captures/parcours-pdf.spec.ts`, qu'il faut demander par `--config` et
 * qu'aucune des deux configurations de CI ne ramasse. Une régression de
 * pointage — un repère inversé, une zone désynchronisée, un facteur appliqué
 * deux fois — pouvait donc passer les douze contrôles verts.
 *
 * Ce scénario-ci ferme ce trou. Il travaille sur `plan_batiment.pdf`, dont la
 * géométrie — 5 000 mm de cote, 6 000 × 4 000 mm de pièce, 24,00 m² — est
 * écrite par la fabrique dans `plan_batiment.json`, **sans passer par le
 * lecteur**, et il échoue si l'écran s'en écarte.
 *
 * **Ce qu'il vérifie, et que rien d'autre ne vérifie.**
 *
 * 1. la **longueur** affichée vaut 6 000 mm à 1 % près ;
 * 2. la **surface** affichée vaut 24,00 m² à 2 % près ;
 * 3. les **unités** sont celles des grandeurs — `mm` pour une longueur, `m²`
 *    pour une aire — et non celles du fichier, qui n'en porte aucune ;
 * 4. la **correspondance image–tracé** : la mesure se redessine sur l'aperçu
 *    ET dans la loupe, sur une image déclarée prête ;
 * 5. l'**exclusion des mesures rejetées** : la surface rejetée reste lisible
 *    et se déclare non reprenable, la longueur corrigée se déclare reprenable.
 *
 * Les captures restent produites à la demande par `captures/parcours-pdf.spec.ts`,
 * qui appelle le même scénario depuis `mesure-pdf/plan-connu.ts`.
 */

async function ouvrirLeProjet(page: Page): Promise<void> {
  await page.getByRole('link', { name: 'PREM-001' }).click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}$/)
}

test('une longueur et une surface mesurées sur un plan de dimensions connues retombent sur ses cotes', async ({
  page,
}) => {
  // Le scénario enchaîne dépôt, analyse, calibration, deux mesures et deux
  // décisions, chacune derrière un rendu de tuile qui coûte des secondes.
  test.setTimeout(300_000)

  await seConnecter(page, ADMIN)
  await ouvrirLeProjet(page)

  const lues = await mesurerLePlanConnu(page, { libelle: 'Plan RDC coté — PDF' })

  // Les comparaisons sont déjà faites dans le scénario, qui échoue sur place
  // avec le nombre lu. Elles sont REFAITES ici, sur ce que la fonction rend,
  // pour que ce fichier dise lui-même ce qu'il garantit : un lecteur qui
  // n'ouvre que ce spec doit pouvoir lire la propriété, pas la deviner.
  const ecartDeLongueur =
    Math.abs(lues.longueurEnMm - VERITE.piece.largeur_mm) / VERITE.piece.largeur_mm
  expect(
    ecartDeLongueur,
    `${lues.longueurAffichee} pour ${VERITE.piece.largeur_mm} mm attendus`,
  ).toBeLessThan(TOLERANCE_DE_LONGUEUR)

  const ecartDeSurface =
    Math.abs(lues.surfaceEnM2 - VERITE.piece.surface_m2) / VERITE.piece.surface_m2
  expect(
    ecartDeSurface,
    `${lues.surfaceAffichee} pour ${VERITE.piece.surface_m2} m² attendus`,
  ).toBeLessThan(TOLERANCE_DE_SURFACE)

  // Écrit dans le journal du banc : c'est ce chiffre que le document de
  // précision recopie, et le relever à la main d'une capture serait une
  // seconde source.
  console.log(
    `  longueur ${lues.longueurAffichee} → écart ${(ecartDeLongueur * 100).toFixed(4)} %\n` +
      `  surface  ${lues.surfaceAffichee} → écart ${(ecartDeSurface * 100).toFixed(4)} %`,
  )
})
