import { createHash } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { expect, test, type Page } from '@playwright/test'

import { ADMIN, CONSTAT_DOCUMENTS } from './banc'

/**
 * Joindre une pièce au chantier, la réviser, la reprendre et la ranger.
 *
 * Le scénario suit `premier-devis.spec.ts` — le nom du fichier le place après
 * lui, et il compte sur le projet que celui-ci a créé. Il ne crée donc AUCUN
 * projet : en créer un ferait échouer le parcours du devis, qui vérifie qu'une
 * organisation neuve n'en a aucun.
 *
 * Tout passe par l'écran : aucun appel direct à l'API, aucune écriture dans le
 * volume. Ce que le test compare, ce sont les octets qui reviennent du
 * navigateur, contre ceux qu'il a lui-même produits.
 */

const CCTP_1 = join(tmpdir(), 'metreo-cctp-r1.pdf')
const CCTP_2 = join(tmpdir(), 'metreo-cctp-r2.pdf')

// Des PDF minimaux mais réels : la signature `%PDF-` est ce que le serveur
// regarde, et un fichier de fantaisie serait refusé — à raison.
writeFileSync(CCTP_1, '%PDF-1.7\n1 0 obj<<>>endobj\n% CCTP lot 2 - revision 1\ntrailer\n%%EOF\n')
writeFileSync(CCTP_2, '%PDF-1.7\n1 0 obj<<>>endobj\n% CCTP lot 2 - revision 2 corrigee\ntrailer\n%%EOF\n')

const empreinte = (chemin: string): string =>
  createHash('sha256').update(readFileSync(chemin)).digest('hex')

async function seConnecter(page: Page, adresse: string): Promise<void> {
  await page.goto('/')
  await page.getByRole('button', { name: /compte de l'entreprise/ }).click()
  await page.locator('#email').fill(adresse)
  await page.getByRole('button', { name: 'Se connecter' }).click()
  await page.waitForURL(/\/projets$/)
}

async function ouvrirLeProjet(page: Page): Promise<void> {
  await page.getByRole('link', { name: 'PREM-001' }).click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}$/)
}

/** Déplie l'historique du premier document, s'il ne l'est pas déjà. */
async function ouvrirLHistorique(page: Page): Promise<void> {
  const bouton = page.getByTestId('documents').getByRole('button', { name: /^Révisions/ }).first()
  if ((await bouton.getAttribute('aria-expanded')) !== 'true') await bouton.click()
  await expect(page.getByText('Révision 1', { exact: true })).toBeVisible()
}

/** La ligne d'une révision dans le dépliant d'historique. */
function ligneRevision(page: Page, numero: number) {
  return page
    .getByTestId('documents')
    .locator('tr')
    .filter({ has: page.getByText(`Révision ${numero}`, { exact: true }) })
    .last()
}

