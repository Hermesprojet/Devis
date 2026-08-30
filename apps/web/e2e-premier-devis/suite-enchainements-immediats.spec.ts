import { createHash } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { expect, test, type Page } from '@playwright/test'

import { ADMIN } from './banc'
import { seConnecter } from './parcours'

/**
 * Créer, puis se servir tout de suite de ce qu'on vient de créer.
 *
 * C'est l'enchaînement qui perd la course quand une écriture est validée après
 * la réponse : l'écran reçoit un identifiant, navigue aussitôt, et l'API — qui
 * ouvre une autre session — ne trouve rien. Le défaut ne se voit qu'en charge,
 * et jamais deux fois au même endroit.
 *
 * Ce scénario ne contient AUCUNE attente artificielle. Pas de `waitForTimeout`,
 * pas de reprise : chaque action suit la précédente aussi vite que le
 * navigateur en est capable. Et il recommence dix fois, parce qu'une course ne
 * se laisse pas voir au premier tour.
 *
 * Il vient après `roles.spec.ts` — l'ordre alphabétique des fichiers est celui
 * d'exécution — et travaille dans l'organisation que le parcours principal a
 * configurée.
 */

const TOURS = 10

const PIECE = join(tmpdir(), 'metreo-enchainement.pdf')
writeFileSync(PIECE, '%PDF-1.7\n1 0 obj<<>>endobj\n% piece d enchainement\ntrailer\n%%EOF\n')
const EMPREINTE_PIECE = createHash('sha256').update(readFileSync(PIECE)).digest('hex')

async function creerLeChantier(page: Page, reference: string): Promise<void> {
  await page.goto('/projets')
  await page.getByRole('button', { name: /nouveau projet/i }).first().click()
  await page.getByLabel(/référence/i).fill(reference)
  await page.getByLabel(/^nom/i).first().fill(`Chantier ${reference}`)
  await page.getByRole('button', { name: /^créer$/i }).first().click()
}

test("créer puis se servir aussitôt, dix fois d'affilée", async ({ page }) => {
  test.setTimeout(180_000)
  await seConnecter(page, ADMIN)

  for (let tour = 1; tour <= TOURS; tour += 1) {
    const reference = `ENCH-${String(tour).padStart(3, '0')}`

    // ---- 1. créer un chantier, puis l'OUVRIR immédiatement
    //
    // Le lien n'apparaît que si la liste, rechargée dans la foulée, contient
    // déjà le projet. Un projet validé après la réponse ne serait pas là.
    await creerLeChantier(page, reference)
    await page.getByRole('link', { name: reference }).first().click()
    await page.waitForURL(/\/projets\/[0-9a-f-]{36}$/)
    await expect(page.getByRole('heading', { name: new RegExp(reference) })).toBeVisible()

    // ---- 2. joindre une pièce, puis la TÉLÉCHARGER immédiatement
    const section = page.getByTestId('documents')
    await section.getByLabel('Catégorie').selectOption('CCTP')
    await section.getByLabel(/Libellé/).fill(`Pièce du tour ${tour}`)
    await section.getByLabel('Fichier à joindre').setInputFiles(PIECE)
    await expect(section.getByRole('cell', { name: 'metreo-enchainement.pdf' }).first()).toBeVisible()

    const historique = section.getByRole('button', { name: /^Révisions/ }).first()
    if ((await historique.getAttribute('aria-expanded')) !== 'true') await historique.click()
    const [recu] = await Promise.all([
      page.waitForEvent('download'),
      section
        .locator('tr')
        .filter({ has: page.getByText('Révision 1', { exact: true }) })
        .last()
        .getByRole('button', { name: 'Télécharger' })
        .click(),
    ])
    expect(
      createHash('sha256').update(readFileSync(await recu.path())).digest('hex'),
      `tour ${tour} : les octets rendus ne sont pas ceux déposés`,
    ).toBe(EMPREINTE_PIECE)

    // ---- 3. archiver, puis RELIRE la liste immédiatement
    await section.getByRole('button', { name: 'Archiver' }).click()
    await section.getByRole('button', { name: /Confirmer l'archivage/ }).click()
    await expect(section).toContainText('Aucun document joint à ce projet')
  }
})

test('deux chantiers créés en même temps aboutissent tous les deux', async ({ browser }) => {
  /**
   * Deux navigateurs, deux créations lancées ensemble.
   *
   * Le métier l'autorise — deux personnes créent chacune leur chantier — et
   * c'est le cas où la validation tardive se voit le mieux : la seconde
   * requête d'une session peut arriver pendant que la première de l'autre
   * n'est pas encore validée.
   */
  const contextes = await Promise.all([browser.newContext(), browser.newContext()])
  const pages = await Promise.all(contextes.map((contexte) => contexte.newPage()))
  try {
    await Promise.all(pages.map((page) => seConnecter(page, ADMIN)))
    await Promise.all(
      pages.map((page, indice) => creerLeChantier(page, `ENCH-PAR-${indice + 1}`)),
    )
    for (const [indice, page] of pages.entries()) {
      await page.goto('/projets')
      await expect(page.getByRole('link', { name: `ENCH-PAR-${indice + 1}` })).toBeVisible()
    }
    // Et le premier voit le chantier de l'autre : ils appartiennent à la même
    // organisation, et les deux écritures ont bien été validées.
    const premier = pages[0]
    if (premier === undefined) throw new Error('le premier navigateur a disparu')
    await premier.goto('/projets')
    await expect(premier.getByRole('link', { name: 'ENCH-PAR-2' })).toBeVisible()
  } finally {
    await Promise.all(contextes.map((contexte) => contexte.close()))
  }
})
