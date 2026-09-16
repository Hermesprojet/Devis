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
| le journal JSON de l'API, `docker compose logs api` | la requête, son `request_id`, le code HTTP — jamais un jeton, jamais un secret |
| chez le fournisseur (Auth0 : *Monitoring → Logs*) | la tentative vue de son côté : `Success Login`, `Failed Login`, et sa raison |

### Les codes de `login_error`, et ce qu'ils désignent

| code | cause | où regarder |
| --- | --- | --- |
| `provider_refused` | le fournisseur a renvoyé une erreur : consentement annulé, compte bloqué, application mal déclarée | le journal du fournisseur |
| `invalid_request` | retour sans `code` ni `state` | l'URI de redirection déclarée chez le fournisseur ne pointe pas la page d'accueil |
| `provider_unavailable` | découverte, JWKS ou point de jeton injoignables | le réseau sortant du conteneur `api` ; `METREO_OIDC_ISSUER` |
| `issuer_mismatch` | le document de découverte se déclare sous un autre émetteur que celui configuré | `METREO_OIDC_ISSUER` — **barre oblique finale comprise** |
| `provider_incomplete` | document de découverte ou réponse de jeton sans les champs attendus | le fournisseur, ou un `METREO_OIDC_ISSUER` qui pointe autre chose qu'un émetteur OIDC |
| `invalid_state` / `expired_state` | demande de connexion inconnue, déjà consommée, ou de plus de `METREO_OIDC_TRANSACTION_TTL_SECONDS` | un retour rejoué, ou deux instances API sans base commune |
| `code_rejected` | le fournisseur a refusé le code d'autorisation | `METREO_OIDC_CLIENT_SECRET` faux, ou l'URI de redirection différente entre `start` et le fournisseur |
| `invalid_audience` / `invalid_issuer` | jeton d'identité émis pour une autre application ou par un autre émetteur | `METREO_OIDC_CLIENT_ID`, `METREO_OIDC_ISSUER` |
| `token_expired` / `token_not_yet_valid` | horloge de la machine décalée | `date -u` sur le serveur, contre une source de temps |
| `invalid_token` / `invalid_nonce` | signature invalide ou jeton rejoué | rare ; un JWKS servi par un autre locataire, ou un rejeu |
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
  | grep -E 'login_methods|configuration_problems|browser_login' -A2

# 2. La demande de connexion se fabrique-t-elle, et vers le bon endroit ?
curl -s https://<application>/api/v1/auth/oidc/start | python3 -m json.tool
```

La réponse de l'étape 2 porte une URL d'autorisation : elle doit commencer par
`METREO_OIDC_ISSUER`, porter `client_id=` avec l'identifiant configuré,
`redirect_uri=` avec `METREO_OIDC_REDIRECT_URI` encodée, `scope=openid email
profile`, et `code_challenge_method=S256`. Chaque écart entre cette URL et ce
que le fournisseur a enregistré est un refus avant même que l'utilisateur ne
voie un formulaire. Le `state` rendu est à usage unique : cette requête a
**consommé** une transaction, ce qui n'a aucune conséquence.

```
# 3. Le retour du fournisseur atteint-il la bonne page ?
```

Après authentification, le navigateur doit revenir sur
`https://<application>/?code=…&state=…`. S'il atterrit sur `localhost` ou
sur un autre hôte, ce n'est pas le fournisseur : c'est l'image `web`, qui
porte l'URL d'API compilée à sa construction (voir `docs/EXPLOITATION.md`,
« Produire ces images »).

```
# 4. Le retour a-t-il produit un code de connexion, ou un code d'erreur ?
```

La page d'accueil relaie `code` et `state` à l'API, qui répond par une
redirection vers `/?login_code=…` — succès — ou `/?login_error=<code>` —
la table ci-dessus. Dans le journal de l'API, la requête `GET
/api/v1/auth/oidc/callback` porte un `303` dans les deux cas : c'est la
destination de la redirection qui distingue, pas le code HTTP.

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
