/**
 * Les gestes que tous les scénarios de la suite partagent.
 *
 * Ils vivaient recopiés à l'identique dans trois fichiers. La copie n'était
 * pas qu'un doublon : quand la connexion a cessé d'aboutir en répétition de
 * préproduction, les trois copies ont échoué de la même manière muette — deux
 * minutes d'attente sur `waitForURL`, puis « Test timeout exceeded », et pas
 * un mot du refus que l'écran affichait pourtant. Le journal de la répétition
 * nommait le fichier fautif, jamais la cause. Corriger une seule des trois
 * copies aurait laissé les deux autres aveugles.
 */

import type { Page } from '@playwright/test'

import { t } from '../src/lib/i18n'

/**
 * Passé ce délai, la connexion n'aboutira plus.
 *
 * Volontairement plus court que le délai du test : c'est ce qui laisse à
 * l'échec le temps de LIRE l'écran et de rapporter le motif, au lieu de se
 * faire couper net par Playwright.
 */
const DELAI_CONNEXION = 30_000

/**
 * Le bouton qui lance la connexion par le fournisseur, nommé par le
 * DICTIONNAIRE et non par une copie de son libellé.
 *
 * Trois fichiers de test recopiaient « compte de l'entreprise ». Renommer ce
 * bouton dans `src/lib/i18n.ts` a donc cassé deux suites — et, pire, en a
 * rendu une troisième complaisante : `e2e/connexion.spec.ts` vérifiait
 * l'ABSENCE du bouton par ce même libellé, et un libellé qui ne désigne plus
 * rien rend cette absence vraie quoi qu'affiche la page. Un test qui passe
 * d'autant mieux que le produit a changé ne prouve rien.
 *
 * `exact` parce que l'écran porte désormais un second bouton dont le nom
 * contient « compte » : « Utiliser un autre compte ».
 */
export const BOUTON_CONNEXION = { name: t('login.oidcSubmit'), exact: true } as const
export const BOUTON_AUTRE_COMPTE = { name: t('login.otherAccount'), exact: true } as const

/** La connexion réelle : celle du déploiement, par le fournisseur d'identité. */
export async function seConnecter(page: Page, adresse: string): Promise<void> {
  await page.goto('/')
  await page.getByRole('button', BOUTON_CONNEXION).click()
  await page.locator('#email').fill(adresse)
  await page.getByRole('button', { name: 'Se connecter' }).click()

  try {
    await page.waitForURL(/\/projets$/, { timeout: DELAI_CONNEXION })
    return
  } catch {
    // On ne relaie pas l'expiration : elle ne dit rien. C'est ici, et
    // seulement ici, qu'on peut encore lire ce que la page montrait.
  }
  throw new Error(
    `La connexion de ${adresse} n'a pas mené à /projets.\n${await constat(page)}`,
  )
}

export async function seDeconnecter(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Se déconnecter' }).click()
  await page.waitForURL(/\/$/)
}

/**
 * Ce que la page montrait au moment de l'échec, sans jamais le faire tomber.
 *
 * Chaque lecture est protégée : une page en cours de navigation détruit son
 * contexte d'exécution, et une sonde qui lèverait ici remplacerait le vrai
 * motif par « Execution context was destroyed » — mesuré.
 *
 * Une première version courait aussi après un `role=alert` visible, pour
 * rapporter le refus sans attendre. Elle se trompait : Next.js maintient en
 * permanence un `role=alert` VIDE — l'annonceur de route, destiné aux
 * lecteurs d'écran. Il devenait visible au moment même de l'arrivée, et la
 * course déclarait un refus sur une connexion parfaitement réussie.
 */
