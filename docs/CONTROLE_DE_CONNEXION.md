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

**Le fournisseur simulé est un bon outil** : il prouve que notre moitié du
protocole est correcte, et il la prouve à chaque commit. Il ne peut rien dire
de la moitié qui appartient à Auth0 et à Google. Les deux moitiés sont
nécessaires, aucune ne remplace l'autre.

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

*Attendu* : Auth0 présente à nouveau son écran. **Observez précisément ce qui
se passe ensuite** — c'est le point à vérifier, et la réponse n'est pas acquise
d'avance :

- l'écran propose-t-il de choisir un autre compte Google, ou
- reprend-il silencieusement le compte déjà connecté ?

**Pourquoi ce doute est légitime.** Le bouton transmet `prompt=login`. Ce
paramètre demande au fournisseur d'afficher à nouveau son écran — mais la
documentation d'Auth0 est explicite : il **ne garantit pas** une nouvelle
authentification lorsque l'identité vient d'un fournisseur **amont** comme
Google. Le mécanisme qui l'impose est `max_age`, dont le résultat se vérifie
ensuite dans la revendication `auth_time` du jeton d'identité
(`https://auth0.com/docs/authenticate/login/max-age-reauthentication`).

Metreo n'envoie aujourd'hui **ni `max_age`**, et ne lit **pas `auth_time`** :
il ne peut donc ni forcer la réauthentification, ni constater qu'elle a eu
lieu. C'est une décision à prendre, pas un défaut à corriger en silence — voir
§ 5.

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

## 5. La décision qui reste à prendre

Si le scénario C montre que Google est repris sans rien demander, deux voies :

| Voie | Ce qu'elle donne | Ce qu'elle coûte |
| --- | --- | --- |
| **Laisser `prompt=login` seul** | le bouton existe, Auth0 réaffiche son écran | aucune garantie sur le compte amont ; « changer de compte » peut rester sans effet |
| **Ajouter `max_age` et vérifier `auth_time`** | la réauthentification est demandée, et Metreo peut **constater** si elle a eu lieu au lieu de l'espérer | une modification du chemin d'authentification, à écrire et à éprouver ; un `auth_time` absent ou trop ancien doit alors être traité, et il faut décider s'il bloque ou s'il informe |

Tant que la seconde voie n'est pas choisie, le document doit continuer à dire
ce qu'il dit ici : **le bouton demande, il ne garantit pas.**

## 6. Tableau à remplir

| Scénario | Date | Ce qui s'est affiché | `login_error` | Conclusion |
| --- | --- | --- | --- | --- |
| A — première connexion | | | | |
| B — session déjà ouverte | | | | |
| C — changer de compte | | | | |
| D — adresse sans compte | | | | |
| E — retour dans Metreo | | | | |

Une seule ligne compte vraiment pour l'instant : **A**. Tant qu'elle n'est pas
remplie par un succès, Metreo n'a jamais été utilisé par un humain sur cette
machine, et tout le reste du produit reste hors d'atteinte.
