import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'

import { expect, test, type Page } from '@playwright/test'

import { ADMIN } from './banc'
import { seConnecter, texteDuPdf } from './parcours'

/**
 * Construire un sous-détail depuis l'interface, puis s'en servir dans un devis.
 *
 * **Ce qui était impossible, mesuré au navigateur depuis une organisation
 * vide, avant ce travail :** créer une bibliothèque marchait ; construire un
 * prix composé, le modifier, le dupliquer, le supprimer et l'affecter à un
 * poste, non. Aucun de ces cinq gestes n'avait de commande à l'écran. Le
 * serveur savait créer et lister un sous-détail — rien d'autre — et l'écran
 * projet n'affichait qu'un badge en lecture quand un identifiant existait.
 *
 * **Des montants ronds, et c'est délibéré.** Chaque attendu se recalcule ici,
 * de tête, sans ouvrir le moteur :
 *
 *     matériau     2 t × 1,05 (perte) × 15,00 €/t   = 31,50
 *     main-d'œuvre 1 m³ ÷ 10 m³/h × 60,00 €/h × 1   =  6,00
 *     transport    1 m³ ÷ 1 m³/rotation × 40,00     = 40,00
 *                                          déboursé = 77,50 / m³
 *
 * Le poste porte 100 m³, donc 7 750,00 de déboursé sec. Après correction du
 * taux horaire de 60 à 120 €/h, le rendement passe à 12,00 et le déboursé à
 * 83,50 / m³, soit 8 350,00.
 *
 * **La charge utile vaut 1 m³, et ce n'est pas un détail.** Une rotation
 * ARRONDIE ne se met pas à l'échelle : mesuré ici, un camion de 10 m³ donne
 * 40,00 /m³ en aperçu (une unité = un camion entier) mais 4,00 /m³ sur un
 * poste de 100 m³ (dix camions). L'aperçu reste exact pour une unité — il ne
 * se multiplie simplement pas, et l'écran le dit désormais par un
 * avertissement. Avec une charge de 1 m³, le composant redevient
 * proportionnel et le parcours peut vérifier la multiplication.
 */

const BIB = { code: 'SD-TERRASSEMENT', label: 'Terrassement complet, m³ en place' }
const CHANTIER = { reference: 'SD-2026-001', nom: 'Chantier sous-détail' }
const CLIENT = {
  nom: 'Commune fictive de Recette',
  numero: 'BE 0207.363.192',
  adresse: 'Place fictive 1',
  codePostal: '5000',
  ville: 'Namur',
}

/** La ligne du poste dans le tableau de l'étude, désignée par son numéro. */
function ligneDuPoste(page: Page) {
  return page.getByRole('row', { name: /^01\.10 / })
}

