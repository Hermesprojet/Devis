import { join } from 'node:path'

import { expect, test, type Page } from '@playwright/test'

import { ADMIN } from './banc'
import { enBelge, seConnecter } from './parcours'

/**
 * Déposer un plan DXF, le voir, et trancher sur une mesure proposée.
 *
 * **Le nom du fichier le place après `premier-devis.spec.ts`.** Les scénarios
 * de ce dossier se suivent dans l'ORDRE ALPHABÉTIQUE et partagent une seule
 * organisation ; `premier-devis.spec.ts` vérifie qu'une organisation neuve n'a
 * AUCUN projet. Un fichier qui trierait avant lui — `plan-…` — ferait échouer
 * ce contrôle pour une raison qui n'a rien à voir avec le plan. Le préfixe
 * `suite-` est la convention du dossier pour « ce qui vient après », et ce
 * scénario ne crée donc aucun projet : il reprend PREM-001.
 *
 * **La fixture est fabriquée par le BANC**, pas par ce scénario :
 * `fixtures/plans/mur_cote.dxf` — un mur de 5 m, coté, `$INSUNITS` = 4, donc
 * des millimètres. Elle n'est pas commitée parce qu'une cotation a besoin de
 * son bloc géométrique, et que ce bloc fait trois mille lignes qu'aucun
 * relecteur ne lira ; `scripts/fabriquer_plans_de_test.py` les écrit en dix
 * lignes lisibles.
 *
 * Et c'est le BANC qui l'appelle, pas ce fichier : une fixture absente doit
 * faire échouer la préparation, jamais rendre un parcours silencieux. Le
 * dépôt a déjà payé cette leçon — deux tests prouvant le refus du DWG étaient
 * SAUTÉS en intégration continue, faute de quoi que ce soit qui fabrique
 * leurs fichiers.
 *
 * **Pourquoi pas `mur_simple.dxf`, qui est commitée** : elle porte deux
 * LIGNES et aucune cotation. Le plan s'y lit — unité, calques, entités — mais
 * il ne propose RIEN à mesurer, et un parcours qui cherche une valeur à
 * corriger n'y trouve rien. C'est ce qui a fait échouer le premier essai dans
 * un navigateur.
 *
 * **Tout passe par l'écran** : aucun appel direct à l'API, aucune écriture
 * dans le volume. Ce qui est vérifié, c'est ce qu'une personne voit.
 *
 * Deux pièges mesurés, et évités ici :
 *
 *  - Next maintient en permanence un `role="alert"` VIDE — l'annonceur de
 *    route, destiné aux lecteurs d'écran. On ne vise jamais `getByRole
 *    ('alert')` seul : on vise `.notice.warning`, `.notice.error`, ou un
 *    `data-testid`.
 *  - l'analyse est SYNCHRONE et dure une dizaine de secondes. Les attentes
 *    qui la suivent portent leur propre délai, plus long que celui du banc.
 */

const PLAN = join(__dirname, '..', '..', '..', 'fixtures', 'plans', 'mur_cote.dxf')

/** Au-delà du délai d'attente ordinaire : l'analyse tient le fil 7 à 9 s. */
const DELAI_ANALYSE = 60_000

/** Une valeur saisie À LA VIRGULE : c'est le clavier belge, et il doit passer. */
const VALEUR_CORRIGEE = '5250,5'
const MOTIF = 'Relevé sur place : la cote du plan ne tient pas compte de l’enduit.'

async function ouvrirLeProjet(page: Page): Promise<void> {
  await page.getByRole('link', { name: 'PREM-001' }).click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}$/)
}

