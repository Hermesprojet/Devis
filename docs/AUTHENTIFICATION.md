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
2.      le navigateur va chez le fournisseur, s'authentifie, revient
3. GET  /api/v1/auth/oidc/callback   → vérifie tout, redirige vers l'application
                                       avec un code de connexion OPAQUE
4. POST /api/v1/auth/oidc/exchange   → rend la session, une seule fois
```

Ce qui est vérifié à l'étape 3 : le `state` existe, n'a pas expiré, n'a pas déjà
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
