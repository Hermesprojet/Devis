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