async function constat(page: Page): Promise<string> {
  async function lire<T>(sonde: () => Promise<T>, defaut: T): Promise<T> {
    try {
      return await sonde()
    } catch {
      return defaut
    }
  }

  const dits = await lire(
    async () =>
      (await page.getByRole('alert').allInnerTexts())
        .map((ligne) => ligne.replace(/\s+/g, ' ').trim())
        .filter(Boolean),
    ['(écran illisible)'],
  )

  // Les NOMS des clés de session, jamais leurs valeurs : le jeton est un
  // secret, et un message d'échec finit dans un journal de CI. Leur seule
  // présence distingue les deux échecs possibles — un échange de code refusé,
  // qui n'a jamais rien posé, d'une session ouverte puis rejetée, que
  // l'application efface avant de revenir à l'accueil.
  const cles = await lire(
    () =>
      page.evaluate(() =>
        Object.keys(window.sessionStorage).filter((cle) => cle.startsWith('metreo.')),
      ),
    ['(sessionStorage illisible)'],
  )

  return (
    `  adresse atteinte : ${page.url()}\n` +
    `  écran            : ${dits.join(' | ') || '(aucun message affiché)'}\n` +
    `  session en place : ${cles.join(', ') || '(aucune)'}`
  )
}

/**
 * Le texte imprimé d'un PDF, tel qu'un lecteur le verrait.
 *
 * Chercher une chaîne dans les octets bruts ne prouve rien : un opérateur
 * `Tj` la porte échappée, un accent y est un octal, et une comparaison brute
 * répond « absent » pour du texte pourtant imprimé. Ce décodeur ne lit que ce
 * que le document DESSINE — c'est la seule lecture qui autorise à conclure
 * qu'une mention interne ne figure pas sur un devis client.
 */
export function texteDuPdf(pdf: Buffer): string {
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

/** L'espace fine insécable, séparateur de milliers à l'écran. */
export const ESPACE_ECRAN = '\u202f'

/** L'espace insécable ordinaire, seul que WinAnsi porte — donc celui du PDF. */
export const ESPACE_PDF = '\u00a0'

/**
 * L'écriture belge d'un nombre que le moteur a déjà arrêté.
 *
 * **Transcrite ici à la main, et non importée de `src/lib/nombres`.** Un
 * scénario qui demanderait à l'application ce qu'elle doit afficher serait
 * vert quoi qu'elle affiche. Le harnais pose l'attendu ; le produit doit s'y
 * conformer.
 *
 * Rien n'est arrondi : les décimales sont celles de la chaîne canonique
 * reçue. « 23080.10 » devient « 23 080,10 ».
 *
 * `espace` vaut l'espace fine insécable U+202F à l'écran, et l'espace
 * insécable ordinaire U+00A0 dans un PDF — les polices de base d'un PDF sont
 * encodées en WinAnsi, qui ne porte pas la première.
 */
export function enBelge(canonique: string, espace: string = ESPACE_ECRAN): string {
  const negatif = canonique.startsWith('-')
  const [entiereBrute = '', fraction] = (negatif ? canonique.slice(1) : canonique).split('.')
  const groupes: string[] = []
  let entiere = entiereBrute
  while (entiere.length > 3) {
    groupes.unshift(entiere.slice(-3))
    entiere = entiere.slice(0, -3)
  }
  groupes.unshift(entiere)
  // Le signe est retiré AVANT le groupage et remis après : sans cela,
  // « -298.45 » voyait sa partie entière « -298 » dépasser trois caractères et
  // se faire couper en « - 298 ».
  const groupee = (negatif ? '-' : '') + groupes.join(espace)
  return fraction === undefined ? groupee : `${groupee},${fraction}`
}

/** Le même nombre, tel qu'un PDF l'imprime. */
export function enBelgeDansLePdf(canonique: string): string {
  return enBelge(canonique, ESPACE_PDF)
}

/**
 * Le nombre derrière une chaîne affichée en belge, pour refaire une addition.
 *
 * `Number('23 080,10')` rend `NaN` : un scénario qui additionnerait des
 * montants lus à l'écran sans repasser par ici comparerait des `NaN`, et un
 * `NaN` comparé à un `NaN` ne tombe pas — le test passerait à tort.
 */
export function nombreLu(affiche: string): number {
  return Number(affiche.replace(/[  \s]/g, '').replace(',', '.'))
}
