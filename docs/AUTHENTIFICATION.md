# Se connecter à Metreo

Ce document décrit le seul moyen de connexion destiné à autre chose qu'un poste
de développement : **OpenID Connect**, code d'autorisation avec PKCE, contre le
fournisseur d'identité de l'entreprise.

## Ce que Metreo ne fait pas

- **Aucun mot de passe applicatif.** Metreo n'en stocke pas, n'en vérifie pas,
  n'en réinitialise pas. C'est le fournisseur d'identité qui authentifie.
- **Aucune inscription publique.** Un compte inconnu est refusé. Un
  administrateur crée le compte et l'appartenance avant la première connexion.
- **Aucune cryptographie réimplémentée.** La vérification des jetons passe par
  PyJWT et `PyJWKClient` ; la découverte, par le document standard du
  fournisseur.

## Les trois modes

`METREO_AUTH_MODE` vaut `dev`, `jwt` ou `oidc`.

| mode | ce qu'il fait | où il est admis |
| --- | --- | --- |
| `dev` | connexion par adresse e-mail, sans mot de passe | développement et test **seulement** — refusé au démarrage en production |
| `jwt` | accepte des jetons émis ailleurs, n'en émet aucun | intégration machine à machine ; **aucun humain ne peut se connecter** |
| `oidc` | parcours complet chez le fournisseur | recette et production |

Le mode `jwt` est légitime, et son absence de parcours de connexion est visible
plutôt que subie : `GET /api/v1/health` renvoie `login_methods: []`, et l'écran
de connexion le dit au lieu d'afficher un formulaire qui n'aboutirait pas.

En mode `oidc`, si l'une des quatre valeurs obligatoires manque, **le service
refuse fermé** : `validate_startup()` nomme chaque valeur absente, `/health`
passe en `degraded`, et les trois routes de connexion répondent `404`. Il n'y a
pas de parcours partiel.

## Configuration

Quatre valeurs sont indissociables :

```
METREO_AUTH_MODE=oidc
METREO_OIDC_ISSUER=https://identite.exemple.invalid
METREO_OIDC_CLIENT_ID=...
METREO_OIDC_CLIENT_SECRET=...
METREO_OIDC_REDIRECT_URI=https://app.exemple.invalid/
```

Trois valeurs facultatives :

```
METREO_OIDC_SCOPES=openid email profile
METREO_OIDC_TRANSACTION_TTL_SECONDS=600   # durée de vie d'une demande en cours
METREO_OIDC_LOGIN_CODE_TTL_SECONDS=120    # durée de vie du code de connexion
```

`METREO_OIDC_REDIRECT_URI` doit être **exactement** l'URI déclarée chez le
fournisseur, et pointer sur l'écran de connexion de l'application.

### Côté fournisseur

À déclarer par le propriétaire, chez son fournisseur :

1. une application cliente **confidentielle** (elle a un secret) ;
2. l'URI de redirection ci-dessus, à l'identique ;
3. les portées `openid email profile` ;
4. la revendication `email_verified` renseignée — sans elle, aucune première
   liaison n'est possible (voir plus bas) ;
5. le type de réponse `code` et PKCE `S256` autorisés.

Le document de découverte `\<issuer\>/.well-known/openid-configuration` doit se
déclarer lui-même sous l'émetteur configuré : un écart est un refus, pas un
avertissement.

## Le parcours, requête par requête

```
1. GET  /api/v1/auth/oidc/start      → URL d'autorisation ; la transaction est
                                       écrite en base (state, nonce, verifier)
2.      le navigateur va chez le fournisseur, s'authentifie, revient sur
                                       METREO_OIDC_REDIRECT_URI avec code+state
3.      la page d'accueil relaie code et state à l'API
4. GET  /api/v1/auth/oidc/callback   → vérifie tout, redirige vers l'application
                                       avec un code de connexion OPAQUE
5. POST /api/v1/auth/oidc/exchange   → rend la session, une seule fois
```

