# Intégration du 28 août 2026 — quatre PR fusionnées dans `main`

Ce dossier enregistre **ce qui a été fusionné, dans quel ordre, et sur quelles
preuves**. Il ne décrit ni un déploiement ni une mise en production : aucun n'a
eu lieu, et rien ici ne le prépare.

## Ce qui a été fusionné

Quatre pull requests, exclusivement par **merge commits**, dans cet ordre.
Aucun squash, aucun rebase, aucun force-push, aucune branche supprimée.

| Rang | PR | Tête fusionnée | Commit de fusion | Diff propre |
| --- | --- | --- | --- | ---: |
| 1 | #1 — socle, moteur de calcul, tranche verticale | `64deb092d58b085792fde19e12396aa558d2533e` | `5c7e6e19aa11ec5bf1b54aa0dab3768295942294` | 166 fichiers |
| 2 | #8 — neuf relations multi-tenant | `4671ca4d477e0be8e760770d32cb1c96022a9bcc` | `a7c920bcece3b806a5e401bb61ecf49ca1f9343d` | 20 fichiers |
| 3 | #9 — sept relations de plus | `cfa3ed5bfab343ada536348a5a2dcf6041f7f5aa` | `02e283df54e72c78e9a6be67a31c0c47481cce6e` | 5 fichiers |
| 4 | #6 — fondations documentaires Phase 2A | `7de918177b5d24b26525cb8b597acf4833b9f3ca` | `c74906ce5f68fd9035d60fc530e86d78abf5f78d` | 29 fichiers |

**`main` final : `c74906ce5f68fd9035d60fc530e86d78abf5f78d`.**

Chaque PR a été reciblée vers `main` **après** la fusion de la précédente, sa CI
relancée sur la nouvelle base, et exigée verte — dix jobs sur dix — avant d'être
passée en `ready` puis fusionnée.

### Les quatre branches sont conservées

`claude/new-session-jdj11s`, `claude/phase1-tenant-db-integrity`,
`claude/phase1-tenant-db-integrity-part2` et `claude/phase2-document-foundation`
existent toujours, à leurs têtes fusionnées. Aucune n'a été supprimée.

### `main` n'était pas protégé

Relevé au préflight : la branche `main` de ce dépôt ne portait **aucune règle de
protection** — ni contrôle requis, ni revue requise, ni restriction de poussée.
L'ordre d'intégration n'était donc imposé par rien d'autre que la procédure
suivie. C'est un fait à connaître avant la prochaine intégration, et un candidat
évident au durcissement.

## Chaîne Alembic finale

Une seule tête, aucune branche :

```
d88792b38c2d → c6526f663ff3 → 105f11dede7e → e2be18fcac1b
             → 7c1e4a9b2d30 → b4f2c7d81a05 → c9d3a5e71b62 → a7e5c04b93f8
```

26 tables, 23 clés étrangères composites dont 16 tenant, 8 unicités parentes.

### Bases locales à recréer

La révision documentaire a **changé d'identifiant** pendant la préparation :
`4d7c9a2e6f10` est devenu `a7e5c04b93f8`, et elle descend maintenant de la
chaîne multi-tenant au lieu d'ouvrir une seconde tête.

C'était nécessaire, et mesuré : en gardant l'ancien identifiant, une base l'ayant
déjà appliqué était considérée **à jour en silence** — `alembic upgrade head`
rendait zéro révision appliquée et le code 0 — alors qu'il lui manquait seize
clés composites tenant. Avec le nouvel identifiant, la même base échoue sur
`Can't locate revision identified by '4d7c9a2e6f10'`, code 255, sans rien
modifier.

**Doivent donc être recréées** — aucune n'existe en production, la Phase 2A
n'ayant jamais été déployée :

| État de la base locale | Comportement | Action |
| --- | --- | --- |
| ayant appliqué `4d7c9a2e6f10` | code 255, diagnostic explicite, rien modifié | **recréer** |
| deux têtes (`e2be18fcac1b` + `4d7c9a2e6f10`) | code 255, rien modifié | **recréer** |
| deux lignes dans `alembic_version` | code 255, rien modifié | **recréer** |
| vide, ou à `e2be18fcac1b` / `b4f2c7d81a05` / `c9d3a5e71b62` | monte jusqu'à `a7e5c04b93f8` | rien à faire |
| portant une unicité `uq_project_org_id` posée à la main | monte ; l'unicité surnuméraire subsiste, sans effet | facultatif |

## Preuves sur le `main` final

