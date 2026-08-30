import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'

import { expect, test, type Download, type Page } from '@playwright/test'

import { ADMIN } from './banc'
import { seConnecter, seDeconnecter } from './parcours'

/**
 * Le parcours commercial, prouvé DANS LE NAVIGATEUR.
 *
 * Ce que ce scénario établit, et que l'API seule n'établit pas : qu'une
 * personne peut, en cliquant, créer un client réutilisable, le poser sur un
 * chantier, geler une version, émettre un devis numéroté, en télécharger un
 * VRAI PDF, puis modifier la fiche client sans que le document déjà remis
 * change d'un octet — et enfin émettre un second devis sans écraser le
 * premier.
 *
 * Le PDF n'est pas jugé sur un code HTTP : il est ouvert, son en-tête lu, son
 * texte extrait, et son empreinte comparée d'un téléchargement à l'autre.
 *
 * Ce fichier suit `roles.spec.ts` dans l'ordre de la suite et s'appuie sur ce
 * que le parcours principal a laissé : une bibliothèque de prix, un taux en
 * vigueur, et les deux collaborateurs que l'administrateur a invités.
 */

const CLIENT = {
  nom: 'Commune de Perwez',
  numero: 'BE 0207.363.192',
  adresse: 'Rue Émile de Brabant 2',
  codePostal: '1360',
  ville: 'Perwez',
}

const CHANTIER = { reference: 'DEVIS-001', nom: 'Égouttage rue du Try' }
const LECTEUR = 'lea.lectrice@neuve.example'

/** Les octets d'un téléchargement, et rien d'autre. */
async function octets(telechargement: Download): Promise<Buffer> {
  const chemin = await telechargement.path()
  expect(chemin, 'le téléchargement doit avoir abouti sur un fichier').toBeTruthy()
  return readFileSync(chemin as string)
}

function empreinte(contenu: Buffer): string {
  return createHash('sha256').update(contenu).digest('hex')
}

/**
 * Le texte d'un PDF sans compression, tel que ce dépôt les écrit.
 *
 * On ne rend pas la page : on lit les chaînes littérales des opérateurs `Tj`.
 * C'est suffisant pour affirmer ce qui est IMPRIMÉ, et cela n'introduit
 * aucune dépendance de rendu dans la suite.
 */
function texteDuPdf(pdf: Buffer): string {
  const brut = pdf.toString('latin1')
  const morceaux: string[] = []
  for (const trouve of brut.matchAll(/\(((?:\\.|[^\\()])*)\)\s*Tj/g)) {
    morceaux.push(
      (trouve[1] ?? '').replace(/\\([0-7]{3})|\\(.)/g, (_, octal: string, echappe: string) =>
        octal ? String.fromCharCode(parseInt(octal, 8)) : echappe,
      ),
    )
  }
  return Buffer.from(morceaux.join('\n'), 'latin1').toString('latin1')
}

/** Le PDF du devis, téléchargé depuis l'écran comme un utilisateur le ferait. */
async function telechargerLeDevis(page: Page): Promise<Buffer> {
  const [fichier] = await Promise.all([
    page.waitForEvent('download'),
    page.getByTestId('telecharger-le-devis').click(),
  ])
  return octets(fichier)
}

