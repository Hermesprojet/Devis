import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'

import { expect, test, type Page } from '@playwright/test'

import { ADMIN } from './banc'
import { classeurFictif } from './classeur-fictif'
import { seConnecter, texteDuPdf } from './parcours'

/**
 * D'un classeur de fournisseur au PDF remis au client, DANS LE NAVIGATEUR.
 *
 * Ce que ce scénario établit, et que l'API seule n'établit pas : qu'une
 * personne peut déposer le `.xlsx` qu'elle a reçu de son fournisseur, voir ce
 * qui sera écrit avant que rien ne le soit, choisir la feuille qui porte le
 * barème, confirmer, puis chiffrer un poste avec un prix VENU DE CE CLASSEUR
 * et le retrouver imprimé sur le devis.
 *
 * Le classeur est fabriqué par le script du dépôt, avec la bibliothèque que le
 * serveur lit : une seconde implémentation en TypeScript divergerait, et le
 * fichier éprouvé ici ne serait plus celui qu'un utilisateur produit.
 *
 * Ce fichier vient après les autres dans l'ordre alphabétique de la suite, et
 * s'appuie sur ce que le parcours principal a laissé : une bibliothèque de
 * prix, un taux de taxe en vigueur, et un profil d'entreprise complet.
 */

const BAREME = {
  code: 'XLS-ENR-001',
  libelle: 'Enrobé bitumineux BB-A 0/10, fourniture et pose',
  unite: 't',
  prix: 148.6,
}

const CHANTIER = { reference: 'XLSX-001', nom: 'Réfection de la rue des Tilleuls' }
const POSTE = { position: '02.10', designation: 'Couche de roulement', quantite: '40' }
//: 40 × 148,60 = 5 944,00 — calculé ici pour que l'attendu soit LISIBLE, et
//: comparé à ce que le serveur imprime réellement sur le devis.
const TOTAL_ATTENDU = '5944.00'

function ligneDuPoste(page: Page) {
  return page.getByRole('row', { name: new RegExp(POSTE.position) }).first()
}

