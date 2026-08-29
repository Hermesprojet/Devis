# Ce que l'interface propose, et ce que l'API refuse

Portée : la page « estimation » du client web et les refus d'autorisation que
l'API lui renvoie. Ce document enregistre un écart mesuré, la décision prise, et
ce qui a été fait. Les constats ci-dessous décrivent l'état **avant**
correction.

L'API n'est pas en cause. Elle refuse ce qu'elle doit refuser, avec le bon code
et le bon message. Ce qui est en cause, c'est que l'interface n'en tient aucun
compte.

## Le constat

La barre d'outils de `estimations/[estimateId]/[versionId]` porte quatre
commandes. Une seule est conditionnée à un droit — et encore, indirectement, par
un booléen que le serveur calcule (`includes_internal_costs`). Les trois autres
sont conditionnées à l'état de l'objet, ou à rien.

Mesuré sur le jeu de démonstration, en appelant chaque commande avec le jeton de
chaque rôle semé :

Ce que l'**API** répond :

| Rôle | Exporter CSV | Export interne | Aperçu du devis | Geler la version |
| --- | --- | --- | --- | --- |
| `org_admin` | 200 | 200 | 200 | 409 *(refus métier)* |
| `estimator` | 200 | 200 | 200 | **403** |
| `viewer` | **403** | **403** | **403** | **403** |

Ce que l'**interface** affiche, pour les mêmes rôles :

| Rôle | Exporter CSV | Export interne | Aperçu du devis | Geler la version |
| --- | --- | --- | --- | --- |
| `org_admin` | affiché | affiché | affiché | affiché |
| `estimator` | affiché | affiché | affiché | **affiché → 403** |
| `viewer` | **affiché → 403** | masqué | **affiché → 403** | **affiché → 403** |

Quatre boutons offerts par l'interface et refusés par l'API. Le seul refus qui
n'atteint jamais l'utilisateur — l'export interne du lecteur — est celui dont la
condition d'affichage vient du serveur.

Pour un `viewer`, le rôle que l'application nomme « Lecteur / auditeur », les
trois boutons visibles mènent tous à un refus.

## La cause

`/auth/me` renvoie déjà la liste exacte des permissions du porteur du jeton.
`apps/web/src/lib/api.ts` la déclare dans le type `Me` :

    permissions: string[]

C'est la seule occurrence de ce champ dans tout `apps/web/src`. Il est reçu,
typé, et **lu nulle part**. L'interface n'a donc aucun moyen d'anticiper un
refus, alors que le serveur le lui a dit.

## Trois conséquences distinctes *(corrigées — voir « La décision prise »)*

### 1. Le motif du refus est perdu au téléchargement

L'API refuse précisément :

    403  {"code": "permission_denied",
          "message": "Action non autorisée pour ce rôle.",
          "required_permission": "export:client",
          "role": "viewer"}

`ErrorNotice` sait afficher `required_permission` — le code est là. Mais le
téléchargement d'export ne passe pas par `request()` : il refait son `fetch` et
lève une `Error` nue, pas une `ApiError`. `ErrorNotice` tombe alors dans sa
branche générique et affiche « Erreur : Erreur HTTP 403 ». La permission
manquante, que l'API a nommée, n'est jamais montrée.

### 2. Une session expirée est annoncée, mais rien n'en tire les conséquences

Le jeton expiré est traité correctement par l'API — mesuré sur trois endpoints :

    401  {"code": "token_expired", "message": "Session expirée."}

Une page qui lit par `request()` reçoit donc une `ApiError` dont le message est
« Session expirée. », et `ErrorNotice` l'affiche. Mais :

  - `Shell` est le seul endroit qui efface la session et renvoie à l'écran de
    connexion, et il ne le fait qu'au montage, sur son appel à `/auth/me` ;
  - une expiration survenue **pendant** que l'utilisateur est sur une page
    tombe dans le `catch` de cette page, qui se contente de `setError`. La
    session n'est pas effacée, il n'y a pas de redirection, et l'utilisateur
    reste sur une page dont les données sont périmées jusqu'à ce qu'il recharge
    de lui-même.

### 3. Une erreur de validation ne dit pas quel champ

Sur un 422, FastAPI renvoie une `detail` qui est une **liste** de problèmes de
champ. `ApiError` y cherche `.message`, ne le trouve pas, et retombe sur
« Erreur HTTP 422 ». Le nom du champ fautif, que le serveur a fourni, n'est pas
affiché.

