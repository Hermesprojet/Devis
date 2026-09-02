# RLS PostgreSQL — ce qu'elle ajouterait, ce qu'elle coûte, ce qu'elle casse

> **Note de décision. Aucun schéma modifié, aucune migration écrite.** Tout ce
> qui suit est mesuré sur le schéma réel, dans des bases et avec un rôle créés
> et détruits par la mesure. Les décisions à prendre sont en fin de document ;
> elles ne sont pas techniques.

La note de suivi de la Phase 1 porte un P2 d'une ligne : « RLS PostgreSQL non
activée — l'isolation est applicative ». C'est le dernier P2 ouvert. Cette note
le remplace par des chiffres.

## 1. Ce que RLS protégerait, et qui n'est pas protégé par elle aujourd'hui

L'isolation actuelle repose sur `owned_query` / `find_owned` / `get_owned`, qui
posent le filtre d'organisation pour qu'on ne puisse pas l'oublier. Mais toutes
les lectures ne passent pas par eux. Relevé par lecture d'AST sur les routeurs
et les services — l'instruction entière, car `select(...).where(...)` est une
chaîne d'appels et le `.where` n'est pas un enfant du `select` :

| Catégorie | Nombre |
| --- | ---: |
| instructions portant un `select(` | **33** |
| filtrant `organization_id` explicitement | 25 |
| bâties sur une variable déjà filtrée — les `count()` sur sous-requête | 3 |
| visant une table sans organisation (`users`, `region_profiles`) | 2 |
| **non rattachées à une organisation** | **3** |

> Un premier comptage, plus grossier, en annonçait cinq. Il traitait les trois
> `count()` sur sous-requête comme non rattachés — ils le sont, par la variable
> qu'ils reprennent — et manquait une lecture d'appartenances dans `dev_login`.
> Les chiffres ci-dessus sont ceux de l'analyse finale, celle qui tourne dans la
> suite de tests.

Les trois, une par une :

| Emplacement | Ce que c'est | Pourquoi c'est sûr aujourd'hui |
| --- | --- | --- |
| `routers/auth.py::dev_login` | lit les appartenances d'un utilisateur **toutes organisations confondues** | c'est voulu : la question « à quelles organisations appartient-il ? » précède le choix d'une organisation et ne peut pas être filtrée par elle. Bornée à l'utilisateur qui vient de s'authentifier |
| `services/estimating.py::next_version_number` | lit les numéros de version d'un devis par `estimate_id` seul | **deux garanties, toutes deux extérieures à la requête** |
| `services/pricebook_versions.py::next_version_number` | idem, par `price_book_id` seul | idem |

La première est le seul endroit du code où une lecture franchit légitimement la
frontière inter-tenant. C'est une propriété qui mérite d'être nommée plutôt que
découverte : elle l'est désormais, dans `test_tenant_query_discipline.py`.

Les deux autres sont exactement la forme du défaut corrigé dans la PR #8 sur
l'index de tri d'un bordereau : un numéro calculé à partir d'un identifiant de
parent, sans filtrer l'organisation. Elles sont sûres pour deux raisons, et
aucune des deux n'est dans la requête :

* `lock_owned(session, Parent, organization_id, parent_id)` est appelé **avant**,
  et rend 404 si le parent n'appartient pas à l'appelant ;
* depuis les PR #8 et #9, la clé composite `fk_estimate_versions_estimate_tenant`
  interdit qu'un enfant d'un parent appartienne à une autre organisation — quatre
  tests existants le prouvent, verts sur PostgreSQL 16.

**C'est là tout l'argument pour RLS, et il n'est pas plus fort que cela.** Ces
requêtes ne sont pas exploitables ; elles sont sûres par deux mécanismes qui
pourraient chacun régresser sans que la requête change. RLS serait une troisième
garantie, indépendante des deux autres, et elle vaudrait aussi pour la
trente-quatrième requête que personne n'a encore écrite.

## 2. Ce que RLS fait, mesuré

Base montée par les migrations réelles, 1 000 projets répartis sur deux
organisations, politique `USING (organization_id = current_setting('app.organization_id', true))` :

| Situation | Lignes visibles |
| --- | ---: |
| aucune organisation posée | **0 sur 1 000** |
| organisation d'Alpha posée | 500 |
| organisation de Beta posée | 500 |
| `SELECT ... WHERE reference LIKE 'B-%'` — la requête sans filtre — vue par Alpha | **0** |
| `INSERT` chez Beta pendant qu'Alpha est posée | **refusé** : `new row violates row-level security policy` |

Elle **ferme par défaut** : sans organisation, on ne voit rien. C'est la bonne
direction pour un défaut de configuration.