L'étape 3 n'est pas un détail : `METREO_OIDC_REDIRECT_URI` désigne
l'**application**, pas l'API — c'est le navigateur qui revient, et il revient
sur une page. La page d'accueil transmet `code` et `state` à l'API sans rien en
vérifier : la signature, l'émetteur, l'audience, le `nonce` et l'usage unique
sont contrôlés par l'API, seule à détenir le vérificateur PKCE. Sans ce relais,
le parcours réussissait au niveau HTTP et échouait dans un navigateur — mesuré :
la page ignorait les deux paramètres et réaffichait l'écran de connexion, si
bien que le premier utilisateur d'un déploiement neuf ne pouvait pas entrer.

Ce qui est vérifié à l'étape 4 : le `state` existe, n'a pas expiré, n'a pas déjà
servi ; la signature du jeton d'identité contre le JWKS du fournisseur ;
l'émetteur ; l'audience ; l'expiration ; et le `nonce`, comparé explicitement —
la bibliothèque ne le fait pas.

### Pourquoi la transaction est en base

Une demande de connexion commence sur une instance et revient sur une autre : un
`state` gardé en mémoire de processus rendrait le parcours aléatoire dès la
deuxième instance. La table `login_transactions` porte l'état, et son marquage
de consommation fait du rejeu un refus d'état, pas une course.

### Pourquoi un code opaque et pas le jeton

Le jeton final **n'apparaît jamais dans une URL**. Une URL se retrouve dans
l'historique du navigateur, dans les journaux du proxy, et dans l'en-tête
`Referer` envoyé au premier lien externe cliqué. Le navigateur ne rapporte donc
qu'un code court, opaque, à usage unique et de courte durée ; le jeton n'existe
que dans le corps de la réponse à l'étape 4. Le code est **effacé** à l'échange
plutôt que marqué utilisé : rien ne doit pouvoir le retrouver.

## Comment une identité est reconnue

### Si le compte Google revient tout seul

Le bouton **Continuer vers la connexion** utilise la session déjà ouverte chez
le fournisseur d'identité. Se déconnecter de Metreo efface la session Metreo,
mais pas nécessairement la session chez ce fournisseur : celui-ci peut alors
réutiliser Google au prochain essai.

Le bouton **Utiliser un autre compte** transmet `prompt=login` au fournisseur
pour demander l'affichage de son écran de connexion, sans modifier le flux
`state` / nonce / PKCE ni la validation de l'identité. Ce paramètre indique un
choix d'interface ; un fournisseur social peut encore proposer ou réutiliser
une session Google. Il ne prouve pas qu'un mot de passe a été ressaisi.

Avec Auth0, si l'on souhaite les comptes e-mail et mot de passe, vérifier dans
**Authentication → Database → Username-Password-Authentication → Applications**
que **Metreo Préproduction** est activée. Si l'accès Google n'est pas souhaité,
désactiver son accès à cette application sous **Authentication → Social → Google
→ Applications**, après avoir vérifié qu'un compte de base de données actif et
vérifié permet bien d'entrer. Si seule la fenêtre Google se superpose à la
connexion, contrôler aussi **Applications → Metreo Préproduction → Settings →
Allow Google One Tap**.
Le mot de passe se gère dans Auth0, jamais dans Metreo. Contrôler le fournisseur
affiché sur la fiche de l'utilisateur si le compte choisi n'est pas celui attendu.

### Retrouver la connexion par e-mail, sans transmettre quoi que ce soit

Le cas qui bloque une première mise en ligne : le mot de passe est oublié, et
l'écran de Metreo ne propose aucun lien pour le réinitialiser. C'est normal —
**Metreo ne voit jamais de mot de passe**, donc il n'a rien à réinitialiser. Le
lien vit chez le fournisseur, sur l'écran QUI DEMANDE le mot de passe. Avec un
tenant réglé en « identifiant d'abord », cet écran n'arrive qu'après avoir
saisi l'adresse et validé : le lien est invisible avant.

Trois causes possibles, dans l'ordre où il faut les écarter.

