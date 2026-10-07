# Contrôler la connexion, pour de vrai

Ce document existe pour une raison précise, et il faut la dire avant tout le
reste :

> **Aucun test, nulle part, n'a jamais exercé le parcours de connexion contre
> de vrais conteneurs ET un vrai fournisseur d'identité.**

Ce n'est pas un oubli de couverture : c'est une limite de nature. Un banc
automatisé ne peut pas franchir l'écran de Google. La seule façon de savoir
qu'on entre dans Metreo est donc qu'un humain essaie, en suivant une procédure
qui dit à l'avance ce qu'il faut observer.

## 1. Ce que les tests prouvent déjà, et ce qu'ils ne prouvent pas

| Ce qui est éprouvé automatiquement | Avec quel fournisseur | Ce que cela ne dit pas |
| --- | --- | --- |
| Le parcours OIDC complet, requête par requête : `state`, nonce, PKCE, code à usage unique, jeton jamais présent dans une URL | **simulé** — `metreo_api.dev_oidc_provider`, monté par le banc lui-même | rien sur Auth0, rien sur Google |
| Les quatorze scénarios du « Banc de connexion » | **simulé** | idem |
| Les parcours navigateur de bout en bout | **simulé** : le banc remplit un champ e-mail sur une page qu'il héberge | idem |
| Chaque code de refus porte une phrase affichable | sans fournisseur : lecture croisée du code et du dictionnaire | que la phrase soit juste, seulement qu'elle existe |
| `max_age=0` part avec le bouton « autre compte », et seulement avec lui ; `auth_time` est lu et comparé à la demande | **simulé** | si Google honore `max_age` — c'est le scénario C qui le dit, et rien d'autre |

**Le fournisseur simulé est un bon outil** : il prouve que notre moitié du
protocole est correcte, et il la prouve à chaque commit. Il ne peut rien dire
de la moitié qui appartient à Auth0 et à Google. Les deux moitiés sont
nécessaires, aucune ne remplace l'autre.

> **Pour la suivre pas à pas**, depuis la déclaration de l'application chez
> Auth0 jusqu'au tableau à renvoyer : `docs/CONNEXION_AUTH0_PAS_A_PAS.md`.

## 2. Avant de commencer : l'adresse décide de tout

À la **première** connexion d'une personne, l'identité se lie par l'adresse
e-mail, et **seulement si les quatre conditions sont réunies** :

1. le fournisseur déclare l'adresse vérifiée (`email_verified: true`) ;
2. un compte Metreo porte **exactement** cette adresse — créé par un
   administrateur, jamais par le parcours lui-même ;
3. ce compte est actif ;
4. il porte au moins une appartenance active.

Ensuite seulement, le couple `(émetteur, sujet)` est enregistré, et c'est lui
qui décide à toutes les connexions suivantes : l'adresse ne décide plus.

**Vérification à faire en premier, elle coûte une minute** : l'adresse du
compte Google que vous utilisez est-elle exactement celle passée à
`--admin-email` lors de l'amorçage ? Si elle diffère, le refus est normal, et
aucun des scénarios ci-dessous n'aboutira. Relancer l'amorçage avec la bonne
adresse ne casse rien : la commande est idempotente.

## 3. Les cinq scénarios

Chacun se joue dans un navigateur, sur la préproduction. Notez pour chacun :
**ce qui s'affiche**, et la valeur du paramètre `login_error` s'il apparaît
dans la barre d'adresse.

### A — Première connexion, compte attendu

1. Fenêtre de navigation privée, aller sur l'accueil.
2. Cliquer sur le bouton de connexion par le compte de l'entreprise.
3. Chez Auth0 puis Google, s'identifier avec **l'adresse de l'amorçage**.

*Attendu* : retour sur Metreo, puis la liste des projets. C'est le seul
scénario qui démontre que le produit est utilisable.

### B — Une session Google déjà ouverte

1. Dans la même fenêtre, se déconnecter de Metreo.
2. Relancer la connexion.

*Attendu aujourd'hui* : le compte Google est repris **sans rien demander**.
Ce n'est pas un défaut, c'est le fonctionnement normal d'OIDC : la requête
d'autorisation que Metreo construit ne porte aucun paramètre `prompt`, donc un
fournisseur qui a déjà une session répond immédiatement.

### C — Changer de compte

