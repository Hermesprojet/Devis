import { writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { expect, test, type Page } from '@playwright/test'

import { ADMIN } from './banc'

/**
 * Ce que les autres rôles peuvent faire de la configuration de l'entreprise.
 *
 * Ces deux vérifications suivent le parcours principal et s'appuient sur ce
 * qu'il a laissé : un taux en vigueur, une bibliothèque, un devis gelé. Elles
 * ne recréent rien.
 *
 * L'interface n'est pas l'autorité — l'API refuse, et c'est éprouvé dans
 * `apps/api/tests/test_organisation_neuve.py`. Ce qui se vérifie ici est
 * l'autre moitié : ne pas proposer une commande qui ne pourrait pas aboutir.
 */

const METREUR = 'marc.metreur@neuve.example'
const LECTEUR = 'lea.lectrice@neuve.example'

async function seConnecter(page: Page, adresse: string): Promise<void> {
  await page.goto('/')
  await page.getByRole('button', { name: /compte de l'entreprise/ }).click()
  await page.locator('#email').fill(adresse)
  await page.getByRole('button', { name: 'Se connecter' }).click()
  await page.waitForURL(/\/projets$/)
}

async function seDeconnecter(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Se déconnecter' }).click()
  await page.waitForURL(/\/$/)
}

test("l'administrateur compose son équipe depuis les réglages", async ({ page }) => {
  await seConnecter(page, ADMIN)
  await page.goto('/parametres')

  const encart = page.getByTestId('collaborateurs')
  await expect(encart).toBeVisible()

  for (const [adresse, role] of [
    [METREUR, 'Métreur / deviseur'],
    [LECTEUR, 'Lecteur / auditeur'],
  ] as const) {
    await page.getByRole('button', { name: 'Ajouter un collaborateur' }).click()
    await page.getByLabel('Adresse e-mail').fill(adresse)
    await page.getByLabel('Nom affiché').fill(adresse.split('@')[0] ?? adresse)
    await page.getByLabel('Rôle', { exact: true }).selectOption({ label: role })
    await page.getByRole('button', { name: 'Ajouter le collaborateur' }).click()
    await expect(encart.getByRole('cell', { name: adresse })).toBeVisible()
  }
  await seDeconnecter(page)
})

test('un métreur utilise la configuration mais ne la modifie pas', async ({ page }) => {
  await seConnecter(page, METREUR)

  // Il chiffre : il voit les projets et le devis de l'entreprise.
  await expect(page.getByRole('link', { name: 'PREM-001' })).toBeVisible()

  await page.goto('/parametres')
  // Le taux lui est LISIBLE — il travaille avec — mais aucune commande ne le
  // lui propose à la modification.
  await expect(page.getByRole('cell', { name: 'TVA-21' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Ajouter un taux de taxe' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Retirer du service' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Supprimer' })).toHaveCount(0)

  // Et l'équipe ne le regarde pas : il ne peut pas la composer.
  await expect(page.getByTestId('collaborateurs')).toHaveCount(0)

  // Témoin positif : ce qu'il PEUT faire lui est bien proposé. Sans ce
  // contrôle, un composant qui ne s'afficherait jamais passerait pour un
  // garde-fou.
  await page.goto('/bibliotheque')
  await expect(page.getByRole('button', { name: 'Ajouter un prix' })).toBeVisible()

  await seDeconnecter(page)
})

test('un métreur joint et révise une pièce du chantier', async ({ page }) => {
  const metre = join(tmpdir(), 'metreo-metre-metreur.csv')
  writeFileSync(metre, 'position;designation;unite;quantite\n01.10;Déblai;m3;1250,5\n')

  await seConnecter(page, METREUR)
  await page.getByRole('link', { name: 'PREM-001' }).click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}$/)

  const section = page.getByTestId('documents')
  await section.getByLabel('Catégorie').selectOption('Métré')
  await section.getByLabel('Fichier à joindre').setInputFiles(metre)
  await expect(section.getByRole('cell', { name: 'metreo-metre-metreur.csv' }).first()).toBeVisible()
  // Le dépôt lui est attribué : c'est ce que l'entreprise doit pouvoir lire.
  await expect(section.getByRole('cell', { name: METREUR }).first()).toBeVisible()

  await seDeconnecter(page)
})

test('un lecteur consulte le devis sans aucune commande interdite', async ({ page }) => {
  await seConnecter(page, LECTEUR)

  await page.getByRole('link', { name: 'PREM-001' }).click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}$/)
  await page.getByRole('link', { name: 'Ouvrir' }).first().click()
  await page.waitForURL(/\/estimations\//)

  // Il lit les montants du devis remis au client.
  await expect(page.getByText('Gelée', { exact: true })).toBeVisible()
  await expect(page.getByText('23080.10 EUR').first()).toBeVisible()

  // Aucune commande d'écriture ne lui est proposée : ni gel, ni saisie.
  await expect(page.getByRole('button', { name: 'Geler cette version' })).toHaveCount(0)
  // Le déboursé sec est un coût interne : il ne relève pas de ce rôle.
  await expect(page.getByRole('button', { name: 'Export interne' })).toHaveCount(0)

  await page.goto('/parametres')
  await expect(page.getByRole('button', { name: 'Ajouter un taux de taxe' })).toHaveCount(0)
  await expect(page.getByTestId('collaborateurs')).toHaveCount(0)

  await page.goto('/bibliotheque')
  await expect(page.getByRole('button', { name: 'Ajouter un prix' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Créer la bibliothèque' })).toHaveCount(0)
  // L'absence s'explique, elle ne laisse pas devant une page vide.
  await expect(page.getByText(/Demandez à un responsable/)).toBeVisible()
})

test('un lecteur télécharge les pièces mais n’en dépose aucune', async ({ page }) => {
  await seConnecter(page, LECTEUR)
  await page.getByRole('link', { name: 'PREM-001' }).click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}$/)

  const section = page.getByTestId('documents')
  await expect(section).toBeVisible()
  // Il lit ce que les autres ont déposé…
  await expect(section.getByRole('cell', { name: 'metreo-metre-metreur.csv' }).first()).toBeVisible()
  const [recu] = await Promise.all([
    page.waitForEvent('download'),
    section.getByRole('button', { name: 'Télécharger' }).first().click(),
  ])
  expect(recu.suggestedFilename()).toBe('metreo-metre-metreur.csv')

  // … et aucune commande d'écriture ne lui est proposée.
  await expect(section.getByLabel('Fichier à joindre')).toHaveCount(0)
  await expect(section.getByRole('button', { name: 'Archiver' })).toHaveCount(0)
  await expect(section.getByLabel('Joindre une nouvelle révision')).toHaveCount(0)
})
