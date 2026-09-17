import { expect, test } from '@playwright/test'

import { ADMIN } from './banc'
import { BOUTON_AUTRE_COMPTE, BOUTON_CONNEXION, seDeconnecter } from './parcours'

/**
 * « Utiliser un autre compte » atteint-il vraiment le fournisseur ?
 *
 * Le besoin vient de l'exploitation : après une déconnexion de Metreo, la
 * session reste ouverte CHEZ LE FOURNISSEUR, qui renvoie alors le même compte
 * sans rien demander. Quelqu'un qui voulait entrer avec une autre adresse
 * n'avait aucun moyen visible de le faire.
 *
 * Le second bouton demande `prompt=login`. Jusqu'ici, seule l'API le
 * prouvait : rien ne disait que le bouton de l'écran le demandait réellement.
 * Entre les deux vivent `api.oidcStart`, un paramètre d'URL et un gestionnaire
 * de clic — trois endroits où le lien peut se rompre sans qu'un test tombe.
 *
 * Ce scénario est délibérément le dernier de la suite : il se connecte et ne
 * crée rien, mais les scénarios précédents partent d'une organisation qui se
 * remplit dans un ordre précis.
 *
 * Ce qu'il NE prouve pas, et ne peut pas prouver ici : qu'Auth0 affichera un
 * écran de saisie. `prompt=login` est une demande, pas une garantie — un
 * fournisseur social reste libre de proposer Google. Voir
 * `docs/AUTHENTIFICATION.md`, « Si le compte Google revient tout seul ».
 */

/** Ce que le fournisseur doit recevoir dans tous les cas. */
function verifierLeSocleOidc(url: string): void {
  const parametres = new URL(url).searchParams
  expect(parametres.get('code_challenge_method'), 'PKCE perdu en route').toBe('S256')
  expect(parametres.get('state'), 'state perdu en route').toBeTruthy()
  expect(parametres.get('nonce'), 'nonce perdu en route').toBeTruthy()
  expect(parametres.get('client_secret'), 'un secret ne passe jamais par le navigateur').toBeNull()
}

test('le bouton ordinaire ne réclame pas d’écran de connexion au fournisseur', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', BOUTON_CONNEXION).click()
  await page.locator('#email').waitFor()

  const url = page.url()
  verifierLeSocleOidc(url)
  // Sans ce contrôle, poser `prompt=login` en permanence passerait inaperçu :
  // le parcours resterait vert, et chaque connexion redemanderait tout.
  expect(new URL(url).searchParams.get('prompt'), 'prompt imposé à tout le monde').toBeNull()
})

test('« Utiliser un autre compte » demande l’écran de connexion, et connecte quand même', async ({
  page,
}) => {
  await page.goto('/')
  await page.getByRole('button', BOUTON_AUTRE_COMPTE).click()
  await page.locator('#email').waitFor()

  const url = page.url()
  verifierLeSocleOidc(url)
  expect(new URL(url).searchParams.get('prompt')).toBe('login')

  // Le chemin doit rester praticable jusqu'au bout : un bouton qui demande un
  // écran de connexion mais n'ouvre plus de session ne vaut pas mieux que pas
  // de bouton du tout.
  await page.locator('#email').fill(ADMIN)
  await page.getByRole('button', { name: 'Se connecter' }).click()
  await page.waitForURL(/\/projets$/)

  await seDeconnecter(page)
})
