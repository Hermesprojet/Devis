import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'

import { expect, test, type Download, type Page } from '@playwright/test'

import { ADMIN, BASE_API } from './banc'
import { seConnecter } from './parcours'

/**
 * Le cycle commercial, de l'émission à la réponse, dans DEUX navigateurs.
 *
 * Un contexte authentifié pour l'entreprise, un contexte vierge pour le
 * client. Ils ne partagent ni cookie ni stockage : c'est la seule façon de
 * prouver que le destinataire n'emprunte rien à la session de l'entreprise, et
 * que son seul droit d'accès est le secret du lien.
 *
 * Ce que ce parcours établit, et qu'aucun test d'API ne peut établir :
 *
 *  - le secret disparaît de la barre d'adresse avant le premier appel réseau ;
 *  - il n'atterrit ni dans `localStorage`, ni dans `sessionStorage` ;
 *  - le PDF que le client télécharge est celui que l'entreprise a émis, au
 *    bit près ;
 *  - rejouer la même acceptation ne crée pas une seconde réponse, et une
 *    réponse opposée est refusée ;
 *  - modifier la fiche client ensuite ne change pas le document remis.
 */

const CLIENT = {
  nom: 'Commune de Jodoigne',
  adresse: 'Rue du Château 13',
  codePostal: '1370',
  ville: 'Jodoigne',
}

const CHANTIER = { reference: 'REPONSE-001', nom: 'Réfection du parvis' }

function empreinte(contenu: Buffer): string {
  return createHash('sha256').update(contenu).digest('hex')
}

async function octets(telechargement: Download): Promise<Buffer> {
  const chemin = await telechargement.path()
  expect(chemin, 'le téléchargement doit avoir abouti').toBeTruthy()
  return readFileSync(chemin as string)
}

