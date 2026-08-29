import { expect, test } from '@playwright/test'

import { API_BASE_URL } from '../playwright.config'

/**
 * L'écran de connexion, côté navigateur.
 *
 * Ce que ces tests couvrent : ce que la page propose selon ce que le
 * déploiement annonce, et ce qu'elle fait d'un retour de fournisseur.
 *
 * Ce qu'ils ne couvrent pas : l'aller-retour complet chez un fournisseur
 * d'identité. Il est prouvé au niveau HTTP dans
 * `apps/api/tests/test_oidc_http_flow.py`, contre un vrai fournisseur RS256
 * simulé — jusqu'à la vérification de signature, de nonce et d'usage unique.
 * Le rejouer ici demanderait une seconde compilation Next par exécution,
 * parce que `NEXT_PUBLIC_API_URL` est figée à la compilation : l'API de ce
 * banc tourne en mode `dev`, une seconde en mode `oidc` aurait besoin de son
 * propre paquet.
 */

test("l'API de ce banc annonce la connexion de développement, et la page la propose", async ({
  page,
  request,
}) => {
  const sante = await (await request.get(`${API_BASE_URL}/health`)).json()
  expect(sante.login_methods).toEqual(['dev'])

  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Se connecter', exact: true })).toBeVisible()
  // Le bouton du fournisseur n'existe pas : proposer un parcours que ce
  // déploiement n'offre pas mène l'utilisateur sur un 404.
  await expect(page.getByRole('button', { name: /compte de l'entreprise/ })).toHaveCount(0)
})

// Next monte son propre `role="alert"` vide pour annoncer les changements de
// route : viser le rôle seul en attrape deux, et l'échec ne dit rien du produit.
const RETOUR = '.notice.warning'

test('un refus du fournisseur revient en phrase, pas en code technique', async ({ page }) => {
  await page.goto('/?login_error=no_membership')
  await expect(page.locator(RETOUR)).toContainText("n'appartient à aucune organisation active")
  await expect(page.locator(RETOUR)).not.toContainText('no_membership')
})

test("un motif de refus inconnu ne s'affiche pas brut à l'écran", async ({ page }) => {
  await page.goto('/?login_error=quelque_chose_de_neuf')
  await expect(page.locator(RETOUR)).toContainText('La connexion a échoué')
  await expect(page.locator(RETOUR)).not.toContainText('quelque_chose_de_neuf')
})

test("un code de connexion invalide échoue et quitte la barre d'adresse", async ({ page }) => {
  // Le code est à usage unique : le laisser dans l'URL ferait qu'un
  // rechargement rejoue un code déjà consommé et affiche une erreur qui n'en
  // est pas une.
  await page.goto('/?login_code=code-qui-nexiste-pas-0123456789')
  await expect(page.locator('.notice.error, .notice.warning').first()).toBeVisible()
  await expect(page).toHaveURL(/\/$/)
  expect(page.url()).not.toContain('login_code')
})

test("aucun jeton ne traîne dans l'URL après une connexion réussie", async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'admin@dubois.demo' }).click()
  await page.getByRole('button', { name: 'Se connecter', exact: true }).click()
  await expect(page).toHaveURL(/\/projets$/)

  const url = page.url()
  expect(url).not.toContain('token')
  expect(url).not.toContain('access_token')
  // Un JWT commence par `eyJ` : le chercher tel quel attrape aussi un jeton
  // glissé sous un autre nom de paramètre.
  expect(url).not.toContain('eyJ')
})
