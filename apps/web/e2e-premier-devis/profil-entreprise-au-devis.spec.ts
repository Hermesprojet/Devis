import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'

import { expect, test, type Page } from '@playwright/test'

import { ADMIN } from './banc'
import { seConnecter, texteDuPdf } from './parcours'
import { logoFictif } from './png-fictif'

/**
 * Le profil de l'entreprise, jusqu'à l'en-tête d'un devis remis.
 *
 * **Ce qui manquait, mesuré au navigateur avant ce travail :** l'organisation
 * n'avait ni adresse, ni coordonnées, ni logo, et le PDF n'imprimait que le
 * nom, la raison sociale et le numéro d'entreprise. Un client recevait un
 * devis sans savoir où répondre.
 *
 * **Ce scénario se place volontairement APRÈS « une organisation vide produit
 * son premier devis » et AVANT tout scénario qui émet.** L'ordre alphabétique
 * des fichiers le garantit. C'est ce qui permet d'éprouver le refus sur une
 * organisation qui n'a réellement pas de profil, sans vider artificiellement
 * des champs — puis de le compléter, ce dont les scénarios suivants ont besoin
 * pour émettre.
 *
 * La promesse centrale est la neuvième étape : **modifier le profil et le logo
 * ne change pas un devis déjà remis.** Elle se vérifie sur les octets, pas sur
 * l'écran — même empreinte SHA-256 avant et après.
 */

const ENTREPRISE = {
  nom: 'Terrassements Dubois',
  raisonSociale: 'Terrassements Dubois SA',
  numero: 'BE 0123.456.789',
  adresse: 'Rue Fictive du Chantier 12',
  complement: 'Zoning Nord, bâtiment C',
  codePostal: '5000',
  ville: 'Namur',
  pays: 'BE',
  email: 'contact@dubois.demo',
  telephone: '+32 81 00 00 00',
  site: 'https://dubois.demo',
}

const APRES = {
  nom: 'Dubois Travaux Publics',
  raisonSociale: 'Dubois Travaux Publics SRL',
  adresse: 'Chaussée Inventée 300',
  codePostal: '6000',
  ville: 'Charleroi',
}

const CLIENT = {
  nom: 'Commune fictive de Profil',
  numero: 'BE 0207.363.192',
  adresse: 'Place Inventée 1',
  codePostal: '1300',
  ville: 'Wavre',
}

const CHANTIER = { reference: 'PROFIL-001', nom: 'Chantier du profil' }

function empreinte(octets: Buffer): string {
  return createHash('sha256').update(octets).digest('hex')
}

/** Remplit le formulaire de profil, champ par champ. */
async function remplirLeProfil(page: Page, valeurs: Record<string, string>) {
  for (const [cle, valeur] of Object.entries(valeurs)) {
    await page.locator(`#profil-${cle}`).fill(valeur)
  }
  await page.getByTestId('profil-entreprise').getByRole('button', { name: 'Enregistrer' }).click()
}