test('un classeur de fournisseur devient un prix, puis une ligne de devis imprimée', async ({
  page,
}) => {
  test.setTimeout(180_000)
  await seConnecter(page, ADMIN)

  // ---- 1. le classeur, tel qu'un fournisseur l'envoie : plusieurs feuilles
  //
  // Un barème réel arrive rarement seul sur sa feuille : il y a une garde, le
  // tableau, et des notes. C'est ce cas qu'il faut éprouver, pas le classeur
  // d'école à une seule feuille.
  const chemin = classeurFictif(
    [[BAREME.code, BAREME.libelle, BAREME.unite, BAREME.prix]],
    { feuille: 'Barème 2026', autresFeuilles: ['Notes'] },
  )

  await page.goto('/bibliotheque')
  await expect(page.getByText('Import de prix')).toBeVisible()

  // Le modèle vide se télécharge VRAIMENT. C'est le geste proposé à qui n'a
  // pas encore de barème au bon format ; un bouton qui échouerait en silence
  // laisserait l'utilisateur devant un fichier qu'il ne sait pas fabriquer.
  const [modele] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: 'Modèle Excel' }).click(),
  ])
  expect(modele.suggestedFilename()).toBe('modele_import_prix.xlsx')

  await page.locator('#file').setInputFiles(chemin)

  // ---- 2. la prévisualisation : rien n'est écrit, et elle le dit
  // Le compte tel que l'ÉCRAN l'écrit — « 1 lignes valides ». Inventer la
  // tournure attendue ferait échouer le parcours sur une différence de
  // libellé plutôt que sur un défaut d'import.
  // `exact` vise le BADGE : le même compte figure aussi sur le bouton de
  // confirmation, et sans cela le sélecteur en trouve deux.
  await expect(page.getByText('1 lignes valides', { exact: true })).toBeVisible({
    timeout: 20_000,
  })
  await expect(page.getByRole('cell', { name: BAREME.code })).toBeVisible()
  // Le prix lu dans le classeur, tel que la prévisualisation le montre.
  await expect(page.getByRole('cell', { name: '148.6', exact: true })).toBeVisible()

  // Le classeur porte deux feuilles : l'écran propose donc de choisir, plutôt
  // que de laisser croire que la première était la bonne.
  const choix = page.getByTestId('choix-de-feuille')
  await expect(choix).toBeVisible()
  await expect(choix.locator('option')).toHaveCount(2)

  // ---- 3. confirmer écrit le prix, et lui seul
  await page.locator('#strategy').selectOption('create')
  await page.getByRole('button', { name: /Confirmer l.import/ }).click()
  await expect(page.getByText(/1 créé/)).toBeVisible({ timeout: 20_000 })

  // Le prix EXISTE en bibliothèque, avec la valeur du classeur — pas celle
  // d'un arrondi de lecture.
  await page.getByPlaceholder('Rechercher').fill(BAREME.code)
  await expect(page.getByRole('cell', { name: BAREME.code })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('cell', { name: '148.60', exact: true })).toBeVisible()

  // ---- 4. un chantier, et un poste chiffré PAR ce prix importé
  await page.goto('/projets')
  await page.getByRole('button', { name: /nouveau projet/i }).first().click()
  await page.getByLabel(/référence/i).fill(CHANTIER.reference)
  await page.getByLabel(/^nom/i).first().fill(CHANTIER.nom)
  await page.getByRole('button', { name: /^créer$/i }).first().click()
  await page.getByRole('link', { name: CHANTIER.reference }).first().click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}$/)

  // Le chantier reprend le client que la suite a déjà créé : en créer un
  // second n'éprouverait rien de plus et brouillerait le répertoire.
  await page.getByTestId('selecteur-client').selectOption({ index: 1 })
  await page.getByRole('button', { name: 'Rattacher' }).click()

  await page.getByRole('button', { name: /^créer$/i }).first().click()
  await page.getByLabel('Poste').fill(POSTE.position)
  await page.getByLabel('Désignation').fill(POSTE.designation)
  await page.getByLabel('Unité').fill(BAREME.unite)
  await page.getByLabel('Quantité').fill(POSTE.quantite)
  await page.locator('#source-nouveau').selectOption('price_item')
  // L'option porte le code ET le libellé : on la retrouve par son texte plutôt
  // que par un index, qui dépendrait de l'ordre des prix déjà en bibliothèque.
  const option = page.locator('#prix-nouveau option', { hasText: BAREME.code }).first()
  await page.locator('#prix-nouveau').selectOption(await option.getAttribute('value'))
  await page.getByRole('button', { name: /^créer$/i }).first().click()

  // ---- 5. l'étude porte le montant attendu : 40 × 148,60
  await page.getByRole('button', { name: 'Créer une étude de prix' }).click()
  await page.getByRole('link', { name: 'Ouvrir' }).first().click()
  await page.waitForURL(/\/estimations\//)
  await expect(
    ligneDuPoste(page).getByRole('cell', { name: TOTAL_ATTENDU, exact: true }),
  ).toBeVisible({ timeout: 20_000 })

  // ---- 6. geler, émettre, et lire le PDF
  await page.getByRole('button', { name: 'Geler cette version' }).click()
  await page.getByRole('button', { name: /confirmer/i }).click()
  await expect(page.getByText('Gelée', { exact: true })).toBeVisible()

  await page.getByTestId('emission-du-devis').getByRole('button', { name: 'Émettre le devis' }).click()
  await page.getByLabel('Valable jusqu’au').fill('2027-12-31')
  await page.getByTestId('confirmer-l-emission').click()
  await expect(page.getByTestId('devis-emis')).toBeVisible({ timeout: 30_000 })

  const [telechargement] = await Promise.all([
    page.waitForEvent('download'),
    page.getByTestId('telecharger-le-devis').click(),
  ])
  const chemin_pdf = await telechargement.path()
  const octets = chemin_pdf ? await readFile(chemin_pdf) : Buffer.alloc(0)
  expect(createHash('sha256').update(octets).digest('hex')).toHaveLength(64)

  // ---- 7. le prix du classeur est IMPRIMÉ sur le devis remis au client
  //
  // C'est le point du parcours. Tout le reste — lecture, bornes, refus — sert
  // à ce qu'un nombre saisi dans le tableur d'un fournisseur arrive juste sur
  // le papier que le client reçoit.
  const texte = texteDuPdf(octets)
  for (const attendu of [POSTE.position, POSTE.designation, '148.60', TOTAL_ATTENDU]) {
    expect(texte, `le PDF doit imprimer « ${attendu} »`).toContain(attendu)
  }
})