**1. On n'est jamais arrivé sur l'écran du fournisseur.** Le bouton
« Continuer vers la connexion » n'a pas ouvert de page, ou une session Google
a court-circuité la saisie. Reprendre avec **Utiliser un autre compte**, qui
demande explicitement l'écran de connexion.

**2. On y est, mais sans champ « mot de passe ».** Saisir l'adresse, valider,
et regarder à nouveau. S'il n'apparaît toujours pas, la connexion base de
données n'est pas activée pour cette application : c'est le contrôle du
paragraphe précédent, sous **Authentication → Database →
Username-Password-Authentication → Applications**. Sans elle, il n'existe
aucun mot de passe à réinitialiser, et aucun lien à afficher.

**3. Le lien est là, mais le courriel n'arrive pas.** Passer par le tableau de
bord, qui ne dépend d'aucun envoi : **User Management → Users**, ouvrir la
fiche de l'utilisateur, **Actions → Change Password**. Le nouveau mot de passe
se saisit dans cette boîte de dialogue, et nulle part ailleurs.

Sur cette fiche, deux choses se vérifient au passage, et une ne se touche
jamais :

| à vérifier | pourquoi |
| --- | --- |
| l'adresse porte la mention vérifiée | sinon Metreo refuse, avec `email_not_verified` |
| le compte n'est pas `Blocked` | plusieurs essais ratés déclenchent la protection anti force brute du fournisseur ; **Actions → Unblock** |
| **ne pas** utiliser **Change Email** *avant la première connexion réussie* | tant qu'aucune liaison n'existe, c'est l'ADRESSE qui rattache le compte du fournisseur au compte Metreo. La changer casse ce rattachement (`unknown_user`) et repasse l'adresse en non vérifiée (`email_not_verified`) |

Après une première connexion réussie, la mise en garde tombe : la liaison
`(issuer, subject)` est faite, `resolve_user` la trouve avant de regarder quoi
que ce soit d'autre, et l'adresse ne décide plus. **Ce qui casse alors la
liaison, c'est de supprimer puis recréer l'utilisateur chez le fournisseur** —
le `subject` change, et le compte Metreo n'est plus rattaché à personne. Un
changement d'adresse, lui, est sans effet.

#### Ce qui se transmet, et ce qui ne se transmet jamais

Depuis l'ajout des messages de refus, **l'écran dit lui-même la plupart des
causes** : un compte désactivé, un fournisseur injoignable, un réglage à
corriger. Lisez la phrase affichée avant tout.

Quand elle ne suffit pas, **une seule valeur est à transmettre** : le code lu
dans la barre d'adresse, `?login_error=<code>`. Il correspond au nom de la clé
`login.error.<code>` de `apps/web/src/lib/i18n.ts`, et il est émis par
`apps/api/src/metreo_api/services/oidc.py`.

Ne transmettez jamais, à personne :

- un mot de passe, ni le `Client Secret` de l'application ;
- **l'URL de retour complète.** Elle porte `code` et `state`. Le `code` est un
  jeton d'autorisation à usage unique : qui le recopie avant vous ouvre la
  session à votre place. Le recopier dans un message, un ticket ou une
  capture, c'est le publier. Relevez le seul `login_error`, et rien d'autre.

Une capture d'écran, elle, se transmet à condition de masquer la barre
d'adresse et l'adresse e-mail.

L'identité est le couple **immuable `(issuer, subject)`**, stocké dans
`external_identities`. C'est lui qui décide, à chaque connexion après la
première.

La toute première connexion n'a pas encore ce couple. Elle se lie par l'adresse
e-mail, et **uniquement** si les quatre conditions sont réunies :

1. le fournisseur déclare l'adresse vérifiée (`email_verified: true`) ;
2. un compte porte cette adresse — créé par un administrateur, jamais par le
   parcours lui-même ;
3. le compte est actif ;
4. il a au moins une appartenance active.