/** Le devis émis, jusqu'à sa fiche de suivi. Rend l'URL de la fiche. */
async function emettreUnDevis(page: Page): Promise<{ fiche: string; empreinte: string }> {
  await page.goto('/clients')
  await page.getByRole('button', { name: 'Nouveau client' }).click()
  await page.getByLabel('Nom', { exact: true }).fill(CLIENT.nom)
  await page.getByLabel('Adresse de facturation').fill(CLIENT.adresse)
  await page.getByLabel('Code postal').fill(CLIENT.codePostal)
  await page.getByLabel('Localité').fill(CLIENT.ville)
  await page.getByRole('button', { name: 'Enregistrer' }).click()
  await expect(page.getByRole('cell', { name: CLIENT.nom })).toBeVisible()

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
  await expect(page.getByTestId('client-du-chantier')).toContainText(CLIENT.nom)

  await page.getByRole('button', { name: /^créer$/i }).first().click()
  await expect(page.getByLabel('Poste')).toBeVisible()
  await page.getByLabel('Poste').fill('03.10')
  await page.getByLabel('Désignation').fill('Déblai pour le parvis')
  // m3, comme le prix de bibliothèque : le moteur REFUSE de convertir des m2
  // en m3 sans masse volumique sourcée, et il a raison de le refuser.
  await page.getByLabel('Unité').fill('m3')
  await page.getByLabel('Quantité').fill('320')
  await page.getByLabel('Prix unitaire').selectOption({ index: 1 })
  await page.getByRole('button', { name: /^créer$/i }).first().click()
  await expect(page.getByRole('cell', { name: '03.10' })).toBeVisible()

  await page.getByRole('button', { name: 'Créer une étude de prix' }).click()
  await page.getByRole('link', { name: 'Ouvrir' }).first().click()
  await page.waitForURL(/\/estimations\//)

  await page.getByRole('button', { name: 'Geler cette version' }).click()
  await page.getByRole('button', { name: /confirmer/i }).click()
  await expect(page.getByText('Gelée', { exact: true })).toBeVisible()

  const emission = page.getByTestId('emission-du-devis')
  await emission.getByRole('button', { name: 'Émettre le devis' }).click()
  await page.getByTestId('confirmer-l-emission').click()
  await expect(page.getByTestId('devis-emis')).toBeVisible()
  const numero = (await page.getByTestId('numero-du-devis').innerText()).trim()

  const pdf = await octets(
    (
      await Promise.all([
        page.waitForEvent('download'),
        page.getByTestId('telecharger-le-devis').click(),
      ])
    )[0],
  )

  await page.getByRole('link', { name: numero }).first().click()
  await page.waitForURL(/\/devis-emis\/[0-9a-f-]{36}$/)
  return { fiche: page.url(), empreinte: empreinte(pdf) }
}

test('un devis émis se partage, se consulte et se répond depuis un autre navigateur', async ({
  page,
  browser,
}) => {
  test.setTimeout(240_000)

  // ---- 1. l'entreprise émet, puis crée le lien
  await seConnecter(page, ADMIN)
  const { fiche, empreinte: empreinteEmise } = await emettreUnDevis(page)

  await expect(page.getByTestId('etat-du-devis')).toHaveText('Émis')
  await page.getByTestId('creer-le-lien').click()
  const url = await page.getByTestId('url-du-lien').inputValue()
  expect(url, 'le lien doit porter son secret dans le fragment').toContain('#')
  const avantFragment = url.split('#')[0] ?? ''
  const secret = url.split('#')[1] ?? ''
  expect(secret.length, 'un secret de 256 bits fait au moins 40 caractères').toBeGreaterThan(40)
  expect(avantFragment, 'aucun secret dans la chaîne de requête').not.toContain('?')

  // Créer un lien n'est PAS transmettre : l'état ne bouge pas.
  await expect(page.getByTestId('etat-du-devis')).toHaveText('Émis')

  await page.getByTestId('marquer-transmis').click()
  await page.getByLabel('Canal').selectOption('email')
  await page.getByTestId('confirmer-saisie').click()
  await expect(page.getByTestId('etat-du-devis')).toHaveText('Transmis')

  // ---- 2. le client ouvre le lien dans un navigateur qui ne connaît rien
  const contexteClient = await browser.newContext()
  const pageClient = await contexteClient.newPage()
  await pageClient.goto(url)
  await expect(pageClient.getByTestId('devis-public')).toBeVisible()

  // Le fragment a disparu de la barre d'adresse.
  expect(pageClient.url(), "le secret est resté dans l'URL").not.toContain(secret)
  expect(new URL(pageClient.url()).hash).toBe('')

  // Et il n'est nulle part dans les stockages du navigateur.
  const stockages = await pageClient.evaluate(() => ({
    local: JSON.stringify(window.localStorage),
    session: JSON.stringify(window.sessionStorage),
  }))
  expect(stockages.local, 'le secret a été écrit dans localStorage').not.toContain(secret)
  expect(stockages.session, 'le secret a été écrit dans sessionStorage').not.toContain(secret)

  await expect(pageClient.getByTestId('destinataire-public')).toContainText(CLIENT.nom)
  await expect(pageClient.getByTestId('destinataire-public')).toContainText(CLIENT.adresse)
  const empreinteAffichee = await pageClient.getByTestId('empreinte-publique').innerText()
  expect(empreinteAffichee).toContain(empreinteEmise)

  // ---- 3. le client télécharge, et c'est le MÊME fichier
  const pdfClient = await octets(
    (
      await Promise.all([
        pageClient.waitForEvent('download'),
        pageClient.getByTestId('telecharger-public').click(),
      ])
    )[0],
  )
  expect(empreinte(pdfClient), 'le PDF du client diffère de celui qui a été émis').toBe(
    empreinteEmise,
  )

  // ---- 4. il accepte, avec une confirmation qui nomme le devis et le montant
  const totalTtc = (await pageClient.getByTestId('total-ttc-public').innerText()).trim()
  const numero = (await pageClient.getByTestId('numero-public').innerText()).trim()
  await pageClient.getByTestId('choisir-accepter').click()
  const confirmation = pageClient.getByTestId('confirmation')
  await expect(confirmation).toContainText(numero)
  await expect(confirmation).toContainText(totalTtc.split(' ')[0] ?? '')
  await pageClient.getByLabel(/Votre nom/).fill('Marie Dupont')
  await pageClient.getByLabel('Votre courriel').fill('marie.dupont@jodoigne.example')
  await pageClient.getByTestId('confirmer-reponse').click()
  await expect(pageClient.getByTestId('recu')).toBeVisible()
  await expect(pageClient.getByTestId('recu-decision')).toHaveText('Accepté')

  // La mention est là, et elle ne prétend rien de plus.
  // L'apostrophe rendue par `&apos;` est droite, pas typographique : le motif
  // accepte les deux plutôt que de dépendre de l'entité choisie dans le JSX.
  await expect(pageClient.getByTestId('mention-identite')).toContainText(
    /pas d.une signature électronique qualifiée/,
  )

  // ---- 5. rejouer la même acceptation, puis tenter le contraire
  //
  // Depuis la page elle-même, avec son cookie : c'est ce que produit un
  // double-clic, un renvoi après coupure réseau, ou un bouton « précédent »
  // suivi d'un renvoi de formulaire. L'API promet l'idempotence ; on la
  // vérifie là où elle sera exercée.
  const rejoue = await pageClient.evaluate(async (base) => {
    const envoyer = (decision: string) =>
      fetch(`${base}/public/quote/response`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          decision,
          respondent_name: 'Marie Dupont',
          confirmed: true,
        }),
      }).then(async (r) => ({ statut: r.status, corps: await r.json() }))
    return {
      identique: await envoyer('accepted'),
      oppose: await envoyer('declined'),
    }
  }, BASE_API)

  expect(rejoue.identique.statut, "rejouer la même réponse doit aboutir").toBe(200)
  expect(rejoue.identique.corps.created, 'une seconde réponse a été écrite').toBe(false)
  expect(rejoue.oppose.statut, 'une réponse opposée doit être refusée').toBe(409)
  expect(rejoue.oppose.corps.detail.code).toBe('quote_already_answered')

  await contexteClient.close()

  // ---- 6. retour côté entreprise : le tableau, la chronologie, l'identité
  await page.goto('/devis-emis')
  const ligne = page.locator(`tr[data-quote-number="${numero}"]`)
  await expect(ligne).toBeVisible()
  await expect(ligne).toContainText('Accepté')

  await page.goto(fiche)
  await expect(page.getByTestId('etat-du-devis')).toHaveText('Accepté')
  const chronologie = page.getByTestId('chronologie')
  await expect(chronologie.locator('tr[data-event-kind="link_created"]')).toHaveCount(1)
  await expect(chronologie.locator('tr[data-event-kind="transmitted"]')).toHaveCount(1)
  await expect(chronologie.locator('tr[data-event-kind="viewed"]')).toHaveCount(1)
  await expect(chronologie.locator('tr[data-event-kind="accepted"]')).toHaveCount(1)
  await expect(chronologie).toContainText('Marie Dupont')
  await expect(chronologie).toContainText('marie.dupont@jodoigne.example')

  // ---- 7. la fiche client change ; le devis remis, non
  await page.goto('/clients')
  await page
    .locator('tr', { hasText: CLIENT.nom })
    .first()
    .getByRole('button', { name: 'Modifier' })
    .click()
  await page.getByLabel('Adresse de facturation').fill('Adresse changée 99')
  await page.getByRole('button', { name: 'Enregistrer' }).click()
  await expect(page.getByRole('cell', { name: 'Adresse changée 99' })).toBeVisible()

  await page.goto(fiche)
  await expect(page.getByTestId('destinataire-fige')).toContainText(CLIENT.adresse)
  await expect(page.getByTestId('destinataire-fige')).not.toContainText('Adresse changée 99')
  const relu = await octets(
    (
      await Promise.all([
        page.waitForEvent('download'),
        page.getByRole('button', { name: 'Télécharger le PDF' }).first().click(),
      ])
    )[0],
  )
  expect(empreinte(relu), 'le document remis a changé').toBe(empreinteEmise)
})