test('un plan DXF déposé s’affiche, propose ses mesures, et une valeur corrigée s’affiche à côté de la valeur proposée', async ({
  page,
}) => {
  await seConnecter(page, ADMIN)
  await ouvrirLeProjet(page)

  const documents = page.getByTestId('documents')
  await expect(documents).toBeVisible()

  // ---- 1. la phrase d'aide dit la vérité sur ce que le serveur accepte
  //
  // Elle énumérait « PDF, PNG, JPEG, CSV, XLSX ou DOCX » et taisait le DXF —
  // le seul format que Metreo sache LIRE. Elle taisait aussi le refus du DWG,
  // qui est la première question d'un utilisateur de plans.
  await expect(documents).toContainText('DXF')
  await expect(documents).toContainText('.dwg')

  // ---- 2. le dépôt du plan, par le sélecteur de fichier
  await documents.getByLabel('Catégorie').selectOption('Plan')
  await documents.getByLabel(/Libellé/).fill('Mur de façade')
  await documents.getByLabel('Fichier à joindre').setInputFiles(PLAN)

  const ligne = documents
    .locator('tr')
    .filter({ has: page.getByRole('cell', { name: 'mur_cote.dxf' }) })
    .first()
  await expect(ligne).toBeVisible()
  // La colonne « Type » nomme le plan au lieu d'afficher « — ».
  await expect(ligne).toContainText('DXF')

  // ---- 3. l'écran de lecture s'atteint depuis la ligne de révision, et
  //         reste sous /projets pour que « Projets » garde le menu surligné
  await ligne.getByTestId('documents-lire-plan').click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}\/plans\/[0-9a-f-]{36}\/[0-9a-f-]{36}/)
  await expect(page.getByRole('link', { name: 'Projets' })).toHaveAttribute(
    'aria-current',
    'page',
  )

  const ecran = page.getByTestId('plan-lecture')
  await expect(ecran).toBeVisible()

  // ---- 4. l'état vide EXPLIQUE, et la durée est annoncée AVANT le clic
  const avant = page.getByTestId('plan-non-analyse')
  await expect(avant).toBeVisible()
  await expect(avant).toContainText('n’a pas encore été lu')
  await expect(avant.locator('.notice.warning')).toContainText('dizaine de secondes')

  // ---- 5. l'analyse : synchrone, et l'attente est montrée pendant
  await page.getByTestId('plan-analyser').click()
  await expect(page.getByTestId('plan-analyse-en-cours')).toBeVisible()

  // ---- 6. le rendu apparaît, chargé comme une IMAGE
  await expect(page.getByTestId('plan-image')).toBeVisible({ timeout: DELAI_ANALYSE })

  // ---- 7. le constat de lecture : l'unité du document, lue dans $INSUNITS
  await expect(page.getByTestId('plan-constat')).toBeVisible()
  await expect(page.getByTestId('plan-unite-source')).toContainText('mm')

  // ---- 8. au moins une mesure proposée
  const mesures = ecran.getByTestId('plan-mesure')
  await expect(mesures.first()).toBeVisible({ timeout: DELAI_ANALYSE })
  expect(await mesures.count()).toBeGreaterThan(0)

  const premiere = mesures.first()
  const proposee = (await premiere.getByTestId('plan-valeur-proposee').innerText()).trim()
  expect(proposee, 'la mesure proposée doit porter une valeur lisible').not.toBe('')

  // ---- 9. la correction. Le motif d'abord : le serveur l'exige non vide.
  await premiere.getByTestId('plan-corriger').click()
  const formulaire = premiere.getByTestId('plan-formulaire-decision')
  await expect(formulaire).toBeVisible()
  await formulaire.getByTestId('plan-motif').fill(MOTIF)

  // La validation ne porte que sur la FORME du nombre : du texte est refusé,
  // en toutes lettres, et rien n'est envoyé.
  await formulaire.getByTestId('plan-valeur-corrigee').fill('cinq mètres')
  await formulaire.getByTestId('plan-enregistrer-decision').click()
  await expect(formulaire.locator('.notice.error')).toContainText('pas un nombre')

  // La virgule du clavier belge passe.
  await formulaire.getByTestId('plan-valeur-corrigee').fill(VALEUR_CORRIGEE)
  await formulaire.getByTestId('plan-enregistrer-decision').click()

  // ---- 10. et c'est le cœur du parcours : l'écran montre LES DEUX.
  //
  // La proposition du programme n'est jamais réécrite. Après la décision, la
  // ligne porte encore la valeur proposée, ET la valeur retenue par la
  // personne, côte à côte. N'en montrer qu'une rendrait le dossier
  // inauditable : on ne saurait plus ce que la machine avait dit.
  const decidee = ecran
    .getByTestId('plan-mesure')
    .filter({ has: page.getByTestId('plan-valeur-retenue') })
    .first()
  await expect(decidee).toBeVisible({ timeout: DELAI_ANALYSE })
  await expect(decidee.getByTestId('plan-valeur-proposee')).toHaveText(proposee)
  // Écrite à la belge, comme la valeur proposée : « 5 250,5 », jamais « 5250.5 ».
  await expect(decidee.getByTestId('plan-valeur-retenue')).toContainText(enBelge('5250.5'))
  await expect(decidee.getByTestId('plan-badge-decision')).toHaveText('corrigée')

  // ---- 11. la mesure décidée ne propose plus ses commandes
  await expect(decidee.getByTestId('plan-corriger')).toHaveCount(0)
  await expect(decidee.getByTestId('plan-confirmer')).toHaveCount(0)
  await expect(decidee.getByTestId('plan-refuser')).toHaveCount(0)
})