## 3. Ce qu'elle coûte, mesuré

| Requête | Avec RLS | Sans RLS, filtre écrit à la main |
| --- | ---: | ---: |
| `count(*)` sur une table, 200 exécutions | 0,325 ms | 0,315 ms |
| jointure `bills_of_quantities` × `projects`, 200 exécutions après chauffe | **6,364 ms** | **6,378 ms** |

Sur une jointure, l'écart est dans le bruit : le prédicat que RLS ajoute est
celui qu'on écrivait déjà. **Le coût n'est pas l'argument contre.**

## 4. Ce qu'elle exige — et le premier point est éliminatoire

**Le rôle applicatif ne doit être ni superuser ni `BYPASSRLS`.** Mesuré, et ce
n'était pas une supposition : avec le rôle actuel — `metreo`, `rolsuper = t` sur
la machine de contrôle — la politique posée et `FORCE ROW LEVEL SECURITY` activé,
**1 000 lignes sur 1 000 restaient visibles sans aucune organisation posée**. Un
superuser ignore RLS, et `FORCE` ne s'y applique pas. Toute la mécanique est
inerte tant que ce point n'est pas réglé.

Il en découle une séparation de rôles qui n'existe pas aujourd'hui :

* un rôle **propriétaire**, qui possède les tables et applique les migrations ;
* un rôle **applicatif**, non privilégié, distinct, celui que l'API utilise ;
* `FORCE ROW LEVEL SECURITY` sur chaque table, sans quoi le propriétaire — donc
  les migrations, mais aussi tout script lancé avec ses identifiants —
  contournerait la politique.

**La valeur doit être posée par transaction, jamais par session.** Mesuré sur un
pool d'une connexion, ce qui est exactement le cas où la fuite se voit :

| Forme | Requête suivante, sans rien poser |
| --- | --- |
| `set_config(..., false)` — portée session | organisation vue = celle de la requête précédente, **300 lignes** |
| `set_config(..., true)` — portée transaction | organisation vue = `''`, **0 ligne** |

La forme session **fuit d'un tenant vers le suivant** dès qu'une connexion est
rendue au pool et reprise par une autre requête. C'est un défaut *introduit* par
une implémentation naïve de RLS, plus grave que ce qu'elle corrige. La forme
transaction ferme le trou.

> Cette mesure a d'abord donné une conclusion fausse : la seconde expérience
> observait la valeur laissée par la première, jamais effacée, et le script
> imprimait « la fuite est fermée » alors qu'il mesurait le contraire. Corrigé,
> re-mesuré. C'est écrit ici parce que c'est le genre d'erreur qui rend un
> tableau rassurant et faux.

## 5. Ce qu'elle casse

**Toute écriture multi-tenant dans une seule transaction.** Mesuré : écrire chez
Beta pendant que l'organisation d'Alpha est posée est refusé par la politique.
Or le jeu de démonstration crée **deux organisations et valide une seule fois** —
il ne passerait pas. Il faudrait soit une transaction par organisation, soit que
le seed tourne avec le rôle propriétaire, ce qui revient à dire qu'il n'est pas
soumis à RLS.

**Une table n'a pas d'organisation.** `import_batch_rows` porte
`id, batch_id, line_number, raw, normalized, is_valid, is_duplicate, errors` et
aucun `organization_id` ; elle ne se rattache que par `batch_id`. Deux voies, et
elles ne se valent pas : ajouter la colonne — une migration de données sur une
table de volume, et une redondance à tenir cohérente — ou écrire une politique
par `EXISTS` sur le parent, dont le coût n'a pas été mesuré ici et ne doit pas
être supposé nul.

**Le harnais de tests.** Chaque test crée son propre schéma et se connecte avec
un seul rôle. Sous RLS, il faudrait poser l'organisation dans chaque transaction
de test, ou faire tourner les tests avec le rôle propriétaire — auquel cas la
suite ne prouverait plus rien sur RLS. Les deux ont un coût de réécriture.

**Les migrations.** Avec `FORCE`, le propriétaire est soumis à la politique lui
aussi. Une migration qui lit ou réécrit des lignes de plusieurs organisations —
il y en aura — devrait explicitement désactiver la politique le temps de son
exécution.

## 6. Les options