Une fois la liaison faite, **l'adresse ne décide plus**. Un compte dont
l'adresse change chez le fournisseur reste le même compte ; une adresse
réattribuée à quelqu'un d'autre chez le fournisseur ne donne pas accès au compte
d'origine. C'est la raison d'être du couple immuable.

Un utilisateur inconnu, désactivé, ou sans appartenance active est refusé.

### Plusieurs organisations

Quand un compte appartient à plusieurs organisations actives, l'échange répond
`400 organization_required` avec la liste des identifiants, et attend un choix
explicite. Aucune organisation n'est présélectionnée : faire travailler
quelqu'un dans la mauvaise sans qu'il l'ait voulu coûte plus cher qu'un clic.

## Amorcer un déploiement neuf

Une base neuve n'a aucun compte, et personne ne peut donc se connecter. La
commande d'amorçage crée l'organisation initiale, le premier administrateur et
son appartenance — **sans mot de passe**, et sans exécuter le jeu de
démonstration :

```
python -m metreo_api.bootstrap \
  --organization "Nom de l'entreprise" \
  --admin-email "prenom.nom@entreprise.example" \
  --admin-name "Prénom Nom"
```

L'adresse est validée par le **même contrôle que la connexion**, et pas
seulement sur la présence d'un `@`. Conséquence pratique : les domaines
réservés — `.invalid`, `.test`, `.localhost` — sont refusés. Ils l'étaient déjà
à la connexion ; ils étaient acceptés à l'amorçage, ce qui créait un premier
administrateur incapable d'entrer, sans que rien ne le signale. Pour un
exemple, `.example` convient.

Elle est **idempotente** : relancée avec les mêmes valeurs elle ne duplique rien
et ne modifie rien, ce qui permet de la laisser dans un script de démarrage.
Elle réactive en revanche une appartenance désactivée, parce que c'est la seule
lecture utile d'une commande qu'on relance pour rétablir l'accès.

L'administrateur ainsi créé n'a aucun moyen d'entrer tant qu'il ne s'est pas
connecté par le fournisseur sur cette adresse vérifiée. C'est voulu : ce que la
commande crée, c'est **le droit d'entrer, pas un moyen d'entrer**.

## Diagnostiquer une connexion qui échoue

Tout se lit sans identifiant réel jusqu'à l'étape 3 ; à partir de là, le
fournisseur d'identité tient son propre journal des tentatives, et c'est lui
qui dit pourquoi il a refusé.

### Où l'erreur se montre

| surface | ce qu'on y lit |
| --- | --- |
| l'URL de retour, `https://<application>/?login_error=<code>` | le **code** — le seul message que l'API confie au navigateur |
| le journal JSON de l'API — `mc logs api`, les mêmes `-f` qu'au déploiement (voir `docs/EXPLOITATION.md`), ou `docker compose -p metreo-staging logs api` | la requête, son `request_id`, le code HTTP — jamais un jeton, jamais un secret |
| chez le fournisseur (Auth0 : *Monitoring → Logs*) | la tentative vue de son côté : `Success Login`, `Failed Login`, et sa raison |

### Les codes de `login_error`, et ce qu'ils désignent

