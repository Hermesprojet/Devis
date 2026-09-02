import { expect, test, type Locator, type Page } from '@playwright/test'

import { ADMIN } from './banc'
import { seConnecter, seDeconnecter } from './parcours'

/**
 * Trois hypothèses de chiffrage, éprouvées au navigateur sur un vrai devis.
 *
 * **Le bordereau, et les chiffres attendus, se recalculent de tête.** Un poste
 * de 100 m³, un sous-détail à quatre composants — un de chaque type, ce qui est
 * le point : chaque axe touche des composants différents, et un bordereau
 * mono-composant ne le montrerait pas.
 *
 *     matériau     100 × 0,35 t/m³ × 1,05 (perte) × 18,00 €/t =   661,50
 *     main-d'œuvre 100 ÷ 12 m³/h = 8,333… h × 2 × 45,00 €/h   =   750,00
 *     transport    100 ÷ 8 m³ = 12,5 → 13 rotations
 *                  × (85,00 + 30 km × 1,20 €/km = 121,00)     = 1 573,00
 *     forfait                                                  =   450,00
 *                                              déboursé sec    = 3 434,50
 *
 * **Ce que chaque hypothèse doit produire, posé à la main :**
 *
 *     prix +10 %       727,65 + 825,00 + 1 730,30 + 450,00 = 3 732,95 (+298,45)
 *     prix -10 %       595,35 + 675,00 + 1 415,70 + 450,00 = 3 136,05 (-298,45)
 *     prix +10 % matériaux seuls                             3 500,65  (+66,15)
 *     productivité +10 %  750,00 ÷ 1,1 = 681,82              3 366,32  (-68,18)
 *     distance +10 %   13 × (85 + 33 × 1,20 = 124,60) = 1 619,80
 *                                                            3 481,30  (+46,80)
 *
 * Trois de ces cinq nombres démontrent à eux seuls que le facteur agit sur les
 * ENTRÉES et non sur le total :
 *
 *   - 3 434,50 × 1,10 vaudrait 3 777,95. L'écart avec 3 732,95 est de 45,00,
 *     soit exactement 10 % du forfait — qu'aucune variation de prix ne touche.
 *   - 1 573,00 × 1,10 vaudrait 1 730,30. Le transport n'en rend que 1 619,80 :
 *     la distance passe par un nombre ENTIER de rotations, et 13 rotations
 *     restent 13 rotations.
 *   - +10 % de productivité fait BAISSER le coût. C'est le seul axe dont le
 *     signe s'inverse, et l'écran l'écrit à côté du champ.
 *
 * **Ce que ce parcours ne prouve pas, et pourquoi.** La chaîne de marge n'est
 * pas modifiable depuis l'interface — les taux de l'organisation y sont en
 * lecture seule. Ce parcours vérifie donc l'absence des étapes commerciales
 * pour un métreur ; leur présence pour qui porte `margin:read` est éprouvée
 * côté API (`test_un_administrateur_avec_margin_read_garde_ses_etapes`), avec
 * un taux sentinelle qu'aucun écran ne pourrait poser ici.
 */

const SD = { code: 'SCN-MIXTE', label: 'Déblai, évacuation et installation' }
const CHANTIER = { reference: 'SCN-2026-001', nom: 'Chantier des scénarios' }
const METREUR = 'marc.metreur@neuve.example'
const LECTEUR = 'lea.lectrice@neuve.example'

const REFERENCE = '3434.50'

type Nom = 'bas' | 'probable' | 'haut'

function colonne(page: Page, nom: Nom): Locator {
  return page.getByTestId(`scenario-${nom}`)
}

/** Saisit un pourcentage HUMAIN dans une colonne : « 10 » vaut « +10 % ». */
async function poser(
  page: Page,
  nom: Nom,
  valeurs: { prix?: string; productivite?: string; distance?: string },
): Promise<void> {
  for (const [axe, valeur] of Object.entries(valeurs)) {
    await page.locator(`#scenario-${nom}-${axe}`).fill(valeur)
  }
}

async function calculer(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Calculer les trois scénarios' }).click()
}

/** Le déboursé sec d'une colonne, attendu jusqu'à ce que le serveur réponde. */
async function attendreDebourse(page: Page, nom: Nom, montant: string): Promise<void> {
  await expect(page.getByTestId(`debourse-${nom}`)).toHaveText(`${montant} EUR`, {
    timeout: 20_000,
  })
}

