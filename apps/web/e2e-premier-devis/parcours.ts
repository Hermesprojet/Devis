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

/**
 * Passé ce délai, la connexion n'aboutira plus.
 *
 * Volontairement plus court que le délai du test : c'est ce qui laisse à
 * l'échec le temps de LIRE l'écran et de rapporter le motif, au lieu de se
 * faire couper net par Playwright.
 */
const DELAI_CONNEXION = 30_000

/** La connexion réelle : celle du déploiement, par le fournisseur d'identité. */
export async function seConnecter(page: Page, adresse: string): Promise<void> {
  await page.goto('/')
  await page.getByRole('button', { name: /compte de l'entreprise/ }).click()
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
