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

Ce qu'il achète, mesuré en le débranchant. Sans lui, PostgreSQL échoue au milieu
de la migration sur un `ForeignKeyViolation` qui ne nomme qu'une contrainte —
l'opérateur ignore combien de lignes, lesquelles des neuf relations, et quoi
faire. Sous SQLite, pire : `batch_alter_table` recopie la table sans vérifier, et
la migration **réussit silencieusement** en posant des contraintes par-dessus des
lignes qui les violent. Le préflight rend les deux moteurs lisibles et
identiques.

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

Mesuré, et plus subtil que la phrase ci-dessus. Un `SET NULL` posé sur la clé
composite **seule** échoue bien : `null value in column "organization_id" of
relation "boq_items" violates not-null constraint`. Mais si la clé simple existe
encore et que son déclencheur passe **avant**, elle a déjà mis `price_item_id` à
NULL ; le déclencheur composite ne trouve alors plus aucune ligne à modifier, et
la suppression réussit. L'ordre des déclencheurs `RI_` dépend de l'ordre de
création des contraintes, pas de leur nature.

Autrement dit : l'action référentielle composite n'est pas toujours fatale, elle
est **fatale ou inoffensive selon un ordre que personne ne contrôle**. C'est une
raison de plus de n'en poser aucune, pas une raison d'en poser une.

Symétriquement, la clé simple porte réellement quelque chose. Retirée,
`boq_items_price_item_id_fkey` ne pose plus NULL, et la clé composite — `NO
ACTION` — **refuse** la suppression du prix : `update or delete on table
"price_items" violates foreign key constraint "fk_boq_items_price_item_tenant"`.
Une relation optionnelle deviendrait bloquante. La coexistence des deux clés est
la conception, pas un reste.

## Verrous attendus

| Opération | Verrou |
| --- | --- |
| `ADD CONSTRAINT UNIQUE` | `ACCESS EXCLUSIVE` sur la table parente, le temps de construire l'index |
| `ADD CONSTRAINT FOREIGN KEY` | `SHARE ROW EXCLUSIVE` sur l'enfant **et** le parent, avec scan de l'enfant |

### Quatre stratégies, mesurées

Sur PostgreSQL 16, deux tables construites pour l'essai, 10 000 puis 100 000
lignes chacune, avec une écriture concurrente lancée pendant chaque phase et un
`lock_timeout` de 3 s pour distinguer « lente » de « bloquée ».

| Stratégie | 10 k | 100 k | Verrou maximal | Écriture pendant l'unicité | Pendant la validation |
| --- | --- | --- | --- | --- | --- |
| **A** — `ADD UNIQUE` + `ADD FK`, une transaction | 19 ms | 183 ms | `AccessExclusiveLock` | acceptée (34 ms) | **bloquée** (3 s, timeout) |
| **B** — `FK NOT VALID` + `VALIDATE`, même transaction | 21 ms | 124 ms | `AccessExclusiveLock` | acceptée (28 ms) | **bloquée** (3 s, timeout) |
| **C** — `CREATE UNIQUE INDEX CONCURRENTLY` + `USING INDEX` + `NOT VALID` + `VALIDATE` | 24 ms | 141 ms | aucun verrou fort | acceptée (25 ms) | acceptée (18 ms) |
| **D** — deux déploiements séparés | 21 ms | 141 ms | aucun verrou fort | acceptée (24 ms) | acceptée (19 ms) |

La durée ne départage rien : 183 ms au pire, à 100 000 lignes. Ce qui départage,
c'est la fenêtre pendant laquelle une écriture concurrente passe ou non.

**Ce que `NOT VALID` apporte, et ce qu'il n'apporte pas.** Dans la même
transaction (B), il n'apporte rien : Alembic exécute la révision d'un bloc, le
verrou est tenu jusqu'au commit, et l'écriture concurrente est bloquée
exactement comme en A. Dire « `NOT VALID` n'apporte rien » s'arrête là et serait
trop général : sorti de la transaction (C et D), il apporte précisément ce qu'on
lui demande — les écritures continuent de passer pendant la validation. La
mesure le montre : acceptée en 18 ms là où A et B tenaient 3 s jusqu'au timeout.