| code | cause | où regarder |
| --- | --- | --- |
| `provider_refused` | le fournisseur a renvoyé une erreur : consentement annulé, compte bloqué, application mal déclarée | le journal du fournisseur |
| `invalid_request` | le point de retour de l'API atteint sans `code` ou sans `state` — la page d'accueil ne relaie jamais l'un sans l'autre : adresse tapée à la main, ou fournisseur qui renvoie ailleurs que sur la page d'accueil | `METREO_OIDC_REDIRECT_URI` d'abord — c'est aussi là que ce code d'erreur est déposé —, puis l'URI déclarée chez le fournisseur |
| `provider_unavailable` | découverte, JWKS ou point de jeton injoignables | le réseau sortant du conteneur `api` ; `METREO_OIDC_ISSUER` |
| `issuer_mismatch` | le document de découverte se déclare sous un autre émetteur que celui configuré | `METREO_OIDC_ISSUER` : le domaine du locataire, en `https`, ou son domaine personnalisé s'il en a un. La barre oblique finale est **tolérée**, retirée avant la comparaison |
| `provider_incomplete` | document de découverte ou réponse de jeton sans les champs attendus | le fournisseur, ou un `METREO_OIDC_ISSUER` qui pointe autre chose qu'un émetteur OIDC |
| `invalid_state` / `expired_state` | demande de connexion inconnue, déjà consommée, ou de plus de `METREO_OIDC_TRANSACTION_TTL_SECONDS` | un retour rejoué — rechargement, bouton « précédent » —, qui **masque l'erreur de la première tentative** : lire la première URL de retour avant de recharger ; ou deux instances API sans base commune |
| `code_rejected` | le fournisseur a refusé le code d'autorisation | `METREO_OIDC_CLIENT_SECRET` faux, ou l'URI de redirection différente entre `start` et le fournisseur |
| `invalid_audience` / `invalid_issuer` | jeton d'identité émis pour une autre application ou par un autre émetteur | `METREO_OIDC_CLIENT_ID`, `METREO_OIDC_ISSUER` |
| `token_expired` / `token_not_yet_valid` | horloge de la machine décalée | `date -u` sur le serveur, contre une source de temps |
| `invalid_token` | signature invérifiable — clés JWKS injoignables depuis le conteneur `api`, clé tournée, jeton d'un autre locataire — ou algorithme refusé : Metreo n'accepte que RS256/384/512 et ES256/384, jamais HS256 | le réseau sortant du conteneur `api` ; chez Auth0, *Applications → Advanced Settings → OAuth → JSON Web Token Signature Algorithm* doit être RS256 |
| `invalid_nonce` | le jeton d'identité ne porte pas le `nonce` de la demande | un rejeu, ou un jeton obtenu ailleurs ; rare |
| `email_not_verified` | le fournisseur ne déclare pas l'adresse vérifiée | chez le fournisseur : vérifier l'adresse de l'utilisateur |
| `unknown_user` | aucun compte ne porte cette adresse, ou le jeton ne porte pas d'adresse | `python -m metreo_api.bootstrap` avec **exactement** l'adresse du fournisseur ; la portée `email` demandée |
| `account_disabled` | le compte existe et est désactivé | un administrateur le réactive |
| `no_membership` | le compte existe, sans appartenance active | `python -m metreo_api.bootstrap` relancé : il réactive l'appartenance |

Le piège le plus courant n'est pas dans cette table : un `callback URL
mismatch` **affiché par le fournisseur**, avant tout retour. C'est l'URI de
redirection déclarée chez lui qui diffère de `METREO_OIDC_REDIRECT_URI`, ne
serait-ce que d'une barre oblique finale.

### Dans l'ordre, sans se connecter

```
# 1. L'API annonce-t-elle la connexion OIDC, sans problème de configuration ?
curl -s https://<application>/api/v1/health | python3 -m json.tool \
  | grep -E 'login_methods|configuration_problems' -A2

# 2. La demande de connexion se fabrique-t-elle, et vers le bon endroit ?
curl -s https://<application>/api/v1/auth/oidc/start | python3 -m json.tool
```

La réponse de l'étape 2 est `{"authorization_url": "…"}`. Cette URL doit
commencer par le point d'autorisation du fournisseur — en pratique par
`METREO_OIDC_ISSUER` —, porter `client_id=` avec l'identifiant configuré,
`redirect_uri=` avec `METREO_OIDC_REDIRECT_URI` encodée,
`scope=openid+email+profile` (les espaces s'encodent en `+`), et
`code_challenge_method=S256`. Chaque écart entre cette URL et ce que le
fournisseur a enregistré est un refus avant même que l'utilisateur ne voie
un formulaire. Cette requête a **ouvert** une transaction de connexion, que
seul le retour du fournisseur consomme ; une transaction jamais rappelée
expire après `METREO_OIDC_TRANSACTION_TTL_SECONDS` et ne gêne rien. En ouvrir
une pour un diagnostic n'a aucune conséquence.

```
# 3. Le retour du fournisseur atteint-il la bonne page ?
```

Après authentification, le navigateur doit revenir sur
`https://<application>/?code=…&state=…`. Deux façons d'atterrir ailleurs,
qui ne se confondent pas :

