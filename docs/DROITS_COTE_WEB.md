# Ce que l'interface propose, et ce que l'API refuse

Portée : la page « estimation » du client web et les refus d'autorisation que
l'API lui renvoie. Ce document constate un écart mesuré et pose la décision.
**Il ne la tranche pas** : les corrections possibles changent ce que voit
l'utilisateur, et ce choix relève du produit.

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

## Trois conséquences distinctes

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

## La décision à prendre

### A — L'interface lit les permissions

Chaque commande est conditionnée à la permission que l'API exige. Le lecteur ne
voit que ce qu'il peut faire.

Conséquence : il faut décider **masquer ou désactiver**. Masquer donne une
interface propre mais l'utilisateur ne sait pas qu'une fonction existe et qu'il
lui manque un droit. Désactiver avec une infobulle le lui dit, au prix d'une
barre d'outils encombrée de boutons éteints. Ce n'est pas un choix technique.

Il faut aussi décider si la table des permissions par commande vit côté web —
au risque de diverger de `ROLE_PERMISSIONS` — ou si l'API l'expose.

### B — L'interface n'anticipe rien, mais explique le refus

Les boutons restent tous visibles ; quand l'API refuse, l'utilisateur lit
exactement pourquoi et quelle permission lui manque.

Bien moins de travail que A : il suffit que le téléchargement lève une
`ApiError` comme le reste du client. Mais l'utilisateur découvre le refus après
avoir cliqué.

### C — Ne rien changer

L'état actuel. Un lecteur voit trois boutons dont aucun ne marche, et un
message qui ne dit pas pourquoi.

## Ce qui ne dépend d'aucune de ces options

La redirection sur session expirée. Que l'on choisisse A, B ou C, une `401
token_expired` reçue en cours de navigation devrait effacer la session et
ramener à l'écran de connexion, comme `Shell` le fait déjà au montage. La seule
question ouverte est de savoir si l'on préserve le travail en cours avant de
rediriger.

## Ce qui est vérifié par un test

`apps/api/tests/test_web_rights_alignment.py` fixe le côté API de ce constat :
le tableau des refus par rôle et par commande, et le code renvoyé sur jeton
expiré. Ce test ne dit pas que le comportement du **web** est juste — il ne
touche pas au web. Il dit que les refus mesurés sont ceux-là, et que le document
ci-dessus ne peut pas se périmer en silence.