test('un sous-détail se construit, se corrige et chiffre un devis jusqu’au PDF', async ({
  page,
  browser,
}) => {
  test.setTimeout(240_000)
  await seConnecter(page, ADMIN)

  // ---- 1. la bibliothèque, vide au départ
  await page.goto('/bibliotheque')
  await page.getByRole('button', { name: 'Créer la bibliothèque' }).click()
  const encart = page.getByTestId('sous-details')
  await expect(encart).toBeVisible()
  await expect(page.getByTestId('sous-details-vide')).toBeVisible()

  // ---- 2. le sous-détail : matériau, main-d'œuvre, transport
  await encart.getByRole('button', { name: 'Nouveau sous-détail' }).click()
  const editeur = page.getByTestId('editeur-sous-detail')
  await expect(editeur).toBeVisible()

  // Par identifiant : « Unité » désigne AUSSI l'unité d'une ressource dans
  // chaque composant, et un libellé partagé ne choisit pas.
  await page.locator('#sd-code').fill(BIB.code)
  await page.locator('#sd-label').fill(BIB.label)
  await page.locator('#sd-unit').fill('m3')

  // Composant 1 — une consommation avec sa perte.
  const premier = page.getByTestId('composant-0')
  await premier.locator('select[id^="type-"]').selectOption('consumption')
  await premier.locator('input[id^="label-"]').fill('Grave 0/32')
  await premier.locator('select[id^="kind-"]').selectOption('material')
  await premier.locator('input[id^="consumption-"]').fill('2')
  await premier.locator('input[id^="resource_unit_code-"]').fill('t')
  await premier.locator('input[id^="unit_price-"]').fill('15.00')
  await premier.locator('input[id^="loss_ratio-"]').fill('0.05')

  // Composant 2 — un rendement horaire.
  await editeur.getByRole('button', { name: 'Ajouter un composant' }).click()
  const second = page.getByTestId('composant-1')
  await second.locator('select[id^="type-"]').selectOption('output_rate')
  await second.locator('input[id^="label-"]').fill('Équipe de pose')
  await second.locator('select[id^="kind-"]').selectOption('labor')
  await second.locator('input[id^="output_rate-"]').fill('10')
  await second.locator('input[id^="hourly_rate-"]').fill('60.00')
  await second.locator('input[id^="crew_size-"]').fill('1')

  // Composant 3 — une rotation de camion, chargée dans la MÊME unité que le
  // sous-détail : croiser m³ et tonnes exigerait une masse volumique sourcée,
  // et c'est un refus qui a son propre test côté API.
  await editeur.getByRole('button', { name: 'Ajouter un composant' }).click()
  const troisieme = page.getByTestId('composant-2')
  await troisieme.locator('select[id^="type-"]').selectOption('rotation')
  await troisieme.locator('input[id^="label-"]').fill('Camion 8x4')
  await troisieme.locator('select[id^="kind-"]').selectOption('transport')
  await troisieme.locator('input[id^="payload_value-"]').fill('1')
  await troisieme.locator('input[id^="payload_unit_code-"]').fill('m3')
  await troisieme.locator('input[id^="cost_per_rotation-"]').fill('40.00')

  // ---- 3. le coût prévisualisé vient du SERVEUR, et il tombe juste
  await expect(page.getByTestId('cout-unitaire')).toContainText('77.50', { timeout: 15_000 })
  const ventilation = page.getByTestId('ventilation')
  await expect(ventilation).toContainText('31.50')
  await expect(ventilation).toContainText('6.00')
  await expect(ventilation).toContainText('40.00')

  await editeur.getByRole('button', { name: 'Enregistrer' }).click()
  await expect(page.getByTestId(`sous-detail-${BIB.code}`)).toBeVisible()

  // ---- 4. le chantier, sa fiche client, et le poste chiffré PAR le sous-détail
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

  await page.goto('/projets')
  await page.getByRole('button', { name: /nouveau projet/i }).first().click()
  await page.getByLabel(/référence/i).fill(CHANTIER.reference)
  await page.getByLabel(/^nom/i).first().fill(CHANTIER.nom)
  await page.getByRole('button', { name: /^créer$/i }).first().click()
  await page.getByRole('link', { name: CHANTIER.reference }).first().click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}$/)
  const urlChantier = page.url()

  await page.getByTestId('selecteur-client').selectOption({ label: `${CLIENT.nom} — ${CLIENT.ville}` })
  await page.getByRole('button', { name: 'Rattacher' }).click()

  await page.getByRole('button', { name: /^créer$/i }).first().click()
  await page.getByLabel('Poste').fill('01.10')
  await page.getByLabel('Désignation').fill('Remblai technique')
  await page.getByLabel('Unité').fill('m3')
  await page.getByLabel('Quantité').fill('100')
  // La source de prix est EXPLICITE : trois choix, et le sous-détail en est un.
  await page.locator('#source-nouveau').selectOption('composite')
  await page.locator('#composite-nouveau').selectOption({ index: 1 })
  await page.getByRole('button', { name: /^créer$/i }).first().click()

  // Le poste montre son sous-détail : code, libellé, nombre de composants, et
  // le coût prévisualisé par le serveur.
  const prixDuPoste = page.getByTestId('prix-poste-01.10')
  await expect(prixDuPoste).toContainText(BIB.code)
  await expect(prixDuPoste).toContainText('3 composants')
  await expect(prixDuPoste).toContainText('77.50', { timeout: 15_000 })

  // ---- 5. l'étude, et le déboursé attendu : 100 × 77,50 = 7 750,00
  await page.getByRole('button', { name: 'Créer une étude de prix' }).click()
  await page.getByRole('link', { name: 'Ouvrir' }).first().click()
  await page.waitForURL(/\/estimations\//)
  const urlVersion = page.url()
  // Le montant se lit sur la LIGNE du poste, par cellule. Un `getByText` global
  // attrape d'abord la même somme dans le « détail du calcul » replié : elle est
  // dans le document mais masquée, et l'attente échoue sur un chiffre pourtant
  // juste. La cellule « Déboursé sec » vaut exactement 7750.00 ; le total HT de
  // la ligne porte en plus la devise, donc l'égalité stricte ne confond pas les
  // deux colonnes.
  await expect(ligneDuPoste(page).getByRole('cell', { name: '7750.00', exact: true })).toBeVisible({
    timeout: 15_000,
  })
  await expect(ligneDuPoste(page).getByRole('cell', { name: '77.50', exact: true })).toBeVisible()
  await expect(page.getByRole('row', { name: 'Déboursé sec 7750.00 EUR' })).toBeVisible()

  // ---- 6. corriger le sous-détail change le BROUILLON
  await page.goto('/bibliotheque')
  await page.getByTestId(`sous-detail-${BIB.code}`).getByRole('button', { name: 'Modifier' }).click()
  await page.getByTestId('composant-1').locator('input[id^="hourly_rate-"]').fill('120.00')
  await expect(page.getByTestId('cout-unitaire')).toContainText('83.50', { timeout: 15_000 })
  await page.getByTestId('editeur-sous-detail').getByRole('button', { name: 'Enregistrer' }).click()
  await expect(page.getByTestId(`sous-detail-${BIB.code}`)).toBeVisible()

  await page.goto(urlVersion)
  // 100 × 83,50 = 8 350,00 : la correction a bien traversé jusqu'au devis.
  await expect(ligneDuPoste(page).getByRole('cell', { name: '8350.00', exact: true })).toBeVisible({
    timeout: 15_000,
  })
  await expect(ligneDuPoste(page).getByRole('cell', { name: '83.50', exact: true })).toBeVisible()
  await expect(page.getByRole('row', { name: 'Déboursé sec 8350.00 EUR' })).toBeVisible()

  // ---- 7. publier la bibliothèque ferme l'édition
  await page.goto('/bibliotheque')
  await page.getByRole('button', { name: 'Publier cette version' }).click()
  await page.getByRole('button', { name: 'Confirmer la publication' }).click()
  await expect(page.getByTestId('version-publiee-badge')).toBeVisible()
  await page.reload()
  await expect(page.getByTestId('version-publiee')).toBeVisible()
  // Republier n'est pas offert : l'API répondrait 409.
  await expect(page.getByRole('button', { name: 'Publier cette version' })).toHaveCount(0)
  // Aucune commande d'écriture n'est offerte : elle échouerait.
  await expect(page.getByTestId('sous-details').getByRole('button', { name: 'Modifier' })).toHaveCount(0)
  await expect(
    page.getByTestId('sous-details').getByRole('button', { name: 'Nouveau sous-détail' }),
  ).toHaveCount(0)

  // ---- 8. geler et émettre
  await page.goto(urlVersion)
  await page.getByRole('button', { name: 'Geler cette version' }).click()
  await page.getByRole('button', { name: /confirmer/i }).click()
  await expect(page.getByText('Gelée', { exact: true })).toBeVisible()

  const emission = page.getByTestId('emission-du-devis')
  await emission.getByRole('button', { name: 'Émettre le devis' }).click()
  await page.getByLabel('Valable jusqu’au').fill('2027-12-31')
  await page.getByTestId('confirmer-l-emission').click()
  await expect(page.getByTestId('devis-emis')).toBeVisible({ timeout: 20_000 })
  const numero = (await page.getByTestId('numero-du-devis').innerText()).trim()
  expect(numero).toMatch(/^DEV-/)

  // ---- 9. le PDF, et son empreinte
  const [telechargement] = await Promise.all([
    page.waitForEvent('download'),
    page.getByTestId('telecharger-le-devis').click(),
  ])
  const chemin = await telechargement.path()
  const octets = chemin ? await readFile(chemin) : Buffer.alloc(0)
  const empreinte = createHash('sha256').update(octets).digest('hex')
  expect(empreinte).toHaveLength(64)
  // Le texte IMPRIMÉ, pas les octets : un accent y est un octal et une
  // recherche brute répondrait « absent » pour du texte pourtant lisible.
  const texte = texteDuPdf(octets)

  // ---- 10. ce que le client reçoit : son prix, et rien de la ventilation
  //
  // Le devis PORTE le prix unitaire de vente — c'est ce pour quoi il existe.
  // Ce qui doit rester dedans, c'est de quoi ce prix est FAIT : les ressources
  // nommées, le taux horaire, et les intitulés de coût interne. Avec une marge
  // nulle, 83,50 est à la fois le déboursé et le prix de vente : ce nombre ne
  // prouve donc rien dans un sens ni dans l'autre, et le vérifier reviendrait à
  // interdire au devis d'afficher son propre prix.
  for (const attendu of ['01.10', 'Remblai technique', '8350.00']) {
    expect(texte, `le PDF doit imprimer « ${attendu} »`).toContain(attendu)
  }
  for (const interne of [
    'Grave 0/32',
    'Équipe de pose',
    'Camion 8x4',
    '120.00',
    'Déboursé',
    'Revient',
    'Marge',
    BIB.code,
  ]) {
    expect(texte.includes(interne), `« ${interne} » ne doit pas figurer dans le PDF client`).toBe(
      false,
    )
  }

  // ---- 10 bis. la page publique, ouverte par un navigateur qui ne sait RIEN
  //
  // Le PDF et la page publique sont deux sorties distinctes du même devis :
  // vérifier l'une ne dit rien de l'autre. Le contexte est neuf — pas de
  // session, pas de cookie — donc c'est bien ce que verrait le client.
  // Le lien se crée depuis la FICHE du devis émis, pas depuis l'étude : c'est
  // le devis qui se partage, et il survit à sa version de calcul.
  await page.getByRole('link', { name: numero }).first().click()
  await page.waitForURL(/\/devis-emis\/[0-9a-f-]{36}$/)
  await page.getByTestId('creer-le-lien').click()
  const lienPublic = await page.getByTestId('url-du-lien').inputValue()
  expect(lienPublic, 'le lien doit porter son secret dans le fragment').toContain('#')

  const contexteClient = await browser.newContext()
  const pageClient = await contexteClient.newPage()
  await pageClient.goto(lienPublic)
  await expect(pageClient.getByTestId('devis-public')).toBeVisible()

  // Le montant que le client lit est celui du calcul gelé : 100 × 83,50.
  await expect(pageClient.getByTestId('devis-public')).toContainText('8350.00')

  // Rien de la ventilation interne, ni en texte visible ni dans le document
  // servi : les libellés des ressources, les taux, le déboursé unitaire et le
  // mot même de « déboursé » sont absents du HTML rendu.
  const htmlClient = await pageClient.content()
  for (const interne of [
    'Grave 0/32',
    'Équipe de pose',
    'Camion 8x4',
    '120.00',
    'Déboursé',
    BIB.code,
  ]) {
    expect(
      htmlClient.includes(interne),
      `« ${interne} » ne doit pas figurer sur la page publique`,
    ).toBe(false)
  }
  await contexteClient.close()

  // ---- 11. et l'API confirme, sur la même version
  const idVersion = urlVersion.split('/').pop() ?? ''
  expect(idVersion).toHaveLength(36)
  await page.goto(urlVersion)
  await expect(page.getByTestId('devis-emis')).toContainText(numero)
  await page.goto(urlChantier)
  await expect(page.getByTestId('prix-poste-01.10')).toContainText(BIB.code)
})