test('un client réutilisable devient un devis émis, numéroté et téléchargeable en PDF', async ({
  page,
}) => {
  test.setTimeout(180_000)
  await seConnecter(page, ADMIN)

  // ---- 1. la fiche client, créée depuis l'écran « Clients »
  await page.goto('/clients')
  await page.getByRole('button', { name: 'Nouveau client' }).click()
  await page.getByLabel('Nom', { exact: true }).fill(CLIENT.nom)
  // L'apostrophe des libellés est typographique (U+2019) : une apostrophe
  // droite ne trouve rien, et l'échec accuse l'écran au lieu du test.
  await page.getByLabel(/Numéro d.entreprise/).fill(CLIENT.numero)
  await page.getByLabel('Adresse de facturation').fill(CLIENT.adresse)
  await page.getByLabel('Code postal').fill(CLIENT.codePostal)
  await page.getByLabel('Localité').fill(CLIENT.ville)
  await page.getByRole('button', { name: 'Enregistrer' }).click()
  await expect(page.getByRole('cell', { name: CLIENT.nom })).toBeVisible()

  // ---- 2. le chantier, et la fiche qu'on lui rattache EXPLICITEMENT
  await page.goto('/projets')
  await page.getByRole('button', { name: /nouveau projet/i }).first().click()
  await page.getByLabel(/référence/i).fill(CHANTIER.reference)
  await page.getByLabel(/^nom/i).first().fill(CHANTIER.nom)
  await page.getByRole('button', { name: /^créer$/i }).first().click()
  await page.getByRole('link', { name: CHANTIER.reference }).first().click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}$/)
  const urlChantier = page.url()

  // Un chantier neuf n'a pas de fiche : l'écran le dit et demande un choix,
  // il ne devine pas.
  await expect(page.getByText(/n’a pas encore de fiche client|n'a pas encore de fiche client/))
    .toBeVisible()
  await page.getByTestId('selecteur-client').selectOption({ label: `${CLIENT.nom} — ${CLIENT.ville}` })
  await page.getByRole('button', { name: 'Rattacher' }).click()
  await expect(page.getByTestId('client-du-chantier')).toContainText(CLIENT.nom)

  // ---- 3. le bordereau, une ligne chiffrée, et l'étude
  await page.getByRole('button', { name: /^créer$/i }).first().click()
  await expect(page.getByLabel('Poste')).toBeVisible()
  await page.getByLabel('Poste').fill('02.10')
  await page.getByLabel('Désignation').fill('Terrassement pour égouttage')
  await page.getByLabel('Unité').fill('m3')
  await page.getByLabel('Quantité').fill('400')
  await page.getByLabel('Prix unitaire').selectOption({ index: 1 })
  await page.getByRole('button', { name: /^créer$/i }).first().click()
  await expect(page.getByRole('cell', { name: '02.10' })).toBeVisible()

  await page.getByRole('button', { name: 'Créer une étude de prix' }).click()
  await page.getByRole('link', { name: 'Ouvrir' }).first().click()
  await page.waitForURL(/\/estimations\//)
  const urlVersion1 = page.url()

  // ---- 4. tant que la version n'est pas gelée, l'émission est annoncée
  //         impossible — et le bouton ne ment pas
  const emission = page.getByTestId('emission-du-devis')
  await expect(emission).toContainText('Gelez d’abord la version')
  await expect(emission.getByRole('button', { name: 'Émettre le devis' })).toBeDisabled()

  // ---- 5. le gel, puis l'émission
  await page.getByRole('button', { name: 'Geler cette version' }).click()
  await page.getByRole('button', { name: /confirmer/i }).click()
  await expect(page.getByText('Gelée', { exact: true })).toBeVisible()

  await emission.getByRole('button', { name: 'Émettre le devis' }).click()
  // L'écran dit que c'est définitif AVANT de le faire, et montre à qui le
  // devis s'adresse.
  await expect(emission).toContainText(/L’émission est définitive|L'émission est définitive/)
  await expect(page.getByTestId('recapitulatif-client')).toContainText(CLIENT.nom)
  await expect(page.getByTestId('recapitulatif-client')).toContainText(CLIENT.adresse)
  await page.getByLabel('Valable jusqu’au').fill('2027-12-31')
  await page.getByLabel('Conditions et note au client').fill(
    'Offre valable sauf vente. Acompte de 30 % à la commande.',
  )
  await page.getByTestId('confirmer-l-emission').click()

  // ---- 6. le numéro, les dates, l'état
  const emis = page.getByTestId('devis-emis')
  await expect(emis).toBeVisible()
  const numero = (await page.getByTestId('numero-du-devis').innerText()).trim()
  expect(numero, 'le devis doit porter un numéro').toMatch(/\S/)
  await expect(emis).toContainText('31/12/2027')
  await expect(emis).toContainText(CLIENT.nom)

  // ---- 7. le PDF : un vrai fichier, pas du HTML renommé
  const pdf = await telechargerLeDevis(page)
  expect(pdf.subarray(0, 5).toString('latin1'), 'un PDF commence par %PDF-').toBe('%PDF-')
  expect(pdf.toString('latin1')).toContain('%%EOF')
  expect(pdf.toString('latin1').toLowerCase()).not.toContain('<html')

  const texte = texteDuPdf(pdf)
  for (const attendu of [numero, CLIENT.nom, CLIENT.ville, CHANTIER.reference, 'Acompte de 30 %']) {
    expect(texte, `le PDF doit imprimer « ${attendu} »`).toContain(attendu)
  }
  // Aucun coût interne : c'est le cas par défaut, et c'est le cas dangereux.
  for (const interdit of ['Déboursé', 'Revient', 'Marge']) {
    expect(texte, `« ${interdit} » ne doit pas figurer sur un devis client`).not.toContain(interdit)
  }

  // ---- 8. deux téléchargements rendent exactement les mêmes octets
  const second = await telechargerLeDevis(page)
  expect(empreinte(second)).toBe(empreinte(pdf))

  // ---- 9. modifier la fiche client ne change pas le devis DÉJÀ remis
  await page.goto('/clients')
  await page.getByRole('button', { name: 'Modifier' }).first().click()
  await page.getByLabel('Adresse de facturation').fill('Nouvelle adresse 99')
  await page.getByLabel('Localité').fill('Jodoigne')
  await page.getByRole('button', { name: 'Enregistrer' }).click()
  await expect(page.getByRole('cell', { name: 'Jodoigne' })).toBeVisible()

  await page.goto(urlVersion1)
  const apres = await telechargerLeDevis(page)
  expect(empreinte(apres), 'le devis remis a changé après une modification du client').toBe(
    empreinte(pdf),
  )
  const texteApres = texteDuPdf(apres)
  expect(texteApres).toContain(CLIENT.ville)
  expect(texteApres).not.toContain('Jodoigne')
  expect(texteApres).not.toContain('Nouvelle adresse 99')

  // ---- 10. une nouvelle version, un nouveau devis, et l'ancien intact
  //
  // Une version gelée ne se rouvre pas : on en crée une NOUVELLE. C'est le
  // seul chemin qui corrige un chiffrage remis sans réécrire le document.
  await page.goto(urlVersion1)
  await page.getByRole('button', { name: 'Créer une nouvelle version' }).click()
  await page.waitForURL((url) => /\/estimations\//.test(url.toString()) && url.toString() !== urlVersion1)
  await expect(page.getByText('Brouillon', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: 'Geler cette version' }).click()
  await page.getByRole('button', { name: /confirmer/i }).click()
  await expect(page.getByText('Gelée', { exact: true })).toBeVisible()

  const emission2 = page.getByTestId('emission-du-devis')
  await emission2.getByRole('button', { name: 'Émettre le devis' }).click()
  await page.getByTestId('confirmer-l-emission').click()
  const numero2 = (await page.getByTestId('numero-du-devis').innerText()).trim()
  expect(numero2, 'le second devis doit porter un autre numéro').not.toBe(numero)

  // L'historique du chantier porte les deux, et le premier PDF est inchangé.
  const historique = page.getByTestId('emission-du-devis').locator('tbody tr')
  await expect(historique).toHaveCount(2)
  await page.goto(urlVersion1)
  const premierRelu = await telechargerLeDevis(page)
  expect(empreinte(premierRelu), "l'émission suivante a réécrit le devis précédent").toBe(
    empreinte(pdf),
  )
  // Et la version déjà émise ne propose plus d'émettre.
  await expect(page.getByTestId('emission-du-devis')).toContainText('Devis émis')
  await expect(
    page.getByTestId('emission-du-devis').getByRole('button', { name: 'Émettre le devis' }),
  ).toHaveCount(0)

  // ---- 11. aucun bouton qu'un rôle ne pourrait pas actionner
  await seDeconnecter(page)
  await seConnecter(page, LECTEUR)
  await page.goto(urlVersion1)
  await expect(page.getByTestId('emission-du-devis')).toBeVisible()
  await expect(
    page.getByTestId('emission-du-devis').getByRole('button', { name: 'Émettre le devis' }),
  ).toHaveCount(0)
  await page.goto('/clients')
  await expect(page.getByRole('cell', { name: CLIENT.nom })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Nouveau client' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Modifier' })).toHaveCount(0)
})