Depuis un **clone entièrement neuf** de `c74906c`, `make install` puis
`make release-gate` avec un PostgreSQL 16 jetable : **code 0 en 7 min 53 s,
rien d'ignoré**.

| Contrôle | Résultat |
| --- | ---: |
| Domaine pur | **127 réussis** |
| Contrats documentaires purs | **22 réussis** |
| API SQLite | **736 réussis, 48 ignorés** |
| API PostgreSQL 16 réel | **780 réussis, 4 ignorés** |
| Garde Next, installation exigée | **24/24, aucun ignoré** — 15.5.24 posé sur disque |
| Lint, format | verts |
| Quatre passes mypy | 7 + 6 + 32 + 7 fichiers, aucun problème |
| `alembic heads` | une tête `a7e5c04b93f8` |
| `alembic branches` | aucune |
| Porte de dérive de schéma (PostgreSQL) | une tête, montée propre, **aucune opération proposée** |
| Aller-retour des migrations | base → head → base → head, **26 tables** |
| Installation propre depuis les manifestes | 38 chemins, 56 schémas, 35 distributions, 52 exigences |
| Skills du dépôt | 8 conformes |
| Composition Docker | valide |
| Parcours navigateur (Playwright) | **15 réussis** |
| Concurrence, intégrité multi-tenant, documentaire (PostgreSQL ciblé) | **163 réussis** |

CI de `main` après chaque fusion, dix jobs sur dix :
[#1](https://github.com/Hermesprojet/Devis/actions/runs/33209801303) ·
[#8](https://github.com/Hermesprojet/Devis/actions/runs/33211370660) ·
[#9](https://github.com/Hermesprojet/Devis/actions/runs/33211718625) ·
[#6](https://github.com/Hermesprojet/Devis/actions/runs/33212168553).

**Non vérifié localement :** la construction des deux images Docker — aucun
démon Docker n'est disponible sur la machine de contrôle. Le job « Images
Docker » de la CI la couvre, et il est vert sur les quatre commits de fusion.

## Compteurs, et pourquoi ils sont reproductibles

Les compteurs ci-dessus valent pour `c74906c` **après `make install`**. Un seul
d'entre eux dépendait auparavant de l'état de la machine : le contrôle « le
paquet Next réellement posé est conforme » s'ignorait sans `node_modules`, ce qui
donnait 24 réussites ici et 23 plus un ignoré sur un clone neuf. Trois états sont
désormais distincts — rien d'installé, installation partielle, et
`METREO_REQUIRE_WEB_INSTALL=1` qui transforme l'ignoré en erreur. `make
release-gate` pose la variable.

## Statuts

| Statut | État | Ce qui manque |
| --- | --- | --- |
| `FUNCTIONALLY_COMPLETE` | ✅ | — |
| `DEPLOYABLE` | ✅ **OUI** | — |
| `PRODUCTION_READY` | ❌ **NON** | voir ci-dessous |

`DEPLOYABLE` dit que rien de connu ne manque dans les dépendances et que
l'application se construit et se lance. Il ne dit pas que le produit doit partir
en production, et **aucun déploiement n'a eu lieu**.

Manquent pour `PRODUCTION_READY`, tous vérifiables comme absents :
authentification réelle (mode développement seul), gestion des secrets
(variables d'environnement nues), sauvegardes et restauration **testées**,
supervision et alertes, politique d'incidents, validation juridique des packs
régionaux — les quatre packs semés sont en `draft` ou `planned`.

## Décisions humaines toujours ouvertes

Aucune ne bloquait la fusion technique ; toutes bloquent un déploiement.

1. Authentification réelle — fournisseur, MFA, SSO.
2. Gestion et rotation des secrets.
3. Sauvegardes **et** restauration éprouvée.
4. Supervision, seuils, alertes, astreinte.
5. Politique d'incident.
6. Validation juridique des packs régionaux par un spécialiste.
7. Bornes numériques métier — quantité 10⁹, prix unitaire 10⁹, total 10¹², masse volumique 30 000 kg/m³.
8. Précision `NUMERIC(28, 10)` — dix décimales conservées, arrondi à l'affichage.
9. Ordre des majorations — déboursé sec → frais de chantier → frais généraux → prix de revient → aléas → marge.
10. Marge sur coût ou sur prix de vente.
11. Règles d'historisation des appartenances utilisateur — cf. `TENANT_USER_REFERENCES.md`.
12. Traitement juridique de `actor_email` dans la chaîne d'audit.

Deux échéances, distinctes des décisions : la borne haute de la ligne Next
(`<15.6`) devra être rouverte quand la branche 15.5 cessera d'être maintenue, et
la protection de `main` reste à mettre en place.