test('trois scénarios de chiffrage, calculés par le moteur et jamais par le navigateur', async ({
  page,
}) => {
  test.setTimeout(240_000)
  await seConnecter(page, ADMIN)

  // ---- 1. un sous-détail à quatre composants : un de chaque type
  await page.goto('/bibliotheque')
  const creation = page.getByRole('button', { name: 'Créer la bibliothèque' })
  const nouvelleVersion = page.getByRole('button', { name: 'Nouvelle version' })
  // `count()` ne PATIENTE PAS : sondé avant que l'écran n'ait fini de charger,
  // il rend zéro pour les deux commandes et le parcours attend ensuite pendant
  // quatre minutes une commande qui était pourtant là. Mesuré.
  await expect(creation.or(nouvelleVersion).first()).toBeVisible({ timeout: 30_000 })
  if (await creation.count()) {
    await creation.click()
  } else {
    page.once('dialog', (dialogue) => void dialogue.accept('Scénarios'))
    await nouvelleVersion.click()
  }
  const encart = page.getByTestId('sous-details')
  await expect(encart).toBeVisible()

  await encart.getByRole('button', { name: 'Nouveau sous-détail' }).click()
  const editeur = page.getByTestId('editeur-sous-detail')
  await expect(editeur).toBeVisible()
  await page.locator('#sd-code').fill(SD.code)
  await page.locator('#sd-label').fill(SD.label)
  await page.locator('#sd-unit').fill('m3')

  const materiau = page.getByTestId('composant-0')
  await materiau.locator('select[id^="type-"]').selectOption('consumption')
  await materiau.locator('input[id^="label-"]').fill('Grave 0/32')
  await materiau.locator('select[id^="kind-"]').selectOption('material')
  await materiau.locator('input[id^="consumption-"]').fill('0.35')
  await materiau.locator('input[id^="resource_unit_code-"]').fill('t')
  await materiau.locator('input[id^="unit_price-"]').fill('18.00')
  await materiau.locator('input[id^="loss_ratio-"]').fill('0.05')

  await editeur.getByRole('button', { name: 'Ajouter un composant' }).click()
  const equipe = page.getByTestId('composant-1')
  await equipe.locator('select[id^="type-"]').selectOption('output_rate')
  await equipe.locator('input[id^="label-"]').fill('Équipe de pose')
  await equipe.locator('select[id^="kind-"]').selectOption('labor')
  await equipe.locator('input[id^="output_rate-"]').fill('12')
  await equipe.locator('input[id^="hourly_rate-"]').fill('45.00')
  await equipe.locator('input[id^="crew_size-"]').fill('2')

  // Le transport porte une distance ET son tarif au km : c'est ce couple que
  // l'axe « distance » modifie, avant que le moteur n'en déduise le coût d'une
  // rotation puis ne le multiplie par un nombre entier de rotations.
  await editeur.getByRole('button', { name: 'Ajouter un composant' }).click()
  const camion = page.getByTestId('composant-2')
  await camion.locator('select[id^="type-"]').selectOption('rotation')
  await camion.locator('input[id^="label-"]').fill('Camion 8 m³')
  await camion.locator('select[id^="kind-"]').selectOption('transport')
  await camion.locator('input[id^="payload_value-"]').fill('8')
  await camion.locator('input[id^="payload_unit_code-"]').fill('m3')
  await camion.locator('input[id^="cost_per_rotation-"]').fill('85.00')
  await camion.locator('input[id^="distance_km-"]').fill('30')
  await camion.locator('input[id^="rate_per_km-"]').fill('1.20')

  // Le forfait : un montant convenu, sans prix unitaire. Aucune hypothèse de
  // prix ne doit le toucher, et c'est ce que l'étape 4 mesure.
  await editeur.getByRole('button', { name: 'Ajouter un composant' }).click()
  const installation = page.getByTestId('composant-3')
  await installation.locator('select[id^="type-"]').selectOption('lump_sum')
  await installation.locator('input[id^="label-"]').fill('Installation de chantier')
  await installation.locator('select[id^="kind-"]').selectOption('other')
  await installation.locator('input[id^="lump_sum_amount-"]').fill('450.00')

  await editeur.getByRole('button', { name: 'Enregistrer' }).click()
  await expect(page.getByTestId(`sous-detail-${SD.code}`)).toBeVisible()

  // ---- 2. le chantier, son poste de 100 m³, et l'étude
  await page.goto('/projets')
  await page.getByRole('button', { name: /nouveau projet/i }).first().click()
  await page.getByLabel(/référence/i).fill(CHANTIER.reference)
  await page.getByLabel(/^nom/i).first().fill(CHANTIER.nom)
  await page.getByRole('button', { name: /^créer$/i }).first().click()
  await page.getByRole('link', { name: CHANTIER.reference }).first().click()
  await page.waitForURL(/\/projets\/[0-9a-f-]{36}$/)

  await page.getByRole('button', { name: /^créer$/i }).first().click()
  await page.getByLabel('Poste').fill('01.10')
  await page.getByLabel('Désignation').fill(SD.label)
  await page.getByLabel('Unité').fill('m3')
  await page.getByLabel('Quantité').fill('100')
  await page.locator('#source-nouveau').selectOption('composite')
  await page
    .locator('#composite-nouveau')
    .selectOption({ label: `${SD.code} — ${SD.label} (4 composants, /m3)` })
  await page.getByRole('button', { name: /^créer$/i }).first().click()

  await page.getByRole('button', { name: 'Créer une étude de prix' }).click()
  await page.getByRole('link', { name: 'Ouvrir' }).first().click()
  await page.waitForURL(/\/estimations\//)
  const urlVersion = page.url()

  // Le chiffrage de RÉFÉRENCE, celui auquel tout scénario doit se comparer.
  await expect(page.getByRole('row', { name: `Déboursé sec ${REFERENCE} EUR` })).toBeVisible({
    timeout: 20_000,
  })

  // ---- 3. le panneau existe, et il n'a encore rien calculé
  //
  // Rien n'est demandé au serveur tant que l'utilisateur ne l'a pas demandé :
  // ouvrir un chiffrage ne doit pas déclencher trois calculs qu'on n'a pas
  // réclamés.
  const panneau = page.getByRole('region', { name: 'Scénarios de chiffrage' })
  await expect(panneau).toBeVisible()
  await expect(page.getByTestId('debourse-probable')).toHaveCount(0)
  // Le sens inversé de la productivité est ÉCRIT, pas supposé connu.
  await expect(panneau).toContainText('un coût qui BAISSE')
  // Et les libellés ne promettent rien.
  await expect(panneau).toContainText('pas une garantie')

  // ---- 4. trois scénarios neutres reproduisent EXACTEMENT la référence
  //
  // Par construction et non par coïncidence : sans hypothèse, aucune entrée
  // n'est modifiée et le moteur refait littéralement le même calcul.
  await calculer(page)
  for (const nom of ['bas', 'probable', 'haut'] as const) {
    await attendreDebourse(page, nom, REFERENCE)
  }
  await expect(page.getByTestId('ecart-bas')).toContainText('0.00 EUR')
  await expect(page.getByTestId('ecart-bas')).toContainText('(0 %)')

  // ---- 5. une variation de prix touche les ENTRÉES, et épargne le forfait
  await poser(page, 'bas', { prix: '-10' })
  await poser(page, 'haut', { prix: '10' })
  await calculer(page)
  await attendreDebourse(page, 'bas', '3136.05')
  await attendreDebourse(page, 'probable', REFERENCE)
  await attendreDebourse(page, 'haut', '3732.95')

  // 3 434,50 × 1,10 vaudrait 3 777,95. La différence, 45,00, est exactement
  // 10 % du forfait : la preuve que le total n'a pas été multiplié.
  await expect(colonne(page, 'haut')).not.toContainText('3777.95')
  await expect(page.getByTestId('ecart-haut')).toContainText('298.45 EUR')
  await expect(page.getByTestId('ecart-bas')).toContainText('-298.45 EUR')
  // Le pourcentage vient du serveur : 298,45 ÷ 3 434,50 = 8,69 %.
  await expect(page.getByTestId('ecart-haut')).toContainText('(8.69 %)')

  // Les hypothèses affichées sont celles que le SERVEUR a appliquées, relues
  // de sa réponse, et non celles que l'écran croit avoir envoyées.
  await expect(colonne(page, 'haut')).toContainText('Prix des ressources 10 %')

  // ---- 6. la même variation, limitée à une nature de ressource
  //
  // Les natures ET leurs libellés viennent du serveur : l'interface n'en tient
  // pas de seconde liste, qui divergerait à la première nature ajoutée.
  await colonne(page, 'haut').getByText('Limiter à certaines natures').click()
  const categories = page.getByTestId('categories-haut')
  await expect(categories).toContainText('Matériaux')
  await expect(categories).toContainText("Main-d'œuvre")
  await expect(categories).toContainText('Transport')
  await colonne(page, 'haut').getByRole('checkbox', { name: 'Matériaux' }).check()
  await calculer(page)
  // 661,50 → 727,65 : seuls les matériaux ont bougé, soit +66,15.
  await attendreDebourse(page, 'haut', '3500.65')
  await expect(page.getByTestId('ecart-haut')).toContainText('66.15 EUR')

  // ---- 7. la productivité, dont le sens s'inverse
  await colonne(page, 'haut').getByRole('checkbox', { name: 'Matériaux' }).uncheck()
  await poser(page, 'haut', { prix: '0', productivite: '10' })
  await calculer(page)
  // 750,00 ÷ 1,1 = 681,82 : produire plus par heure COÛTE MOINS.
  await attendreDebourse(page, 'haut', '3366.32')
  await expect(page.getByTestId('ecart-haut')).toContainText('-68.18 EUR')

  // ---- 8. la distance passe par l'arrondi des rotations
  await poser(page, 'haut', { productivite: '0', distance: '10' })
  await calculer(page)
  // 13 × (85 + 33 × 1,20) = 1 619,80, et NON 1 573,00 × 1,10 = 1 730,30 :
  // 13 rotations restent 13 rotations.
  await attendreDebourse(page, 'haut', '3481.30')
  await expect(page.getByTestId('ecart-haut')).toContainText('46.80 EUR')
  await expect(colonne(page, 'haut')).not.toContainText('3591.80')

  // ---- 9. la virgule est acceptée, comme le point
  //
  // Le clavier belge produit une virgule. La refuser obligerait l'utilisateur
  // à saisir dans une notation qui n'est pas la sienne.
  await poser(page, 'haut', { distance: '10,0' })
  await calculer(page)
  await attendreDebourse(page, 'haut', '3481.30')

  // ---- 10. une saisie illisible reste CONFINÉE à sa colonne
  await poser(page, 'bas', { prix: 'abc' })
  await calculer(page)
  await expect(colonne(page, 'bas').getByRole('alert')).toContainText("n'est pas un pourcentage")
  await expect(colonne(page, 'haut').getByRole('alert')).toHaveCount(0)
  // Et les deux voisins gardent leur résultat : une faute de frappe sur un
  // tiers de l'écran ne fait pas perdre une comparaison entière.
  await expect(page.getByTestId('debourse-haut')).toHaveText('3481.30 EUR')
  await expect(page.getByTestId('debourse-probable')).toHaveText(`${REFERENCE} EUR`)

  // ---- 11. des libellés qui mentent sont SIGNALÉS, jamais réordonnés
  await poser(page, 'bas', { prix: '10' })
  await poser(page, 'haut', { prix: '-10', distance: '0' })
  await calculer(page)
  await attendreDebourse(page, 'bas', '3732.95')
  await attendreDebourse(page, 'haut', '3136.05')
  await expect(page.getByTestId('scenarios-ordre')).toBeVisible()
  // La colonne « bas » est TOUJOURS la première, avec son total le plus cher :
  // réordonner masquerait l'information la plus utile.
  const colonnes = await page.locator('[data-testid^="scenario-"] legend').allInnerTexts()
  expect(colonnes).toEqual(['Bas', 'Probable', 'Haut'])

  // ---- 12. rien n'a été écrit, et une version gelée reste isolée
  await page.reload()
  await expect(page.getByRole('row', { name: `Déboursé sec ${REFERENCE} EUR` })).toBeVisible({
    timeout: 20_000,
  })
  await expect(page.getByText('Brouillon', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: 'Geler cette version' }).click()
  await page.getByRole('button', { name: /confirmer/i }).click()
  await expect(page.getByText('Gelée', { exact: true })).toBeVisible()
  const empreinteAvant = await page.getByTestId('empreinte').innerText()

  // La version gelée se simule elle aussi — depuis son INSTANTANÉ, ce que la
  // réponse dit —, et le sceau du gel n'en bouge pas.
  await poser(page, 'haut', { prix: '10' })
  await calculer(page)
  await attendreDebourse(page, 'haut', '3732.95')
  await attendreDebourse(page, 'probable', REFERENCE)
  await page.reload()
  await expect(page.getByTestId('empreinte')).toHaveText(empreinteAvant)

  await seDeconnecter(page)

  // ---- 13. un métreur compare ses déboursés, sans jamais voir la marge
  await seConnecter(page, METREUR)
  await page.goto(urlVersion)
  await expect(page.getByRole('region', { name: 'Scénarios de chiffrage' })).toBeVisible()
  await calculer(page)
  await attendreDebourse(page, 'probable', REFERENCE)
  // Aucune chaîne de prix : le rôle porte `cost:read` SANS `margin:read`, et
  // le serveur retire les étapes commerciales de sa réponse.
  await expect(page.getByTestId('scenario-marges-probable')).toHaveCount(0)
  await seDeconnecter(page)

  // ---- 14. un lecteur sans `cost:read` ne voit pas le panneau du tout
  await seConnecter(page, LECTEUR)
  await page.goto(urlVersion)
  await expect(page.getByRole('heading', { name: /Étude de prix/ })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Scénarios de chiffrage' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Calculer les trois scénarios' })).toHaveCount(0)
  await seDeconnecter(page)
})