- sur un autre hôte **avec** `?code=…&state=…` : c'est `METREO_OIDC_REDIRECT_URI`
  qui désigne le mauvais hôte — la valeur que `start` a envoyée, et que le
  fournisseur a acceptée parce qu'elle est aussi déclarée chez lui ;
- sur la bonne page d'accueil, puis un saut vers
  `localhost:8000/api/v1/auth/oidc/callback…` : c'est le relais de l'étape 4,
  et l'image `web` porte l'URL d'API compilée à sa construction (voir
  `docs/EXPLOITATION.md`, « Produire ces images »).

```
# 4. Le retour a-t-il produit un code de connexion, ou un code d'erreur ?
```

La page d'accueil relaie `code` et `state` à l'API, qui répond par une
redirection vers `/?login_code=…` — succès — ou `/?login_error=<code>` —
la table ci-dessus. Dans le journal de l'API, la requête `GET
/api/v1/auth/oidc/callback` porte un `303` dans les deux cas : c'est la
destination de la redirection qui distingue, pas le code HTTP, et elle se
lit dans la barre d'adresse du navigateur. La lire **avant** de recharger :
un rechargement rejoue le retour sur une transaction déjà consommée, et
`invalid_state` remplace le code d'origine.

```
# 5. L'échange rend-il la session ?
```

`POST /api/v1/auth/oidc/exchange` répond `200` avec la session, `401
invalid_login_code` si le code a expiré (`METREO_OIDC_LOGIN_CODE_TTL_SECONDS`,
deux minutes par défaut) ou déjà servi, `400 organization_required` si le
compte appartient à plusieurs organisations, `403 no_membership` sans
appartenance active.

Ce qu'aucune de ces étapes ne montre : un mot de passe. Le fournisseur
l'authentifie, Metreo ne le voit jamais. Un mot de passe oublié se
réinitialise chez le fournisseur, et n'est pas un défaut de Metreo.

## Éprouver le parcours sans fournisseur réel

`python -m metreo_api.dev_oidc_provider` monte un fournisseur OIDC minimal
signant en RS256, avec découverte et JWKS réels. Il **refuse de démarrer** hors
des environnements `development` et `test`.

Les tests, eux, n'en dépendent pas : `apps/api/tests/fake_oidc.py` monte le même
fournisseur derrière un transport `httpx` simulé, et
`apps/api/tests/test_oidc_http_flow.py` suit le parcours complet — jusqu'à
vérifier qu'aucun jeton n'apparaît dans une URL et qu'un code de connexion ne
sert qu'une fois.

## Ce qui reste à la charge du propriétaire

- Choisir et déclarer le fournisseur d'identité réel.
- Fournir les quatre valeurs de configuration comme secrets de la plateforme —
  **jamais dans Git**.
- Décider de la politique de comptes : qui crée, qui désactive, sous quel délai.

Tant que ces trois points ne sont pas tranchés, le parcours est fonctionnel et
prouvé, mais aucun humain réel ne peut se connecter.

## Éprouver le parcours AVEC un fournisseur réel

Tout ce qui précède éprouve **notre moitié** du protocole, et la prouve à
chaque commit. Cela ne dit rien d'Auth0 ni de Google : aucun test, nulle part,
n'a jamais exercé ce parcours contre de vrais conteneurs ET un vrai
fournisseur d'identité — un banc automatisé ne franchit pas l'écran de Google.

La procédure manuelle qui comble ce trou, ses cinq scénarios et ce qu'il faut
y observer vivent dans `docs/CONTROLE_DE_CONNEXION.md`.
La même procédure, découpée en une action à la fois avec son résultat attendu —
du tableau de bord Auth0 au premier écran de Metreo —, est dans
`docs/CONNEXION_AUTH0_PAS_A_PAS.md`.