**Pourquoi A est gardée quand même.** Ce que C et D achètent, c'est la
disponibilité en écriture sur des tables volumineuses ; ce qu'ils coûtent est
plus lourd que ce gain à ce stade :

* `CREATE UNIQUE INDEX CONCURRENTLY` ne peut pas s'exécuter dans une
  transaction. La révision perd son atomicité — et l'interruption forcée montre
  (voir plus bas) que cette atomicité est ce qui rend la migration rejouable
  telle quelle. En cas d'échec, PostgreSQL laisse un **index INVALID** que
  personne ne supprime automatiquement : une reprise demande une intervention
  manuelle avant de relancer.
* D découpe en deux déploiements, ce qui rend le `downgrade` ambigu — de quel
  des deux états revient-on ?

Ce compromis est daté et sa condition est écrite : sur des tables où le scan de
validation cesse de se compter en millisecondes, ou si une fenêtre de blocage en
écriture devient inacceptable, **C est la bonne réponse** et la mesure ci-dessus
dit ce qu'elle rapporte. Aujourd'hui, atomicité et reprise valent plus que trois
secondes de blocage sur des tables de quelques milliers de lignes.

### Interruption en plein milieu

Vérifié en interrompant la révision après trois blocs sur neuf, sur PostgreSQL
réel : `alembic_version` reste à `e2be18fcac1b`, **zéro** clé composite et
**zéro** unicité composite subsistent, et relancer la migration telle quelle la
mène à 9 clés et 6 unicités. Rien à nettoyer, aucune étape à sauter.

## Coût, et ce qu'il achète

Les contraintes ont d'abord fait passer la suite SQLite d'environ 85 s à 231 s.
Ce n'était pas un coût à accepter mais une régression à diagnostiquer.

Deux causes, toutes deux traitées. La première tenait à la migration :
`batch_alter_table` recrée la table entière sur SQLite, et trois blocs sur
`boq_items` la recréaient trois fois. Pire, une table à la fois parente et
enfant — `bills_of_quantities`, `price_book_versions`, `price_items` — perdait
l'unicité posée dans un bloc précédent lors de sa propre recréation. Un seul
bloc par table, en ordre de dépendance, ramène 15 recréations à 9.

La seconde était plus ancienne et le surcoût des contraintes l'a seulement
rendue visible : chacun des six cents tests rejouait toute la chaîne des
migrations. Un gabarit SQLite est désormais construit **une fois** par session,
puis recopié pour chaque test.

L'isolation ne bouge pas — chaque test reçoit sa copie, un fichier distinct dans
son propre répertoire temporaire, jamais un fichier partagé ; trois tests le
vérifient, dont une écriture faite dans un test et cherchée dans le suivant. Les
migrations restent la source de vérité : le gabarit est leur produit, et le test
qui confronte le schéma migré aux modèles continue de tourner.

Mesuré depuis un clone neuf, trois exécutions consécutives : **62 s, 63 s,
62 s** — 603 passés, 30 ignorés. Sous les 85 s d'avant les contraintes, et loin
sous la cible de 150 s. Sur PostgreSQL réel, la suite complète fait 633 passés
en 191 s, sans un seul ignoré sur le jeu de fichiers de concurrence.

Ce coût achète une protection réelle sur les **deux** moteurs : `db.py` pose
`PRAGMA foreign_keys=ON`, et SQLite refuse donc aussi le lien croisé — sans
nommer la contrainte. Les tests exigent le nom sur PostgreSQL et se contentent
de la violation sur SQLite.

## Ce que les clés tiennent quand deux écritures se croisent

Les preuves ci-dessus montrent le refus d'un lien inter-tenant posé seul. Elles
ne disent rien de la fenêtre entre la lecture d'un parent et l'insertion de
l'enfant — précisément là où une protection applicative cède.