| | Option | Ce qu'on gagne | Ce qu'on paie |
| --- | --- | --- | --- |
| A | **Ne rien faire** | rien à écrire, rien à casser | les 5 requêtes restent sûres par ricochet, et rien ne le vérifie |
| B | **Un test, pas une politique** : interdire par AST toute requête qui ne filtre pas l'organisation, avec une liste d'exceptions nommées | ferme la classe d'erreur là où elle naît, coût quasi nul, aucun changement d'exploitation | ne protège pas d'un script lancé hors de l'application |
| C | **RLS sur les 21 tables portant `organization_id`** | une troisième garantie, indépendante de l'application | rôle applicatif non privilégié, séparation des rôles, `SET LOCAL` par transaction, seed et harnais de tests à réécrire, `import_batch_rows` à traiter |
| D | **C, mais progressif** : RLS d'abord sur les tables portant des montants (`estimates`, `estimate_versions`, `price_items`, `composite_prices`) | l'essentiel du risque financier couvert, chantier borné | deux régimes coexistent, et il faut savoir lequel s'applique où |

## 7. Recommandation

**B maintenant — c'est fait —, C ou D seulement après une décision d'exploitation.**

Le contrôle d'AST est posé : `apps/api/tests/test_tenant_query_discipline.py`.
Il ne change rien à l'exploitation et attaque le défaut là où il apparaît. Les
trois requêtes non rattachées y sont nommées, chacune avec la raison écrite de
sa sûreté ; une quatrième fait tomber la suite. Deux contrôles l'empêchent de
pourrir : une exception qui ne correspond plus à aucune requête doit être
retirée, et une exception sans raison écrite est refusée.

Quatre falsifications, chacune rouge pour la raison attendue : une requête sans
filtre ajoutée dans un routeur ; le filtre retiré d'une requête existante ; une
exception devenue inutile laissée en place ; une exception dont la raison a été
vidée.

**Une faiblesse trouvée dans ce contrôle même, et corrigée.** Sa première
version déduisait la liste des modèles libres : « est libre tout modèle sans
colonne `organization_id` ». Commode, et faux. `ImportBatchRow` n'a pas la
colonne mais appartient à une organisation **à travers son lot** : un
`select(ImportBatchRow)` sans filtre rendrait les lignes d'import de tout le
monde, et le contrôle l'acceptait — vérifié avant correction.

La leçon dépasse le cas : l'absence d'une colonne ne dit pas l'absence de
propriétaire, elle dit que le propriétaire est ailleurs. `Organization` et
`User` étaient dans la même situation. La liste des modèles réellement globaux
est désormais **écrite** — un seul, `RegionProfile` — et la partition entre
« global » et « possédé indirectement » doit couvrir exactement ce que portent
les modèles : un modèle ajouté demain sans colonne et rangé nulle part fait
tomber le contrôle au lieu de glisser du côté permissif.

Cela ne change rien au code applicatif : les trois requêtes non rattachées sont
les mêmes avant et après. C'est la garantie du contrôle qui était fausse, pas
l'application.

RLS reste souhaitable — c'est la seule des quatre options qui protège d'un script
lancé avec les identifiants de l'application, en dehors de tout code applicatif.
Mais elle n'a de sens qu'une fois la question du rôle tranchée, et cette question
n'est pas technique : elle décide qui, en production, détient quelles
identifiants, et elle appartient au même dossier que l'authentification réelle et
la gestion des secrets, tous deux encore ouverts.

Poser RLS avant cela donnerait une base qui *semble* protégée et ne l'est pas :
la mesure du point 4 le montre en une ligne — politique en place, `FORCE` activé,
1 000 lignes sur 1 000 visibles, parce que le rôle est superuser.

## 8. Décisions à prendre

1. **Le rôle applicatif de production est-il, ou sera-t-il, non privilégié ?**
   Sans cela, RLS est inerte. C'est le préalable, pas un détail de mise en œuvre.
2. **Accepte-t-on la séparation propriétaire / applicatif ?** Elle change le
   déploiement, les migrations et les scripts d'exploitation.
3. **Le jeu de démonstration doit-il rester une seule transaction ?** Sinon il
   faut le découper par organisation, ou l'exclure de RLS.
4. **`import_batch_rows` : colonne dénormalisée, ou politique par `EXISTS` ?**
   La seconde a un coût qui n'a pas été mesuré.
5. **Tout, ou progressivement les tables portant des montants ?**

Tant que le point 1 n'est pas tranché, aucune ligne de migration RLS ne sera
écrite : elle donnerait une garantie que la configuration ne tient pas.

## 9. Comment rejouer

Les trois sondes vivent hors du dépôt — elles créent une base et un rôle qu'elles
détruisent, et n'ont pas leur place dans la suite de tests. Leur contenu est
décrit ci-dessus assez précisément pour être réécrit : monter le schéma par
`alembic upgrade head` dans une base créée pour l'occasion, semer deux
organisations par l'ORM, poser la politique, puis mesurer avec le rôle
propriétaire d'abord — qui ne verra rien changer — et avec un rôle non
privilégié ensuite.
