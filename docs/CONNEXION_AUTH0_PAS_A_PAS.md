# Votre vraie connexion Auth0, un geste à la fois

Ce guide se suit dans l'ordre, sans sauter d'étape. Chaque étape est **une
action**, suivie du **résultat attendu**. Si le résultat n'est pas celui-là,
arrêtez-vous à cette étape : la colonne « sinon » dit où regarder, et rien de
ce qui suit ne réussira tant qu'elle n'est pas réglée.

> **Qui fait quoi.** Toutes les actions de ce guide sont les vôtres. Les
> parties A et C se jouent dans le tableau de bord Auth0 et dans un navigateur.
> La partie B **touche le serveur de préproduction** : elle ne se fait qu'avec
> votre décision, et personne d'autre ne la lance à votre place.
>
> **Ce qui ne se transmet jamais** : le `Client Secret`, un mot de passe, et
> l'URL de retour complète — elle porte `code` et `state`, qui ouvrent une
> session. Pour un diagnostic, trois choses suffisent : la valeur de
> `login_error` lue dans la barre d'adresse, le message affiché à l'écran, et
> le fait que l'écran de Google soit apparu ou non.

## Deux preuves distinctes, qui ne se remplacent pas

| | Ce qui l'établit | Ce que ça prouve | Ce que ça ne prouve pas |
| --- | --- | --- | --- |
| **Fournisseur simulé** | l'atelier « Banc de connexion (14 scénarios) » de la CI, et les parcours navigateur de `apps/web/e2e-premier-devis/`, à chaque commit | que **notre moitié** du protocole est juste : `state`, PKCE, code à usage unique, jeton jamais dans une URL, codes de refus | rien sur Auth0, rien sur Google |
| **Votre Auth0, ce guide** | vous, dans un navigateur, sur la préproduction | que **la vôtre** l'est : l'application déclarée, l'URI de retour, la connexion Google, l'adresse vérifiée | rien de plus que les cinq scénarios joués |

Le fournisseur simulé ne peut pas franchir l'écran de Google. C'est pourquoi
ce guide existe, et pourquoi son résultat se note à part.

## Avant de commencer

| | Ce qu'il faut | Pourquoi |
| --- | --- | --- |
| 1 | l'adresse Google **exacte** avec laquelle vous vous connecterez | c'est elle qui sera liée au premier compte administrateur |
| 2 | l'adresse publique de la préproduction, ici `https://preprod.metreobtp.com/` | elle doit être écrite **à l'identique**, barre finale comprise, à trois endroits |
| 3 | un accès au tableau de bord de votre locataire Auth0 | partie A |
| 4 | un accès au serveur, si vous décidez de jouer la partie B | partie B |

**Quelle version faut-il sur le serveur.** Les scénarios A, B, D et E se
jouent sur la version en service (`39ad8d0`). Le scénario C — le bouton
« Utiliser un autre compte » — n'existe qu'à partir de la demande #91 : il ne
se joue qu'une fois celle-ci fusionnée et déployée, ce qui reste votre
décision.

---

## A — Dans le tableau de bord Auth0