1. Toujours connecté côté Google, lancer la connexion.
2. Utiliser le bouton **« Utiliser un autre compte »** (il n'existe qu'une
   fois la PR #73 fusionnée et déployée).

*Attendu* : Auth0 présente à nouveau son écran. Ensuite, **deux fins sont
possibles, et les deux sont des résultats valables** :

- soit Google redemande une authentification, et vous pouvez choisir un autre
  compte ;
- soit Google reprend le compte déjà connecté. Metreo affiche alors un **avis
  bleu** sous le titre : « Vous avez été reconnecté avec le compte déjà
  ouvert : votre fournisseur d'identité n'a pas redemandé d'authentification.
  Pour changer de compte, déconnectez-vous d'abord chez lui. »

**Notez laquelle des deux vous obtenez.** C'est l'information utile, et elle ne
se devine pas : elle dépend de la configuration de la connexion Google dans
votre tenant Auth0, pas de Metreo.

**Ce que Metreo fait maintenant, et ce qu'il ne peut pas faire.** Le bouton
transmet `prompt=login` ET `max_age=0`. Le premier demande au fournisseur
d'afficher son écran ; le second exige que la personne vienne d'être
authentifiée. La documentation d'Auth0 est explicite : `prompt=login` **ne
garantit pas** une nouvelle authentification lorsque l'identité vient d'un
fournisseur **amont** comme Google
(`https://auth0.com/docs/authenticate/login/max-age-reauthentication`).

Forcer un fournisseur amont n'est au pouvoir de personne ici. Mais `max_age`
oblige le jeton d'identité à porter la revendication `auth_time`, et Metreo la
lit désormais : il **constate** si la réauthentification a eu lieu, et le dit.
C'est la différence entre « le bouton ne marche pas » et « Google a repris
votre session, voici comment en sortir ».

**La connexion n'est jamais refusée pour autant** : vous entrez dans les deux
cas. Le bouton est un confort, pas une frontière de sécurité, et bloquer
fermerait Metreo à quiconque passe par un fournisseur qui n'honore pas
`max_age` — c'est-à-dire exactement la situation que ce contrôle sert à
détecter.

### D — Une adresse sans compte Metreo

Se connecter avec une adresse Google **qui n'a pas de compte** dans
l'organisation.

*Attendu* : refus, et le message doit nommer la cause — « aucun compte ne porte
cette adresse ». Le paramètre `login_error` doit valoir `unknown_user`.

### E — Revenir dans Metreo

Après une connexion réussie, fermer l'onglet, rouvrir l'application.

*Attendu* : la session est retrouvée sans repasser par le fournisseur.

## 4. Ce qu'il ne faut jamais transmettre

L'URL de retour contient `code` et `state`. **Ce sont des identifiants.** Ne
les recopiez nulle part, ne me les envoyez pas, ne les collez pas dans un
ticket.

Ce qui peut être transmis sans risque, et qui suffit au diagnostic :

- la valeur de `login_error` seule (`unknown_user`, `email_not_verified`,
  `account_disabled`, `no_membership`, `provider_refused`, `invalid_request`…) ;
- le message affiché à l'écran ;
- le fait que l'écran de Google soit apparu ou non.

## 5. La décision prise, et pourquoi

La réserve de ce document disait : « Metreo n'envoie ni `max_age`, ni ne lit
`auth_time` : il ne peut donc ni forcer la réauthentification, ni constater
qu'elle a eu lieu. C'est une décision à prendre. » Elle est prise.

**La seconde voie a été retenue** : `max_age=0` est envoyé avec le bouton
« utiliser un autre compte », et `auth_time` est lu au retour.

Restait la question que la réserve laissait ouverte — *un `auth_time` absent ou
trop ancien doit-il bloquer ou informer ?* **Il informe.**

| | Bloquer | Informer |
| --- | --- | --- |
| Ce que ça donne | la garantie qu'on n'entre qu'après une authentification fraîche | la personne entre, et sait pourquoi elle retombe sur le même compte |
| Ce que ça coûte | **Metreo devient inaccessible** à quiconque passe par un fournisseur amont qui n'honore pas `max_age` — c'est-à-dire exactement le cas que le contrôle sert à détecter | rien : la connexion suit son cours |

Le bouton « utiliser un autre compte » est un **confort**, pas une frontière de
sécurité. Rien dans Metreo ne dépend de la fraîcheur de l'authentification :
les droits viennent de l'appartenance en base, pas de l'âge d'un jeton.
Bloquer échangerait donc un risque qui n'existe pas contre une panne
d'accès bien réelle.

### Ce qui est vérifié automatiquement, et ce qui ne peut pas l'être

| Vérifié par un test | Ne peut pas l'être |
| --- | --- |
| `max_age=0` part avec le bouton, et seulement avec lui | que Google honore `max_age` |
| une connexion ordinaire n'envoie ni `prompt` ni `max_age` | |
| un `auth_time` antérieur à la demande rend « non réauthentifié » | |
| un `auth_time` absent rend « non réauthentifié » — jamais « oui » | |
| un `auth_time` frais rend « réauthentifié » | |
| la tolérance d'horloge joue dans les deux sens | |
| l'avis porte une phrase affichable, et aucun refus ne porte le même code | |

La colonne de droite est courte, et c'est elle qui justifie le scénario C :
seul un humain devant un vrai Google peut dire ce que Google fait.

### Si vous voulez quand même forcer le changement de compte

Il y a un moyen, et il n'est pas dans Metreo : se déconnecter de Google dans le
navigateur, ou utiliser une fenêtre de navigation privée. L'avis affiché par
Metreo le dit, parce que c'est l'action qui marche.

## 6. Tableau à remplir

| Scénario | Date | Ce qui s'est affiché | `login_error` ou avis | Conclusion |
| --- | --- | --- | --- | --- |
| A — première connexion | | | | |
| B — session déjà ouverte | | | | |
| C — changer de compte | | *Google a redemandé / avis bleu « reconnecté avec le compte déjà ouvert »* | | |
| D — adresse sans compte | | | `unknown_user` attendu | |
| E — retour dans Metreo | | | | |

Pour le scénario C, la colonne qui compte est la troisième : notez laquelle des
deux fins vous obtenez. Les deux sont des résultats, aucune n'est une panne.

Une seule ligne compte vraiment pour l'instant : **A**. Tant qu'elle n'est pas
remplie par un succès, Metreo n'a jamais été utilisé par un humain sur cette
machine, et tout le reste du produit reste hors d'atteinte.