Cinq scénarios ne demandent aucune concurrence et valent sur les deux moteurs :
un lot dont une seule ligne traverse la frontière est refusé **en entier** et ne
laisse rien ; un import qui contourne les services est refusé comme le reste ;
une session portant deux organisations est légitime mais ne peut pas les mêler
dans une ligne ; et les deux moitiés d'un déplacement — la clé étrangère sans
l'organisation, l'organisation sans la clé — sont refusées, seul le changement
cohérent des deux passe.

Trois demandent deux transactions réellement simultanées et sont **réservés à
PostgreSQL**. SQLite sérialise les écritures sur un verrou de fichier : les y
faire tourner donnerait du vert sans rien démontrer, et ce qui est prouvé sous
PostgreSQL n'est pas présenté ici comme prouvé sous SQLite.

| Course | Résultat mesuré |
| --- | --- |
| Renommer le parent pendant l'insertion de l'enfant | les deux passent, sans attente |
| Déplacer le parent vers une autre organisation | le déplacement **attend**, puis est **refusé** par `fk_bills_of_quantities_project_tenant` |
| Supprimer le parent pendant l'insertion | la suppression **attend**, puis `SET NULL` s'applique à la ligne toute neuve ; `organization_id` reste rempli |

Le mécanisme est celui des modes de verrou. La vérification d'une clé prend un
`FOR KEY SHARE` sur la ligne parente. Une fois `uq_projects_id_organization`
posée, `organization_id` devient une **colonne de clé** : la modifier demande un
`FOR UPDATE`, incompatible, et le déplacement attend. Sans la clé composite, il
n'attendrait pas — le projet partirait chez Beta et le bordereau tout neuf
resterait chez Alpha en le pointant.

Le blocage n'est pas supposé : il est constaté dans `pg_locks` par sondage, puis
libéré. Aucun `sleep` n'arbitre ces tests ; un blocage qui ne vient pas les fait
échouer.

Le premier scénario compte autant que les deux autres : si toute vérification
composite sérialisait les modifications du parent, ces contraintes seraient
inutilisables sous charge.

## Ce qui a été tenté contre ces preuves

Huit falsifications, dans un clone jetable, tout restauré ensuite.

| Ce qu'on retire ou modifie | Ce qui tombe |
| --- | --- |
| Une clé composite (`fk_boq_items_price_item_tenant`) | 8 tests, dans 3 fichiers |
| `PRAGMA foreign_keys=ON` sous SQLite | 24 tests |
| L'unicité composite remplacée par un index **non** unique | la migration elle-même : `there is no unique constraint matching given keys` |
| Un second chemin de clé sans `foreign_keys=` explicite | `AmbiguousForeignKeysError` à la configuration des mappers |
| L'ancienne clé simple `ON DELETE SET NULL` | la suppression du prix devient **refusée** — une relation optionnelle deviendrait bloquante |
| Une action référentielle sur la clé composite | `NotNullViolation` sur `organization_id` — mais masquée si la clé simple s'exécute d'abord (voir plus haut) |
| Une interruption forcée au 4ᵉ bloc sur 9 | rien ne subsiste ; la relance aboutit |
| **L'appel au préflight** | **rien du tout, au premier essai** |

La dernière ligne est la trouvaille de ce travail. Le préflight était éprouvé
fonction par fonction, jamais branché : l'appel retiré, la suite restait
entièrement verte pendant que la migration posait ses contraintes sans avoir
rien regardé. Un garde qu'on peut débrancher sans qu'un seul test bronche n'est
pas un garde. Trois tests jouent maintenant la vraie migration sur une vraie
base incohérente, et deux tombent quand on la débranche.

## Ce qui reste ouvert — les quatorze relations

Vingt-trois relations traversent potentiellement la frontière ; neuf sont
couvertes. Voici les quatorze autres, qualifiées une par une.

### Sept vers `users` — hors du modèle

`memberships.user_id`, `projects.created_by`, `price_book_versions.created_by`,
`import_batches.created_by`, `estimates.created_by`,
`estimate_versions.frozen_by`, `estimate_versions.created_by`.