| # | Action | Résultat attendu | Sinon |
| --- | --- | --- | --- |
| A1 | *Applications → Applications → Create Application*. Nom : « Metreo préproduction ». Type : **Regular Web Applications**. *Create*. | La fiche de l'application s'ouvre sur l'onglet *Settings*. | Un autre type (*Single Page*, *Native*) n'a pas de secret client : recommencez avec *Regular Web Applications*. |
| A2 | Dans *Settings*, relevez **Domain**, par exemple `votre-locataire.eu.auth0.com`. | Vous avez le domaine. L'émetteur sera `https://` + ce domaine + `/`. | — |
| A3 | Relevez **Client ID**. Ne copiez pas le **Client Secret** ailleurs que dans le fichier du serveur, à l'étape B2. | Vous avez l'identifiant. | — |
| A4 | *Allowed Callback URLs* : `https://preprod.metreobtp.com/`. *Allowed Logout URLs* : la même. *Allowed Web Origins* : `https://preprod.metreobtp.com`. *Save Changes*. | Le bandeau « Changes saved » apparaît. | Une barre finale manquante, un `www.` ou un `http` suffisent à faire refuser la connexion : « Callback URL mismatch », affiché par Auth0. |
| A5 | Onglet *Credentials* : *Authentication Method* = **Client Secret (Post)**. | Metreo envoie le secret dans le corps de la requête de jeton : c'est la méthode « Post ». | Avec une autre méthode, le retour aboutit sur `login_error=code_rejected`. |
| A6 | *Settings → Advanced Settings → OAuth* : *JSON Web Token Signature Algorithm* = **RS256**, *OIDC Conformant* activé. *Grant Types* : **Authorization Code** coché. *Save Changes*. | Enregistré. | Metreo refuse les jetons signés en HS256 : un jeton signé par un secret partagé ne prouve pas qui l'a émis. |
| A7 | Onglet *Connections* de l'application : activez **google-oauth2**. Désactivez les connexions que vous n'utiliserez pas. | Google apparaît activé pour cette application. | Sans connexion activée, Auth0 affiche une page sans aucun moyen de se connecter. |
| A8 | *Authentication → Social → Google* : vérifiez que les attributs demandés incluent l'adresse e-mail. | La connexion Google transmet l'adresse **et** son état vérifié. | Sans `email_verified: true`, le premier lien d'identité est refusé : `login_error=email_not_verified`. |

> **Les clés de développement d'Auth0.** Tant que la connexion Google utilise
> les clés de démonstration d'Auth0, elle fonctionne pour un essai, mais Auth0
> l'annonce comme non destinée à la production. Remplacer ces clés par celles
> d'un projet Google à vous est une décision séparée, à prendre avant
> d'ouvrir Metreo à d'autres personnes.

---

## B — Sur le serveur, avec votre décision

Les commandes se lancent depuis la racine du clone sur le serveur, avec la
fonction `mc` décrite dans `docs/EXPLOITATION.md` (« Les mêmes fichiers de
composition, à chaque commande »).

| # | Action | Résultat attendu | Sinon |
| --- | --- | --- | --- |
| B1 | `git check-ignore infra/staging.env` | La commande répond `infra/staging.env` : le fichier est ignoré par Git. | **Arrêtez-vous.** Un fichier non ignoré emporterait le secret au prochain `git add`. |
| B2 | Dans `infra/staging.env`, écrivez les cinq lignes :<br>`METREO_AUTH_MODE=oidc`<br>`METREO_OIDC_ISSUER=https://<Domain>/`<br>`METREO_OIDC_CLIENT_ID=<Client ID>`<br>`METREO_OIDC_CLIENT_SECRET=<Client Secret>`<br>`METREO_OIDC_REDIRECT_URI=https://preprod.metreobtp.com/` | — | — |
| B3 | `grep -E '^METREO_OIDC_(ISSUER\|CLIENT_ID\|REDIRECT_URI)=' infra/staging.env` | Trois lignes, sans espace autour du `=`, sans guillemets, la troisième identique à l'étape A4. Cette commande n'affiche **pas** le secret, et c'est voulu. | Un commentaire en fin de ligne fait partie de la valeur : gardez chaque commentaire sur sa propre ligne. |
| B4 | `mc up -d api` | Le conteneur `api` est recréé. `mc ps` le montre `healthy` après quelques secondes. | `mc logs api` : une configuration OIDC incomplète refuse de démarrer, et **nomme** chaque valeur manquante. |
| B5 | `curl -s https://preprod.metreobtp.com/api/v1/health \| python3 -m json.tool \| grep -E 'login_methods\|configuration_problems' -A2` | `login_methods` contient `oidc`, `configuration_problems` est vide. | Le problème est nommé dans `configuration_problems`. |
| B6 | `curl -s https://preprod.metreobtp.com/api/v1/auth/oidc/start \| python3 -m json.tool` | `authorization_url` commence par `https://<Domain>/authorize`, et porte `client_id=<Client ID>`, `redirect_uri=https%3A%2F%2Fpreprod.metreobtp.com%2F` et `code_challenge_method=S256`. | `issuer_mismatch` ou `provider_unavailable` : relisez l'émetteur de l'étape B2, `https` et domaine compris. |
| B7 | `mc exec -T api python -m metreo_api.bootstrap --organization "<Nom de l'entreprise>" --admin-email "<adresse Google exacte>" --admin-name "<Prénom Nom>"` | Une ligne `organisation='…' id=… admin=<adresse> role=org_admin (créé)`, ou `(déjà en place)` si elle existait. | `bootstrap refusé : …` dit pourquoi. Les domaines réservés, comme `.invalid`, sont refusés. |