test('le profil de l’entreprise remonte jusqu’à l’en-tête du devis remis', async ({
  page,
  browser,
}) => {
  test.setTimeout(240_000)
  await seConnecter(page, ADMIN)

  // ---- 1. l'organisation n'a pas de profil, et l'écran le dit
  await page.goto('/parametres')
  const profil = page.getByTestId('profil-entreprise')
  await expect(profil).toBeVisible()
  // Le nom d'origine, LU et non deviné : il vaut « Entreprise neuve » ici et
  // « Organisation de répétition » dans la répétition de préproduction. Il sera
  // rendu à la fin — voir l'étape 11.
  const nomInitial = await page.locator('#profil-name').inputValue()
  const insuffisant = page.getByTestId('profil-insuffisant')
  await expect(insuffisant).toBeVisible()
  // Le message NOMME les champs, il ne dit pas « profil incomplet ».
  await expect(insuffisant).toContainText("l'adresse")
  await expect(insuffisant).toContainText('le code postal')
  await expect(insuffisant).toContainText('la localité')
  await expect(page.getByTestId('logo-absent')).toBeVisible()

  // ---- 2. le chantier et son client, prêts à être chiffrés
  await page.goto('/clients')
  await page.getByRole('button', { name: 'Nouveau client' }).click()
  await page.getByLabel('Nom', { exact: true }).fill(CLIENT.nom)
  await page.getByLabel(/Numéro d.entreprise/).fill(CLIENT.numero)
  await page.getByLabel('Adresse de facturation').fill(CLIENT.adresse)
  await page.getByLabel('Code postal').fill(CLIENT.codePostal)
  await page.getByLabel('Localité').fill(CLIENT.ville)
  await page.getByRole('button', { name: 'Enregistrer' }).click()
  await expect(page.getByRole('cell', { name: CLIENT.nom })).toBeVisible()

  // La bibliothèque et son prix : créés ici s'ils n'existent pas encore. Joué
  // dans la suite, le scénario précédent les a laissés ; joué seul, il n'y a
  // rien. Les deux chemins mènent au même point de départ.
  await page.goto('/bibliotheque')
  const creerLaBibliotheque = page.getByRole('button', { name: 'Créer la bibliothèque' })
  const ajouterUnPrix = page.getByRole('button', { name: 'Ajouter un prix' })
  // Attendre que l'écran ait FINI de se charger avant de compter quoi que ce
  // soit. `goto` rend la main dès la page servie ; le contenu, lui, arrive
  // d'un appel d'API. Compter tout de suite trouvait zéro bouton et concluait
  // qu'une bibliothèque existait déjà — puis attendait en vain celle-ci.
  await expect(creerLaBibliotheque.or(ajouterUnPrix)).toBeVisible({ timeout: 15_000 })
  if (await creerLaBibliotheque.count()) {
    await creerLaBibliotheque.click()
  }
  await expect(ajouterUnPrix).toBeVisible({ timeout: 15_000 })
  if ((await page.getByText('TER-001').count()) === 0) {
    await ajouterUnPrix.click()
    await page.getByLabel('Code', { exact: true }).fill('TER-001')
    await page.getByLabel('Désignation').fill('Déblai en terrain meuble')
    await page.getByLabel('Unité').selectOption('m3')
    await page.getByLabel(/Prix unitaire HT/).fill('12.50')
    await page.getByRole('button', { name: 'Enregistrer le prix' }).click()
    await expect(page.getByText('TER-001').first()).toBeVisible()
  }

  await page.goto('/projets')
  await page.getByRole('button', { name: /nouveau projet/i }).first().click()
  await page.getByLabel(/référence/i).fill(CHANTIER.reference)
  await page.getByLabel(/^nom/i).first().fill(CHANTIER.nom)
  await page.getByRole('button', { name: /^créer$/i }).first().click()
  await page.getByRole('link', { name: CHANTIER.reference }).first().click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}$/)

  await page
    .getByTestId('selecteur-client')
    .selectOption({ label: `${CLIENT.nom} — ${CLIENT.ville}` })
  await page.getByRole('button', { name: 'Rattacher' }).click()

  await page.getByRole('button', { name: /^créer$/i }).first().click()
  await page.getByLabel('Poste').fill('01.10')
  await page.getByLabel('Désignation').fill('Déblai en terrain meuble')
  await page.getByLabel('Unité').fill('m3')
  await page.getByLabel('Quantité').fill('100')
  await page.locator('#source-nouveau').selectOption('library')
  await page.locator('#prix-nouveau').selectOption({ index: 1 })
  await page.getByRole('button', { name: /^créer$/i }).first().click()
  await expect(page.getByRole('cell', { name: '01.10' })).toBeVisible()

  await page.getByRole('button', { name: 'Créer une étude de prix' }).click()
  await page.getByRole('link', { name: 'Ouvrir' }).first().click()
  await page.waitForURL(/\/estimations\//)
  const urlVersion = page.url()
  await page.getByRole('button', { name: 'Geler cette version' }).click()
  await page.getByRole('button', { name: /confirmer/i }).click()
  await expect(page.getByText('Gelée', { exact: true })).toBeVisible()

  // ---- 3. émettre est REFUSÉ, et le refus nomme ce qui manque
  const emission = page.getByTestId('emission-du-devis')
  await emission.getByRole('button', { name: 'Émettre le devis' }).click()
  await page.getByTestId('confirmer-l-emission').click()
  const refus = page.getByRole('alert').filter({ hasText: /profil/i })
  await expect(refus.first()).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('devis-emis')).toHaveCount(0)

  // ---- 4. le profil se complète, et un logo se charge
  await page.goto('/parametres')
  await remplirLeProfil(page, {
    name: ENTREPRISE.nom,
    legal_name: ENTREPRISE.raisonSociale,
    company_number: ENTREPRISE.numero,
    address: ENTREPRISE.adresse,
    address_complement: ENTREPRISE.complement,
    postal_code: ENTREPRISE.codePostal,
    city: ENTREPRISE.ville,
    country_code: ENTREPRISE.pays,
    email: ENTREPRISE.email,
    phone: ENTREPRISE.telephone,
    website: ENTREPRISE.site,
  })
  await expect(page.getByTestId('profil-suffisant')).toBeVisible()
  await expect(page.getByTestId('profil-insuffisant')).toHaveCount(0)

  await page
    .locator('#profil-logo')
    .setInputFiles({ name: 'logo.png', mimeType: 'image/png', buffer: logoFictif(96, 96) })
  // `toBeVisible` ne suffit PAS : une image cassée occupe une boîte non vide
  // — son texte de remplacement — et passerait. On vérifie que le navigateur a
  // réellement décodé des pixels. C'est ce contrôle qui a révélé qu'une balise
  // <img> nue ne peut pas porter le jeton que la route exige.
  await expect(page.getByTestId('logo-actuel')).toBeVisible({ timeout: 15_000 })
  await expect
    .poll(() => page.getByTestId('logo-actuel').evaluate((n) => (n as HTMLImageElement).naturalWidth), {
      timeout: 15_000,
    })
    .toBeGreaterThan(0)
  await expect(page.getByTestId('logo-absent')).toHaveCount(0)

  // ---- 5. l'aperçu montre l'en-tête que le devis imprimera
  const apercu = page.getByTestId('apercu-entete')
  await expect(apercu).toContainText(ENTREPRISE.nom)
  await expect(apercu).toContainText(ENTREPRISE.raisonSociale)
  await expect(apercu).toContainText(ENTREPRISE.adresse)
  await expect(apercu).toContainText(ENTREPRISE.complement)
  await expect(apercu).toContainText(`${ENTREPRISE.codePostal} ${ENTREPRISE.ville}`)
  await expect(apercu).toContainText(ENTREPRISE.telephone)
  await expect(apercu).toContainText(ENTREPRISE.email)

  // ---- 6. l'émission passe, et le PDF porte l'identité complète
  await page.goto(urlVersion)
  await emission.getByRole('button', { name: 'Émettre le devis' }).click()
  await page.getByTestId('confirmer-l-emission').click()
  await expect(page.getByTestId('devis-emis')).toBeVisible({ timeout: 20_000 })
  const numero = (await page.getByTestId('numero-du-devis').innerText()).trim()
  expect(numero).toMatch(/^DEV-/)

  const [premierTelechargement] = await Promise.all([
    page.waitForEvent('download'),
    page.getByTestId('telecharger-le-devis').click(),
  ])
  const cheminPremier = await premierTelechargement.path()
  const octetsPremier = cheminPremier ? await readFile(cheminPremier) : Buffer.alloc(0)
  const empreintePremier = empreinte(octetsPremier)
  expect(empreintePremier).toHaveLength(64)

  // ---- 7. l'identité, LUE dans le texte imprimé
  const texte = texteDuPdf(octetsPremier)
  for (const attendu of [
    ENTREPRISE.nom,
    ENTREPRISE.raisonSociale,
    ENTREPRISE.adresse,
    ENTREPRISE.complement,
    `${ENTREPRISE.codePostal} ${ENTREPRISE.ville}`,
    ENTREPRISE.telephone,
    ENTREPRISE.email,
    ENTREPRISE.site,
    numero,
    CLIENT.nom,
  ]) {
    expect(texte, `« ${attendu} » doit figurer sur le devis`).toContain(attendu)
  }
  // Le logo voyage DANS les octets : le client l'ouvre hors ligne et le voit.
  expect(octetsPremier.includes(Buffer.from('/Subtype /Image'))).toBe(true)
  // Et aucun coût interne, comme toujours.
  for (const interdit of ['Déboursé', 'Revient', 'Marge']) {
    expect(texte, `« ${interdit} » ne doit pas figurer`).not.toContain(interdit)
  }

  // ---- 8. le profil change, ET LE DEVIS REMIS NE CHANGE PAS
  await page.goto('/parametres')
  await remplirLeProfil(page, {
    name: APRES.nom,
    legal_name: APRES.raisonSociale,
    address: APRES.adresse,
    postal_code: APRES.codePostal,
    city: APRES.ville,
  })
  await page
    .locator('#profil-logo')
    .setInputFiles({
      name: 'bandeau.png',
      mimeType: 'image/png',
      buffer: logoFictif(240, 60, [180, 40, 40]),
    })
  await expect
    .poll(() => page.getByTestId('logo-actuel').evaluate((n) => (n as HTMLImageElement).naturalWidth), {
      timeout: 15_000,
    })
    .toBeGreaterThan(0)

  await page.goto(urlVersion)
  const [reTelechargement] = await Promise.all([
    page.waitForEvent('download'),
    page.getByTestId('telecharger-le-devis').click(),
  ])
  const cheminRelu = await reTelechargement.path()
  const octetsRelus = cheminRelu ? await readFile(cheminRelu) : Buffer.alloc(0)
  expect(
    empreinte(octetsRelus),
    'le devis remis a changé après une modification du profil',
  ).toBe(empreintePremier)
  const texteRelu = texteDuPdf(octetsRelus)
  expect(texteRelu).toContain(ENTREPRISE.nom)
  expect(texteRelu).not.toContain(APRES.nom)
  expect(texteRelu).not.toContain(APRES.ville)

  // ---- 9. une nouvelle version porte la NOUVELLE identité
  // « Créer une nouvelle version » n'apparaît QUE sur une version gelée : c'est
  // le seul chemin honnête pour corriger un chiffrage déjà remis. Aucun écran
  // de confirmation — créer une version ne détruit rien.
  const urlAvant = page.url()
  await page.getByRole('button', { name: 'Créer une nouvelle version' }).click()
  await page.waitForURL((url) => url.toString() !== urlAvant, { timeout: 20_000 })
  await page.getByRole('button', { name: 'Geler cette version' }).click()
  await page.getByRole('button', { name: /confirmer/i }).click()
  await expect(page.getByText('Gelée', { exact: true })).toBeVisible()
  await page.getByTestId('emission-du-devis').getByRole('button', { name: 'Émettre le devis' }).click()
  await page.getByTestId('confirmer-l-emission').click()
  await expect(page.getByTestId('devis-emis')).toBeVisible({ timeout: 20_000 })

  const [secondTelechargement] = await Promise.all([
    page.waitForEvent('download'),
    page.getByTestId('telecharger-le-devis').click(),
  ])
  const cheminSecond = await secondTelechargement.path()
  const octetsSecond = cheminSecond ? await readFile(cheminSecond) : Buffer.alloc(0)
  const texteSecond = texteDuPdf(octetsSecond)
  expect(texteSecond).toContain(APRES.nom)
  expect(texteSecond).toContain(APRES.ville)
  expect(empreinte(octetsSecond)).not.toBe(empreintePremier)

  // ---- 10. le lien public montre la même identité, sans rien d'interne
  const secondNumero = (await page.getByTestId('numero-du-devis').innerText()).trim()
  await page.getByRole('link', { name: secondNumero }).first().click()
  await page.waitForURL(/\/devis-emis\/[0-9a-f-]{36}$/)
  await page.getByTestId('creer-le-lien').click()
  const lien = await page.getByTestId('url-du-lien').inputValue()

  const contexteClient = await browser.newContext()
  const pageClient = await contexteClient.newPage()
  await pageClient.goto(lien)
  await expect(pageClient.getByTestId('devis-public')).toBeVisible()

  const emetteur = pageClient.getByTestId('emetteur-public')
  await expect(emetteur).toContainText(APRES.nom)
  await expect(emetteur).toContainText(APRES.raisonSociale)
  await expect(emetteur).toContainText(APRES.adresse)
  await expect(emetteur).toContainText(`${APRES.codePostal} ${APRES.ville}`)
  await expect(emetteur).toContainText(ENTREPRISE.telephone)
  await expect(emetteur).toContainText(ENTREPRISE.email)
  // Le logo figé par CE devis, servi par sa propre route.
  await expect(pageClient.getByTestId('logo-public')).toBeVisible()
  await expect
    .poll(
      () =>
        pageClient
          .getByTestId('logo-public')
          .evaluate((n) => (n as HTMLImageElement).naturalWidth),
      { timeout: 15_000 },
    )
    .toBeGreaterThan(0)

  const htmlClient = await pageClient.content()
  for (const interne of ['Déboursé', 'déboursé', 'Revient', 'Marge']) {
    expect(htmlClient.includes(interne), `« ${interne} » ne doit pas figurer`).toBe(false)
  }
  await contexteClient.close()

  // ---- 11. rendre son nom à l'organisation partagée
  //
  // Les scénarios de cette suite se suivent dans UNE organisation. Celui-ci est
  // le seul à toucher son identité ; la laisser renommée imposerait aux
  // suivants un nom qu'ils n'ont pas choisi — et la répétition de
  // préproduction, qui rejoue cette suite puis vérifie après restauration que
  // l'organisation a retrouvé son nom d'amorçage, échouait précisément là.
  //
  // L'adresse reste complète : les scénarios qui suivent doivent pouvoir
  // émettre. Et rendre le nom ne défait rien de ce qui précède — c'est une
  // dernière fois la démonstration du point : les deux devis émis gardent
  // chacun l'identité qui était la sienne.
  await page.goto('/parametres')
  await remplirLeProfil(page, { name: nomInitial })
  await expect(page.getByTestId('profil-suffisant')).toBeVisible()

  await page.goto(urlVersion)
  const [ultime] = await Promise.all([
    page.waitForEvent('download'),
    page.getByTestId('telecharger-le-devis').click(),
  ])
  const cheminUltime = await ultime.path()
  const octetsUltimes = cheminUltime ? await readFile(cheminUltime) : Buffer.alloc(0)
  expect(empreinte(octetsUltimes), 'le premier devis a changé au dernier geste').toBe(
    empreintePremier,
  )
})