test('un CCTP se joint au chantier, se révise et se retrouve à l’identique', async ({ page }) => {
  await seConnecter(page, ADMIN)
  await ouvrirLeProjet(page)

  const section = page.getByTestId('documents')
  await expect(section).toBeVisible()
  // L'état vide explique, il ne se contente pas d'être vide.
  await expect(section).toContainText('Aucun document joint à ce projet')

  // ---- 1. le dépôt, au sélecteur de fichier — accessible au clavier
  await section.getByLabel('Catégorie').selectOption('CCTP')
  await section.getByLabel(/Libellé/).fill('Lot 2 — terrassements')
  await section.getByLabel('Fichier à joindre').setInputFiles(CCTP_1)
  await expect(section.getByRole('cell', { name: 'metreo-cctp-r1.pdf' }).first()).toBeVisible()

  // ---- 2. les métadonnées que l'écran doit montrer
  await expect(section).toContainText('CCTP — Lot 2 — terrassements')
  await expect(section).toContainText('PDF')
  await expect(section).toContainText(ADMIN)
  await expect(section.getByText(empreinte(CCTP_1).slice(0, 16), { exact: false })).toBeVisible()

  // ---- 3. le téléchargement rend EXACTEMENT ce qui a été déposé
  await ouvrirLHistorique(page)
  const [recu] = await Promise.all([
    page.waitForEvent('download'),
    ligneRevision(page, 1).getByRole('button', { name: 'Télécharger' }).click(),
  ])
  expect(recu.suggestedFilename()).toBe('metreo-cctp-r1.pdf')
  expect(empreinte(await recu.path())).toBe(empreinte(CCTP_1))

  // ---- 4. une deuxième révision, sans rien écraser
  await section.getByLabel('Joindre une nouvelle révision').setInputFiles(CCTP_2)
  await expect(section.getByText('Révision 2', { exact: true })).toBeVisible()
  await expect(section.getByRole('cell', { name: 'metreo-cctp-r2.pdf' }).first()).toBeVisible()

  // ---- 5. les DEUX révisions restent téléchargeables, chacune à son empreinte
  for (const [numero, source] of [
    [1, CCTP_1],
    [2, CCTP_2],
  ] as const) {
    const [fichier] = await Promise.all([
      page.waitForEvent('download'),
      ligneRevision(page, numero).getByRole('button', { name: 'Télécharger' }).click(),
    ])
    expect(empreinte(await fichier.path()), `révision ${numero}`).toBe(empreinte(source))
  }

  // ---- 6. le doublon exact est refusé, et le dit
  await section.getByLabel('Joindre une nouvelle révision').setInputFiles(CCTP_2)
  await expect(section.getByText(/déjà la révision 2/)).toBeVisible()
  await expect(section.getByText('Révision 3', { exact: true })).toHaveCount(0)

  // ---- 7. l'archivage : hors des listes courantes, jamais détruit
  await section.getByRole('button', { name: 'Archiver' }).click()
  await section.getByRole('button', { name: /Confirmer l'archivage/ }).click()
  await expect(section).toContainText('Aucun document joint à ce projet')

  await section.getByRole('button', { name: 'Voir les documents archivés' }).click()
  await expect(section.getByText('archivé').first()).toBeVisible()
  await expect(section.getByRole('cell', { name: 'metreo-cctp-r2.pdf' }).first()).toBeVisible()

  // ---- 8. et l'original archivé se télécharge toujours
  //
  // On CONSTATE l'état du dépliant au lieu de le supposer : cliquer sans
  // regarder le refermait quand il était déjà ouvert, et le téléchargement
  // suivant attendait un bouton qui venait de disparaître.
  await ouvrirLHistorique(page)
  const [apresArchivage] = await Promise.all([
    page.waitForEvent('download'),
    ligneRevision(page, 1).getByRole('button', { name: 'Télécharger' }).click(),
  ])
  expect(empreinte(await apresArchivage.path())).toBe(empreinte(CCTP_1))

  // ---- 9. remis en service pour la suite du parcours
  await section.getByRole('button', { name: 'Réactiver' }).click()
  await expect(section.getByText('archivé')).toHaveCount(0)

  // ---- 10. de quoi retrouver cette pièce après une sauvegarde et une
  //          restauration. La répétition de préproduction relit CE document —
  //          celui qu'une personne a déposé — et non un fichier qu'un script
  //          aurait posé dans le volume.
  if (CONSTAT_DOCUMENTS) {
    const [, projet] = /\/projets\/([0-9a-f-]{36})/.exec(page.url()) ?? []
    writeFileSync(
      CONSTAT_DOCUMENTS,
      JSON.stringify({
        project_id: projet,
        filenames: ['metreo-cctp-r1.pdf', 'metreo-cctp-r2.pdf'],
        sha256: [empreinte(CCTP_1), empreinte(CCTP_2)],
        byte_sizes: [readFileSync(CCTP_1).length, readFileSync(CCTP_2).length],
      }),
    )
  }
})

test('un fichier refusé le dit en clair et ne laisse rien derrière', async ({ page }) => {
  const executable = join(tmpdir(), 'metreo-faux.pdf')
  // Un exécutable Windows, renommé en `.pdf`. L'extension ment ; la signature
  // non, et c'est elle que le serveur lit.
  writeFileSync(executable, Buffer.concat([Buffer.from('MZ\x90\x00'), Buffer.alloc(64, 0x41)]))

  await seConnecter(page, ADMIN)
  await ouvrirLeProjet(page)
  const section = page.getByTestId('documents')

  await section.getByLabel('Fichier à joindre').setInputFiles(executable)
  await expect(section.getByText(/exécutable Windows/)).toBeVisible()
  await expect(section.getByRole('cell', { name: 'metreo-faux.pdf' })).toHaveCount(0)
})