L'étape B6 ouvre une demande de connexion qui expire seule au bout de dix
minutes. Elle ne gêne rien, et ne crée aucun compte.

L'étape B7 crée **le droit d'entrer, pas un moyen d'entrer** : aucun mot de
passe n'est créé. Elle est idempotente : la relancer avec la même adresse ne
duplique rien.

---

## C — Dans un navigateur, les cinq scénarios

Pour chacun, notez **ce qui s'affiche** et, s'il apparaît dans la barre
d'adresse, la valeur de `login_error`. Rien d'autre.

| # | Action | Résultat attendu | Sinon |
| --- | --- | --- | --- |
| C1 — A | Fenêtre de navigation **privée**. Ouvrez `https://preprod.metreobtp.com/`. Cliquez **Continuer vers la connexion**. Chez Google, choisissez l'**adresse de l'étape B7**. | Retour sur Metreo, puis la liste des projets. **C'est le seul scénario qui prouve que vous pouvez entrer.** | « Callback URL mismatch » chez Auth0 : étape A4. `login_error=unknown_user` : l'adresse diffère de celle de l'étape B7. `code_rejected` : étapes A5 et B2. |
| C2 — E | Fermez l'onglet, rouvrez l'application dans la même fenêtre. | Vous êtes toujours connecté, sans repasser par Google. | — |
| C3 — B | Déconnectez-vous de Metreo, puis relancez la connexion. | Google reprend votre compte **sans rien demander**. C'est le fonctionnement normal d'OIDC, pas un défaut. | — |
| C4 — D | Dans une autre fenêtre privée, connectez-vous avec une adresse Google **qui n'a pas de compte** dans Metreo. | Refus, avec un message qui nomme la cause. `login_error=unknown_user`. | Un autre code : notez-le, il désigne la cause dans `docs/AUTHENTIFICATION.md`. |
| C5 — C | *Après déploiement de la #91 seulement.* Connecté côté Google, cliquez **Utiliser un autre compte**. | Auth0 affiche à nouveau son écran. Ensuite, **deux fins sont valables** : Google redemande une authentification, ou Metreo affiche un avis bleu disant que le compte ouvert a été repris. | Notez laquelle des deux vous obtenez : elle dépend de la configuration Google de votre locataire, pas de Metreo. |

### Le tableau à me renvoyer

| Scénario | Ce qui s'affiche | `login_error` | L'écran Google est-il apparu ? |
| --- | --- | --- | --- |
| C1 — A | | | |
| C2 — E | | | |
| C3 — B | | | |
| C4 — D | | | |
| C5 — C | | | |

Ce tableau ne contient ni adresse e-mail, ni URL de retour, ni secret. Une
capture d'écran convient si la barre d'adresse et l'adresse e-mail y sont
masquées.

---

## Revenir en arrière

Remettre `METREO_AUTH_MODE` à sa valeur précédente dans `infra/staging.env`,
puis `mc up -d api`. Les comptes créés par l'étape B7 restent en base : ils ne
donnent accès à rien tant qu'aucun fournisseur ne les authentifie.

Le détail des codes de refus et le diagnostic en cinq requêtes sont dans
`docs/AUTHENTIFICATION.md`. La procédure d'origine, avec le pourquoi de chaque
scénario, est dans `docs/CONTROLE_DE_CONNEXION.md`.