const REFUS = {
  client: { nom: 'SPRL Toitures Lambert', adresse: 'Chaussée de Wavre 220', cp: '1300', ville: 'Wavre' },
  chantier: { reference: 'REPONSE-002', nom: 'Réfection de toiture' },
}

test('un refus et une réponse hors ligne se tracent aussi', async ({ page, browser }) => {
  test.setTimeout(240_000)
  await seConnecter(page, ADMIN)

  // ---- un second devis, monté au plus court
  await page.goto('/clients')
  await page.getByRole('button', { name: 'Nouveau client' }).click()
  await page.getByLabel('Nom', { exact: true }).fill(REFUS.client.nom)
  await page.getByLabel('Adresse de facturation').fill(REFUS.client.adresse)
  await page.getByLabel('Code postal').fill(REFUS.client.cp)
  await page.getByLabel('Localité').fill(REFUS.client.ville)
  await page.getByRole('button', { name: 'Enregistrer' }).click()

  await page.goto('/projets')
  await page.getByRole('button', { name: /nouveau projet/i }).first().click()
  await page.getByLabel(/référence/i).fill(REFUS.chantier.reference)
  await page.getByLabel(/^nom/i).first().fill(REFUS.chantier.nom)
  await page.getByRole('button', { name: /^créer$/i }).first().click()
  await page.getByRole('link', { name: REFUS.chantier.reference }).first().click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}$/)
  await page
    .getByTestId('selecteur-client')
    .selectOption({ label: `${REFUS.client.nom} — ${REFUS.client.ville}` })
  await page.getByRole('button', { name: 'Rattacher' }).click()
  await page.getByRole('button', { name: /^créer$/i }).first().click()
  await expect(page.getByLabel('Poste')).toBeVisible()
  await page.getByLabel('Poste').fill('04.10')
  await page.getByLabel('Désignation').fill('Déblai de fondation')
  await page.getByLabel('Unité').fill('m3')
  await page.getByLabel('Quantité').fill('180')
  await page.getByLabel('Prix unitaire').selectOption({ index: 1 })
  await page.getByRole('button', { name: /^créer$/i }).first().click()
  await page.getByRole('button', { name: 'Créer une étude de prix' }).click()
  await page.getByRole('link', { name: 'Ouvrir' }).first().click()
  await page.waitForURL(/\/estimations\//)
  await page.getByRole('button', { name: 'Geler cette version' }).click()
  await page.getByRole('button', { name: /confirmer/i }).click()
  await page
    .getByTestId('emission-du-devis')
    .getByRole('button', { name: 'Émettre le devis' })
    .click()
  await page.getByTestId('confirmer-l-emission').click()
  const numero = (await page.getByTestId('numero-du-devis').innerText()).trim()
  await page.getByRole('link', { name: numero }).first().click()
  await page.waitForURL(/\/devis-emis\/[0-9a-f-]{36}$/)
  const fiche = page.url()

  // ---- 1. le client REFUSE par le lien
  await page.getByTestId('creer-le-lien').click()
  const url = await page.getByTestId('url-du-lien').inputValue()

  const contexteClient = await browser.newContext()
  const pageClient = await contexteClient.newPage()
  await pageClient.goto(url)
  await pageClient.getByTestId('choisir-refuser').click()
  // Refuser n'oblige personne à se nommer.
  await pageClient.getByLabel(/Motif/).fill('Budget reporté à l’exercice suivant.')
  await pageClient.getByTestId('confirmer-reponse').click()
  await expect(pageClient.getByTestId('recu-decision')).toHaveText('Refusé')
  await contexteClient.close()

  await page.goto(fiche)
  await expect(page.getByTestId('etat-du-devis')).toHaveText('Refusé')
  await expect(page.getByTestId('chronologie')).toContainText('Budget reporté')

  // ---- 2. le parcours hors ligne, sur une saisie erronée puis corrigée
  //
  // Une décision est déjà prise : les boutons de réponse ont disparu, et seule
  // la transmission reste enregistrable. C'est ce qu'on éprouve.
  await expect(page.getByTestId('acceptation-hors-ligne')).toHaveCount(0)
  await page.getByTestId('marquer-transmis').click()
  await page.getByLabel('Canal').selectOption('email')
  await page.getByLabel(/Note/).fill('Renvoyé par courriel le 12/03.')
  await page.getByTestId('confirmer-saisie').click()
  await expect(page.getByTestId('chronologie')).toContainText('Renvoyé par courriel')

  const transmis = page
    .getByTestId('chronologie')
    .locator('tr[data-event-kind="transmitted"]')
    .first()
  await transmis.getByRole('button', { name: 'Corriger' }).click()
  await page.getByLabel('Motif de la correction').fill('Envoyé par téléphone, pas par courriel.')
  await page.getByRole('button', { name: /confirmer/i }).click()

  // La ligne d'origine est TOUJOURS là, barrée, avec son motif en regard.
  await expect(page.getByTestId('chronologie')).toContainText('Renvoyé par courriel')
  await expect(page.getByTestId('motif-de-correction')).toContainText('téléphone')
  // Et le refus, lui, n'a pas bougé : corriger une transmission ne défait pas
  // une décision.
  await expect(page.getByTestId('etat-du-devis')).toHaveText('Refusé')
})
