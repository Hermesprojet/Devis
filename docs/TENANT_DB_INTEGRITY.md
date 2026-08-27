# Intégrité multi-tenant tenue par la base — Phase 1

Ce dossier ne parle que de la PR qui le porte. Il ne modifie ni ne commente le
statut de la PR #1 : `DEPLOYABLE` y reste atteint, `PRODUCTION_READY` non, et
rien ici ne change cela.

## Ce qui était ouvert

Le modèle de la Phase 1 portait 39 clés étrangères simples et **aucune**
`ForeignKeyConstraint` composite. Rien ne liait l'organisation d'une ligne à
celle de son parent.

Mesuré sur PostgreSQL 16, dans une base créée pour l'expérience et détruite
après : **neuf relations croisées sur neuf** entre deux organisations acceptées
par des `INSERT` directs.

Quatre refus lors d'un premier passage étaient des faux positifs — contraintes
`kind`, `status` et `component_type` violées par un remplissage générique, sans
rapport avec le tenant. Corrigés, la base n'opposait plus rien.

Les routes tenaient la frontière — elles répondent 404 — mais rien d'autre. Un
script d'exploitation, une correction manuelle en base ou un import mal écrit
passaient au travers, et le calcul produisait ensuite un montant tiré des
tarifs de quelqu'un d'autre.

## Contraintes posées

Six unicités `(id, organization_id)` :

| Table parente | Contrainte |
| --- | --- |
| `projects` | `uq_projects_id_organization` |
| `bills_of_quantities` | `uq_bills_of_quantities_id_organization` |
| `price_books` | `uq_price_books_id_organization` |
| `price_book_versions` | `uq_price_book_versions_id_organization` |
| `price_items` | `uq_price_items_id_organization` |
| `composite_prices` | `uq_composite_prices_id_organization` |

Neuf clés étrangères composites :

| Enfant → parent | Contrainte |
| --- | --- |
| `bills_of_quantities.project_id` → `projects` | `fk_bills_of_quantities_project_tenant` |
| `price_book_versions.price_book_id` → `price_books` | `fk_price_book_versions_price_book_tenant` |
| `price_items.price_book_version_id` → `price_book_versions` | `fk_price_items_price_book_version_tenant` |
| `boq_items.boq_id` → `bills_of_quantities` | `fk_boq_items_boq_tenant` |
| `boq_items.price_item_id` → `price_items` | `fk_boq_items_price_item_tenant` |
| `boq_items.composite_price_id` → `composite_prices` | `fk_boq_items_composite_price_tenant` |
| `composite_components.price_item_id` → `price_items` | `fk_composite_components_price_item_tenant` |
| `estimates.boq_id` → `bills_of_quantities` | `fk_estimates_boq_tenant` |
| `estimates.price_book_version_id` → `price_book_versions` | `fk_estimates_price_book_version_tenant` |

Révision Alembic `7c1e4a9b2d30`, sur `e2be18fcac1b`.

## Ce que la migration ne fait pas

Elle ne corrige aucune donnée. Un préflight interroge chaque relation
séparément avant toute écriture ; s'il trouve une incohérence, il s'arrête en
nommant la relation et le nombre de lignes, et donne la procédure. Rien d'autre
que des compteurs ne sort : ni identifiant, ni référence de projet, ni nom de
client n'atteint un journal de migration.

Réattribuer une ligne à une autre organisation ou la rattacher à un autre
parent change un montant de devis. C'est une décision d'exploitation.

Un test interdit `UPDATE`, `DELETE`, `INSERT` et `op.execute` dans la révision.

## Actions référentielles — le piège

Un `ON DELETE SET NULL` composite tenterait de vider aussi `organization_id`,
qui est NOT NULL : la suppression échouerait. Les clés composites ne portent
donc **aucune** action référentielle. Les clés simples gardent les leurs : elles
posent NULL sur le parent, puis la clé composite — `MATCH SIMPLE` — ne vérifie
plus rien, une colonne étant NULL.

Les deux chemins sont couverts : supprimer un prix met la référence à NULL sans
vider l'organisation ; supprimer un bordereau emporte bien ses lignes.

## Verrous attendus

| Opération | Verrou |
| --- | --- |
| `ADD CONSTRAINT UNIQUE` | `ACCESS EXCLUSIVE` sur la table parente, le temps de construire l'index |
| `ADD CONSTRAINT FOREIGN KEY` | `SHARE ROW EXCLUSIVE` sur l'enfant **et** le parent, avec scan de l'enfant |

`NOT VALID` puis `VALIDATE CONSTRAINT` a été évalué et **écarté**. Alembic
exécute la révision dans une seule transaction : le verrou est de toute façon
tenu jusqu'au commit, et la séparation n'apporte rien. Elle ne vaudrait que
découpée en deux déploiements distincts, ce qui rendrait le `downgrade` ambigu.
Sur des tables de la taille actuelle, le scan se compte en millisecondes ; sur
un volume important, prévoir une fenêtre.

## Coût, et ce qu'il achète

La suite SQLite passe d'environ 85 s à 231 s. Le surcoût est entièrement dans
la migration : `batch_alter_table` recrée chaque table enfant, et chaque test
rejoue la chaîne complète. Un seul bloc par table en ordre de dépendance ramène
15 recréations à 9 — trois blocs sur `boq_items` la recréaient trois fois, et
une table à la fois parente et enfant perdait l'unicité posée dans un bloc
précédent lors de sa propre recréation.

Ce coût achète une protection réelle sur les **deux** moteurs : `db.py` pose
`PRAGMA foreign_keys=ON`, et SQLite refuse donc aussi le lien croisé — sans
nommer la contrainte. Les tests exigent le nom sur PostgreSQL et se contentent
de la violation sur SQLite.

## Ce qui reste ouvert

Quatorze des vingt-trois relations tenant-croisantes ne sont pas couvertes.
Elles n'ont pas été démontrées exploitables, et plusieurs pointent `users`, où
un utilisateur appartient légitimement à plusieurs organisations.

Deux méritent une décision séparée : `composite_components.composite_price_id`
et `estimate_versions.price_book_version_id` créeraient un second chemin de clé
entre deux tables déjà liées par une `relationship()`, qui exigerait alors un
`foreign_keys=` explicite. Les couvrir est possible ; ce n'est pas gratuit.

## Comment rejouer

```bash
make migration-roundtrip-test METREO_ADMIN_DATABASE_URL=postgresql+psycopg://…/postgres
pytest apps/api/tests/test_tenant_db_integrity.py apps/api/tests/test_tenant_preflight.py
```

Les deux fichiers tournent sur SQLite comme sur PostgreSQL. Sur PostgreSQL, ils
exigent le nom de la contrainte fautive : un refus venu d'ailleurs ne passe pas
pour une preuve.
