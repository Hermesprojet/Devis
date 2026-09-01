import { readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import { expect, test, type Page } from '@playwright/test'

import { ADMIN, CONSTAT, PILE_EXTERNE } from './banc'
import { seConnecter } from './parcours'

/**
 * Une organisation vide produit son premier devis, sans seed ni SQL.
 *
 * Ce que ce parcours prouve : qu'une entreprise qui vient d'installer Metreo
 * peut, depuis son navigateur et rien d'autre, configurer sa fiscalité, ses
 * prix, son chantier, et remettre un devis chiffré — puis le retrouver après
 * une déconnexion et après un redémarrage des serveurs.
 *
 * Ce qu'il interdit, et vérifie qu'il s'interdit : semer des données de
 * démonstration, écrire en base, appeler un point d'entrée métier depuis le
 * test. Ces trois raccourcis rendraient le vert mensonger — c'est exactement
 * ainsi que la répétition de préproduction avait fini par « prouver » un
 * parcours que personne ne pouvait suivre.
 */

const MONTANTS = {
  quantite: '1250.5',
  prixUnitaire: '18.4567',
  /** 1250,5 × 18,4567 arrondi au centime. */
  totalHT: '23080.10',
  tva: '4846.82',
  ttc: '27926.92',
}

// --------------------------------------------------------------------------
// Les garde-fous
// --------------------------------------------------------------------------

test('aucun scénario de cette suite n’emprunte de raccourci', () => {
  /**
   * Le contrôle porte sur le TEXTE des fichiers, pas sur une intention.
   *
   * Un parcours qui appellerait l'API directement, ou qui écrirait en base,
   * passerait au vert sans rien prouver de l'interface. Le seul moyen de
   * s'en assurer durablement est de refuser ces tournures ici, où quiconque
   * les ajouterait verrait le test tomber.
   *
   * TOUT le dossier est lu, scénarios ET fichiers d'aide, et non ce seul
   * fichier : la garantie doit valoir pour ce qu'on ajoutera, sans quoi il
   * suffirait d'écrire le raccourci dans un fichier voisin — ou, depuis que
   * les gestes communs vivent dans `parcours.ts`, dans un fichier qui n'est
   * même pas un scénario.
   *
   * `banc.ts` est la seule exception, et par son nom : il EST le banc. Il lui
   * revient de migrer la base, de l'amorcer et de démarrer les serveurs, ce
   * que la liste ci-dessous interdit à juste titre partout ailleurs.
   */
  const source = readdirSync(__dirname)
    .filter((nom) => nom.endsWith('.ts') && nom !== 'banc.ts')
    .map((nom) => readFileSync(join(__dirname, nom), 'utf8'))
    .join('\n')
  const interdits: ReadonlyArray<{ motif: RegExp; pourquoi: string }> = [
    { motif: /\brequest\.(post|patch|put|delete)\b/, pourquoi: 'appel direct à un point d’entrée métier' },
    { motif: /\bcontext\.request\b/, pourquoi: 'appel direct via le contexte Playwright' },
    { motif: /\bmetreo_api\.seed\b/, pourquoi: 'jeu de démonstration' },
    { motif: /\bsqlite3\b/, pourquoi: 'écriture en base' },
    { motif: /\bexecSync\b|\bexecFileSync\b/, pourquoi: 'exécution d’un script métier' },
    { motif: /\bINSERT\s+INTO\b/i, pourquoi: 'insertion SQL' },
  ]
  const trouves = interdits
    .filter(({ motif }) => motif.test(source.replace(/^.*motif:.*$/gm, '')))
    .map(({ pourquoi }) => pourquoi)
  expect(trouves, 'Le parcours doit tout créer au navigateur').toEqual([])
})

// --------------------------------------------------------------------------
// Le parcours
// --------------------------------------------------------------------------

/** Les montants tels que l'écran les affiche, lus dans le tableau des totaux. */
async function totaux(page: Page): Promise<Record<string, string>> {
  // Le calcul arrive après le rendu : lire tout de suite rendrait un objet
  // vide, et l'échec accuserait les montants au lieu de l'attente.
  await page.locator('table.totals tr').first().waitFor()
  const lignes = await page.locator('table.totals tr').all()
  const lus: Record<string, string> = {}
  for (const ligne of lignes) {
    const [intitule, montant] = await ligne.locator('td').allInnerTexts()
    if (intitule !== undefined && montant !== undefined) {
      lus[intitule.trim()] = montant.replace(/\s*EUR$/, '').trim()
    }
  }
  return lus
}

test('une organisation vide produit son premier devis sans seed', async ({ page }) => {
  // ---- 0. la connexion du premier utilisateur, par le fournisseur d'identité
  await seConnecter(page, ADMIN)
  await expect(page.getByText("Administrateur de l'entreprise")).toBeVisible()

  // ---- 1. l'organisation est vraiment vide, et le dit
  const miseEnRoute = page.getByTestId('mise-en-route')
  await expect(miseEnRoute).toBeVisible()
  await expect(miseEnRoute).toContainText('Configurer un taux de taxe')
  await expect(miseEnRoute).toContainText('Prochaine action')
  // Aucune donnée d'un scénario précédent : la liste des projets ne mène
  // nulle part, parce qu'elle ne contient rien.
  await expect(page.getByRole('link', { name: /^[A-Z0-9-]+$/ })).toHaveCount(0)

  // ---- 2. la fiscalité, choisie par l'administrateur et par personne d'autre
  await page.goto('/parametres')
  await expect(page.getByText(/Aucun taux configuré/)).toBeVisible()
  await page.getByRole('button', { name: 'Ajouter un taux de taxe' }).click()
  await page.getByLabel('Code', { exact: true }).fill('TVA-21')
  await page.getByLabel(/Libellé imprimé/).fill('TVA 21 %')
  await page.getByLabel(/Taux \(en pourcentage\)/).fill('21')
  await page.getByLabel(/Source/).fill("Choisi par l'administrateur de l'entreprise")
  await page.getByRole('button', { name: 'Enregistrer le taux' }).click()
  await expect(page.getByRole('cell', { name: 'TVA-21' })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'en vigueur' })).toBeVisible()

  // ---- 3. la bibliothèque et son premier prix, saisis à la main
  await page.goto('/bibliotheque')
  await page.getByRole('button', { name: 'Créer la bibliothèque' }).click()
  await page.getByRole('button', { name: 'Ajouter un prix' }).click()
  await page.getByLabel('Code', { exact: true }).fill('TER-001')
  await page.getByLabel('Désignation').fill('Déblai en terrain meuble')
  await page.getByLabel('Unité').selectOption('m3')
  await page.getByLabel(/Prix unitaire HT/).fill(MONTANTS.prixUnitaire)
  await page.getByRole('button', { name: 'Enregistrer le prix' }).click()
  await expect(page.getByText('TER-001').first()).toBeVisible()

  // ---- 4. le chantier
  await page.goto('/projets')
  await page.getByRole('button', { name: /nouveau projet/i }).first().click()
  await page.getByLabel(/référence/i).fill('PREM-001')
  await page.getByLabel(/^nom/i).first().fill('Premier chantier')
  await page.getByRole('button', { name: /^créer$/i }).first().click()
  await page.getByRole('link', { name: 'PREM-001' }).first().click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}$/)

  // ---- 5. le bordereau et une ligne QUI PORTE UN PRIX
  await page.getByRole('button', { name: /^créer$/i }).first().click()
  await expect(page.getByLabel('Poste')).toBeVisible()
  await page.getByLabel('Poste').fill('01.10')
  await page.getByLabel('Désignation').fill('Déblai en terrain meuble')
  await page.getByLabel('Unité').fill('m3')
  await page.getByLabel('Quantité').fill(MONTANTS.quantite)
  // La source de prix est explicite depuis que les postes peuvent aussi être
  // chiffrés par un sous-détail : choisir « bibliothèque », puis le prix.
  await page.locator('#source-nouveau').selectOption('library')
  await page.locator('#prix-nouveau').selectOption({ index: 1 })
  await page.getByRole('button', { name: /^créer$/i }).first().click()
  await expect(page.getByRole('cell', { name: '01.10' })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'bibliothèque' })).toBeVisible()

  // ---- 6. l'étude de prix
  await page.getByRole('button', { name: 'Créer une étude de prix' }).click()
  await page.getByRole('link', { name: 'Ouvrir' }).first().click()
  await page.waitForURL(/\/estimations\//)

  // ---- 7. le calcul : non nul, cohérent, et arithmétiquement vérifiable
  const lus = await totaux(page)
  expect(lus['Total HT']).toBe(MONTANTS.totalHT)
  expect(lus['TVA 21 %']).toBe(MONTANTS.tva)
  expect(lus['Total TTC']).toBe(MONTANTS.ttc)
  expect(Number(lus['Total HT'])).toBeGreaterThan(0)
  expect(Number(lus['TVA 21 %'])).toBeGreaterThan(0)
  // TTC = HT + TVA, sur les montants IMPRIMÉS et non sur des flottants
  // intermédiaires : c'est l'identité que le client peut refaire à la main.
  expect((Number(lus['Total HT']) + Number(lus['TVA 21 %'])).toFixed(2)).toBe(lus['Total TTC'])

  // La somme des lignes affichées vaut le HT affiché.
  //
  // `table:not(.totals)` et non `tbody tr` : le tableau des totaux a lui aussi
  // des cellules numériques, et les additionner à celles des postes donnait
  // une somme qui ne voulait rien dire — mesuré, elle valait cinq fois le HT.
  const postes = await page.locator('table:not(.totals) tbody tr').all()
  let sommeLignes = 0
  for (const poste of postes) {
    const cellules = await poste.locator('td').allInnerTexts()
    const derniere = cellules[cellules.length - 1] ?? ''
    const montant = Number(derniere.replace(/\s*EUR$/, '').trim())
    if (!Number.isNaN(montant)) sommeLignes += montant
  }
  expect(sommeLignes.toFixed(2)).toBe(lus['Total HT'])

  // ---- 8. le gel
  await page.getByRole('button', { name: 'Geler cette version' }).click()
  await page.getByRole('button', { name: /confirmer/i }).click()
  await expect(page.getByText('Gelée', { exact: true })).toBeVisible()
  const urlDevis = page.url()

  // ---- 9. le devis et le CSV, tels que le client et l'entreprise les reçoivent
  const [apercu] = await Promise.all([
    page.waitForEvent('popup'),
    page.getByRole('button', { name: 'Aperçu du devis' }).click(),
  ])
  await apercu.waitForLoadState('domcontentloaded')
  const texteDevis = await apercu.locator('body').innerText()
  for (const attendu of [MONTANTS.totalHT, MONTANTS.tva, MONTANTS.ttc, 'TVA 21 %', '01.10']) {
    expect(texteDevis, `le devis doit porter ${attendu}`).toContain(attendu)
  }
  await apercu.close()

  const [csv] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: 'Export CSV' }).click(),
  ])
  const contenu = readFileSync(await csv.path(), 'utf8')
  expect(contenu).toContain('01.10')
  expect(contenu).toContain(MONTANTS.totalHT)

  // ---- 10. le devis survit à une déconnexion
  await page.getByRole('button', { name: 'Se déconnecter' }).click()
  await page.waitForURL(/\/$/)
  await seConnecter(page, ADMIN)
  await page.goto(urlDevis)
  await expect(page.getByText('Gelée', { exact: true })).toBeVisible()
  expect(await totaux(page)).toMatchObject({
    'Total HT': MONTANTS.totalHT,
    'Total TTC': MONTANTS.ttc,
  })

  // ---- 11. et l'appelant sait où le retrouver
  //
  // La répétition de préproduction rejoue ses contrôles de redémarrage, de
  // sauvegarde et de restauration sur CE devis — celui qu'une personne a
  // fabriqué au navigateur — et non sur un devis qu'un script aurait posé.
  if (CONSTAT) {
    const [, estimation, version] = /\/estimations\/([0-9a-f-]{36})\/([0-9a-f-]{36})/.exec(
      urlDevis,
    ) ?? []
    expect(estimation, "l'URL du devis doit porter ses identifiants").toBeTruthy()
    writeFileSync(
      CONSTAT,
      JSON.stringify({
        estimate_id: estimation,
        version_id: version,
        total_ht: lus['Total HT'],
        total_ttc: lus['Total TTC'],
      }),
    )
  }

  // La mise en route a disparu : elle guide, elle ne s'installe pas.
  await page.goto('/projets')
  await expect(page.getByTestId('mise-en-route')).toHaveCount(0)
})