C'est le seul des trois points qui ne concerne pas les droits ; il est ici
parce qu'il a la même cause : une forme d'enveloppe que le client ne sait pas
lire.

Pour mémoire, l'enveloppe est par ailleurs cohérente là où l'application la
contrôle — mesuré sur neuf cas :

| Forme de `detail` | Cas |
| --- | --- |
| `{code, message, …}` | 401 sans jeton, 401 jeton invalide, 403, 404 objet, 409 métier |
| chaîne | 404 route inconnue, 405 méthode *(défauts du framework)* |
| liste | 422 *(validation FastAPI)* |

## La décision prise

**Masquer ce qui ne pourra jamais aboutir, désactiver avec explication ce qui
attend un changement d'état.** L'API reste l'autorité : elle refuse toujours,
et c'est la deuxième barrière. L'interface cesse seulement de proposer ce qui
ne peut pas marcher.

Les deux causes d'indisponibilité sont distinguées, parce qu'elles n'appellent
pas la même réponse :

| Cause | Traitement | Pourquoi |
| --- | --- | --- |
| le rôle n'a pas la permission | **masqué** | la montrer désactivée n'apprend rien d'actionnable — le rôle ne l'obtiendra pas en cliquant ailleurs |
| l'objet ne s'y prête pas (version déjà gelée) | **désactivé, avec l'explication** | la commande existe et redeviendra disponible ; l'utilisateur doit le savoir |

### Ce que ça change, concrètement

| Rôle | Exporter CSV | Export interne | Aperçu du devis | Geler la version |
| --- | --- | --- | --- | --- |
| `org_admin` | affiché | affiché | affiché | affiché *(désactivé si déjà gelée)* |
| `estimator` | affiché | affiché | affiché | **masqué** |
| `viewer` | **masqué** | masqué | **masqué** | **masqué** |

### Comment

`apps/web/src/lib/permissions.ts` décide, `usePermissions` fournit la liste que
`/auth/me` renvoie déjà. **La table des permissions n'est pas recopiée côté
web** : recopier `ROLE_PERMISSIONS` produirait deux vérités qui divergeraient
au premier rôle modifié. Le serveur dit ce que le porteur du jeton peut faire ;
le web ne fait que le lire.

Trois corrections viennent avec :

1. **le motif du refus n'est plus perdu.** Le téléchargement passe par
   `api.fetchExport`, qui lève une `ApiError` comme le reste du client :
   `ErrorNotice` retrouve le `required_permission` que l'API fournit ;
2. **une session expirée met fin à la session, où qu'elle soit constatée.**
   `request` et `fetchExport` effacent la session et renvoient à la connexion
   sur `401 token_expired` — et sur ce code seulement. Un `403`, ou un `401`
   d'une autre cause, laisse la session intacte : elle est valide, c'est
   l'action qui ne l'est pas. `Shell` faisait l'inverse : il traitait `403`
   comme `401` et aurait déconnecté un utilisateur parfaitement authentifié ;
3. **un 422 nomme le champ.** `detail` est une liste chez FastAPI ; `ApiError`
   la traduit en problèmes de champ, et `ErrorNotice` les affiche au lieu de
   « Erreur HTTP 422 ».

## Ce qui est vérifié par un test

`apps/api/tests/test_web_rights_alignment.py` fixe le côté API : le tableau des
refus par rôle et par commande, et le code renvoyé sur jeton expiré. Ces refus
sont la deuxième barrière, et ils doivent rester exactement ce qu'ils sont.

`apps/web/e2e/parcours.spec.ts` fixe le côté web : trois tests, un par rôle,
qui vérifient ce que la barre d'outils propose à `org_admin`, `estimator` et
`viewer` — plus un quatrième qui appelle l'API directement avec le jeton du
lecteur, **sans passer par l'interface**, pour montrer que masquer un bouton
n'est pas ce qui protège.

Un test antérieur du même fichier — « un métreur ne peut pas geler » — ne
distinguait pas les deux causes d'absence du bouton : la permission manquante,
et la version déjà gelée par un test qui le précède. Il passait donc sans rien
prouver sur les permissions. Il est conservé comme garde de non-régression, et
son commentaire le dit.