Elles ne relèvent pas du même problème et **ne doivent pas** recevoir de clé
composite vers `users`. Un utilisateur appartient légitimement à plusieurs
organisations : `users` n'a pas d'`organization_id`, et n'en aura pas. Exiger
`(user_id, organization_id) → users` serait faux par construction.

La question correcte est différente : l'auteur d'une ligne doit-il être **membre
de l'organisation** de cette ligne au moment où il écrit ? La référence serait
alors `(user_id, organization_id) → memberships`, pas `users`. C'est une règle
métier — que devient une ligne quand son auteur quitte l'entreprise ? — et pas
une contrainte à poser en passant. Toutes ces colonnes sont d'ailleurs nullables
sauf `memberships.user_id`, qui **est** la table de rattachement. Hors périmètre,
et à décider avec le métier.

### Sept vers des parents possédés par un tenant

| Enfant → parent | Nullable | Route d'écriture | Protection applicative | SQL croisé accepté ? | Classement |
| --- | --- | --- | --- | --- | --- |
| `composite_prices.price_book_version_id` → `price_book_versions` | non | prix composés | `get_owned` | oui | P2 |
| `composite_components.composite_price_id` → `composite_prices` | non | prix composés | `get_owned` | oui | P2 — ambiguïté ORM |
| `import_batches.price_book_version_id` → `price_book_versions` | non | import | `get_owned` | oui | P2 |
| `boq_items.parent_id` → `boq_items` | oui | **aucune** | sans objet | oui | P2 |
| `estimates.project_id` → `projects` | non | création de devis | `get_owned` + contrôle croisé 422 | oui | P2 |
| `estimate_versions.estimate_id` → `estimates` | non | versions de devis | `get_owned` | oui | P2 |
| `estimate_versions.price_book_version_id` → `price_book_versions` | non | versions de devis | `get_owned` | oui | P2 — ambiguïté ORM |

**Toutes les sept acceptent un parent d'une autre organisation en SQL direct** —
vérifié par un script construisant les lignes par l'ORM, sur une base créée pour
l'essai. Elles restent donc une exposition réelle, et « non démontrées
exploitables » ne veut pas dire « sûres ».

Aucune n'est cependant atteignable par une route. Les deux candidates les plus
plausibles ont été instruites : `boq_items.parent_id` n'apparaît dans aucun
schéma ni aucun routeur — le client ne peut pas le fournir ; et
`create_estimate` valide `project_id`, `boq_id` et `price_book_version_id` par
`get_owned`, plus un contrôle explicite que le bordereau appartient bien au
projet, sinon 422. D'où le classement **P2** — exposition en SQL interne, pas
P1 — et la décision de ne pas élargir les contraintes dans cette PR.

Deux d'entre elles coûtent plus que les autres :
`composite_components.composite_price_id` et
`estimate_versions.price_book_version_id` créeraient un **second chemin de clé**
entre deux tables déjà liées par une `relationship()`. Prototypé : sans
`foreign_keys=` explicite, SQLAlchemy refuse de configurer ses mappers
(`AmbiguousForeignKeysError`) ; avec un `foreign_keys=` posé des deux côtés, les
mappers se configurent, la relation charge et la cascade fonctionne. C'est donc
faisable — mais cela touche les modèles au-delà d'un ajout de contrainte, et
mérite sa propre revue.

## Comment rejouer

```bash
make migration-roundtrip-test METREO_ADMIN_DATABASE_URL=postgresql+psycopg://…/postgres
pytest apps/api/tests/test_tenant_db_integrity.py \
       apps/api/tests/test_tenant_preflight.py \
       apps/api/tests/test_tenant_referential_semantics.py \
       apps/api/tests/test_tenant_concurrency.py
```

Les quatre fichiers tournent sur SQLite comme sur PostgreSQL — les trois classes
de course de `test_tenant_concurrency.py` s'ignorant hors PostgreSQL, ce que
l'inventaire des ignorés partiels enregistre et qu'une étape de CI vérifie en
refusant tout résumé portant `skipped`. Sur PostgreSQL, ils
exigent le nom de la contrainte fautive : un refus venu d'ailleurs ne passe pas
pour une preuve.
