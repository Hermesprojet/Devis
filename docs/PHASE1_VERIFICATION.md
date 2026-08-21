# Vérification de la Phase 1

> **Quatre choses distinctes, souvent confondues.** Ce document décrit une
> *procédure* reproductible ; il nomme le *dernier commit contrôlé depuis un
> clone propre* ; la *tête de la PR* est indiquée sur la PR elle-même et peut
> être plus récente ; et c'est la *CI de cette tête* qui fait foi. Un hash
> inscrit dans un fichier versionné ne prouve rien par lui-même — un document
> ne peut pas citer sa propre empreinte, et il vieillit à chaque commit.
> Les compteurs ci-dessous sont vrais pour le commit nommé et faux dès le
> suivant : c'est pourquoi ils vivent ici, et ni dans les skills ni dans le
> `README.md`.

## Ce qui fait foi

| | |
| --- | --- |
| Règle | les contrôles requis doivent être verts sur le **dernier SHA de la PR** |
| Tête de la PR | [#1](https://github.com/Hermesprojet/Devis/pull/1) — voir l'onglet Checks |
| Dernier commit contrôlé depuis un clone propre | `87bcead` |
| Procédure | `make install` puis `make verify` puis `make e2e`, depuis un clone vide |
| Branche | `claude/new-session-jdj11s` |
| Tête Alembic | `e2be18fcac1b` — quatre révisions à ce jour, la dernière imposant une source de prix unique par poste |

## Commit contrôlé depuis un clone propre

| | |
| --- | --- |
| Commit | `87bcead1ebbddf63debf5913e3a0850df0fbc011` |
| Abrégé | `87bcead` |
| Fichiers versionnés | 146 |
| Exécution CI correspondante | [push 32480318193](https://github.com/Hermesprojet/Devis/actions/runs/32480318193) et [pull_request 32480322864](https://github.com/Hermesprojet/Devis/actions/runs/32480322864) — 10/10 chacune |

Les commits postérieurs à celui-ci sont couverts par la CI de la tête, pas par
ce contrôle manuel. Quand l'écart ne porte que sur de la documentation, la CI
suffit ; quand il touche au code, aux dépendances, aux tests ou à la CI
elle-même, refaire le clone propre.

## Environnement de la vérification

| Outil | Version |
| --- | --- |
| Python | 3.11.15 |
| Node | 22.22.2 |
| npm | 10.9.7 |
| PostgreSQL | 16.13 (avec PostGIS en CI : image `postgis/postgis:16-3.4`) |
| Docker | 29.3.1 |
| Playwright | 1.62.1 (Chromium) |

Les versions Python sont contraintes par `packages/domain/pyproject.toml` et
`apps/api/pyproject.toml` ; les versions JavaScript sont verrouillées par
`apps/web/package-lock.json`, installé avec `npm ci`.

## Installation

```bash
make install
```

Équivalent explicite :

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e packages/domain -e "apps/api[dev,postgres]"
cd apps/web && npm ci
```

## Commandes de vérification et résultats

Tout est rejouable par `make verify`. Chaque étape ci-dessous affiche sa
commande et s'arrête au premier échec.

| Étape | Commande | Résultat | Durée |
| --- | --- | --- | --- |
| Format et lint Python | `make lint` | `All checks passed!` | < 1 s |
| Types — domaine | `mypy packages/domain/src/metreo_domain` | 7 fichiers, aucun problème | ~1 s |
| Types — API | `mypy apps/api/src/metreo_api` | 28 fichiers, aucun problème | ~2 s |
| Types — scripts | `mypy scripts` | 3 fichiers, aucun problème | < 1 s |
| Tests du domaine | `make test-domain` | **127 passed** | < 1 s |
| Tests API sur SQLite | `make test-api` | **323 passed, 3 ignorés** | ~115 s |
| Tests API sur PostgreSQL 16 | `make test-api-postgres` | **326 passed** | ~150 s |
| Migrations aller-retour | `make migrations` | `upgrade head` → `downgrade base` → `upgrade head` | ~3 s |
| Jeu de démonstration | `make seed` | `status: seeded` | < 1 s |
| Installation depuis les manifestes | `make clean-install` | 34 chemins, 51 schémas, 35 distributions, 52 exigences honorées | ~30 s |
| Contrôle des skills | `make skills` | `8 skills conformes.` | < 1 s |
| Aucun secret commité | `make secrets` | `aucun secret évident` | < 1 s |
| Composition Docker | `make compose-config` | `docker compose : valide` | ~1 s |
| Types du front | `make web-typecheck` | `tsc --noEmit` sans erreur | ~2 s |
| Build de production | `make web-build` | 9 routes compilées | ~3 s |
| Parcours navigateur | `make e2e` | **15 passed** | ~62 s |

Les tests API tournent **réellement** sur PostgreSQL lorsque
`METREO_TEST_DATABASE_URL` est défini : chaque test obtient son propre schéma.
Sans cette variable, la suite retombe sur SQLite et `make test-api-postgres`
l'annonce explicitement plutôt que de passer en silence.

Conséquence sur la lecture du tableau : la variable passée à `make verify`
vaut pour ses deux étapes API, qui ont donc toutes deux tourné sur
PostgreSQL — **326 passed** chacune. Le chiffre SQLite vient d'une exécution
séparée du même clone, sans la variable ; les trois tests ignorés y sont les
tests propres à PostgreSQL, qui refusent de faire semblant.

```bash
make verify METREO_TEST_DATABASE_URL=postgresql+psycopg://metreo:metreo@localhost:5432/metreo
```

## Reproductibilité depuis un clone propre

Vérifié réellement, pas supposé : `git clone` depuis GitHub dans un répertoire
vide, puis `make install`, `make verify` et `make e2e`. Tout passe.

Ce contrôle a trouvé un défaut que rien d'autre ne pouvait voir :
`apps/api/pyproject.toml` déclarait `pydantic>=2.7` alors que `schemas.py`
utilise `EmailStr`, qui exige l'extra `email`. Les trois jobs de CI
installaient une liste de paquets écrite à la main — contenant
`pydantic[email]` — au lieu d'installer le paquet lui-même : ils validaient un
jeu de dépendances qui n'était pas celui du dépôt. Les deux sont corrigés, et
la CI installe désormais `./apps/api` avec ses extras, si bien qu'un manque
dans `pyproject.toml` casse la CI plutôt que le premier `git clone`.

Le contrôle rejoué au commit ci-dessus en a trouvé un second : `make
migrations` échouait sur un clone neuf. L'URL par défaut désigne
`./var/metreo.sqlite3`, `var/` n'est pas versionné, et SQLite ne crée pas le
répertoire — il signale « unable to open database file », un message qui ne
nomme ni le chemin ni ce qui manque. L'application le crée désormais
elle-même, à l'ouverture du moteur comme au démarrage d'Alembic.

## Versions réellement installées

Démarrer ne prouve rien sur les versions : une application démarre très bien
sur une version antérieure à la borne qu'elle déclare, rien n'obligeant un
import à traverser le code qui a besoin de cette borne. `make clean-install`
ne s'arrête donc plus au démarrage.

| Contrôle | Ce qu'il attrape |
| --- | --- |
| Chaque épingle du verrou est installée à la version épinglée | un verrou périmé, citant un paquet que plus rien n'installe |
| Chaque distribution installée figure au verrou | une dépendance transitive non figée, qui flotte au gré des publications amont |
| Chaque exigence des manifestes est satisfaite par la version posée | une borne déclarée mais non respectée |
| Le parcours suit les extras | `pydantic[email]` doit mener à `email-validator` — c'est là que le défaut d'origine se logeait |

Les deux modes d'échec du verrou ont été falsifiés sur une exécution réelle,
et non seulement raisonnés : une épingle sans installation correspondante et
une dépendance transitive retirée du verrou sont toutes deux refusées. Le
parcours de clôture reçoit un résolveur plutôt que d'interroger
l'environnement, ce qui le rend vérifiable sur un graphe construit —
`apps/api/tests/test_dependency_closure.py` le met en défaut sur une version
trop ancienne, une version au-delà du plafond, une exigence absente, un
extra dont la dépendance manque, un extra de racine, un cycle et deux noms
qui ne diffèrent que par leur normalisation.

`packaging` n'est installé qu'après le relevé, pour ne pas fausser la clôture
mesurée ; ce n'est pas une dépendance du produit.

## Vulnérabilités des dépendances

`npm audit` et `pip-audit` sont exécutés à chaque construction par le job
« Vulnérabilités des dépendances », et le rapport est consigné dans ses
journaux. **Aucune vulnérabilité connue à ce jour.**

L'état de départ était : une critique et deux hautes. Traitement :

| Paquet | Avant | Après | Avis levés |
| --- | --- | --- | --- |
| `next` | 15.5.4 | **15.5.23** | tous les avis propres à Next, dont la RCE critique du protocole flight |
| `postcss` | 8.4.31 | **8.5.26** | GHSA-6g55-p6wh-862q, GHSA-r28c-9q8g-f849, GHSA-fxqj-rqcc-2cmp, GHSA-qx2v-qp2m-jg93 |
| `sharp` | 0.34.5 | **0.35.3** | GHSA-f88m-g3jw-g9cj (CVE-2026-33327, -33328, -35590, -35591 dans libvips) |

Montée sur la branche de maintenance 15.5, pas de migration vers Next 16 :
le tag npm `backport` pointe 15.5.23. React reste en 19.1.0, que cette
version accepte (`peer ^19.0.0`). `postcss` et `sharp` sont des dépendances
transitives de Next, relevées par des `overrides` ciblés dans
`apps/web/package.json`.

Une migration majeure vers Next 16 reste une tranche à part, avec analyse des
changements incompatibles — elle n'est plus imposée par la sécurité.

**Déploiement bloqué jusqu'à 15.5.24.** Un correctif critique annoncé pour le
26 août 2026 n'est pas encore publié : au jour de cette vérification, le tag
npm `backport` pointe toujours 15.5.23 (`npm view next dist-tags`). Rien ne
part en production avant que cette version soit installée et la CI rejouée.

### Exposition réellement mesurée

Vérifiée dans le code avant de trancher, et non supposée : App Router, mais
9 des 11 composants portent `'use client'` (les deux autres sont
`layout.tsx` et `icon.tsx`) ; ni Server Actions, ni route handler, ni
middleware, ni `next/image` ; `output: 'standalone'`. `postcss` ne traite que
la feuille de style du dépôt, à la construction. L'exposition était donc
faible — pas nulle — et elle est close plutôt que documentée.

## Conteneurs

Construits et contrôlés en CI, le proxy de la session de développement
bloquant le CDN de Docker Hub.

| Contrôle | Résultat |
| --- | --- |
| `docker compose config` | valide |
| Construction `infra/api.Dockerfile` | réussie |
| Construction `infra/web.Dockerfile` | réussie |
| UID effectif de l'API | **10001** |
| UID effectif du front | **1000** |
| Point de santé déclaré | sur les deux images |

Les deux images sont en plusieurs étapes : les outils de construction ne
survivent pas à l'étape finale. Le code appartient à `root` alors que le
processus tourne sous un compte non privilégié — l'application ne peut pas
réécrire ses propres fichiers. Un seul répertoire lui est ouvert en écriture,
`/var/lib/metreo`, en 700, monté depuis un volume : c'est là qu'atterriront
les documents de la phase 2.

Être non-root réduit le risque, il n'isole pas. Ce n'est qu'une mesure parmi
d'autres avant de traiter des fichiers venant de tiers.

## Bornes numériques

Les colonnes décimales sont des `NUMERIC(28, 10)` : dix décimales, donc
dix-huit chiffres avant la virgule, soit une capacité **exclusive** de 10^18.
`metreo_domain.bounds` définit huit bornes métier — quantité, prix unitaire,
total, taux, rendement, masse volumique, coefficient, distance — chacune avec
son minimum inclusif ou exclusif, son maximum, ses décimales utiles et son
unité. Les schémas Pydantic les dérivent au lieu de redéclarer des maxima.

La démonstration a d'abord échoué, et c'est ce qui a corrigé la conception :
quantité maximale × prix unitaire maximal vaut exactement 10^18 et ne tient
donc pas. Ce ne sont pas les bornes d'entrée qui protègent le stockage, mais
celle du **total**, vérifiée sur le résultat calculé avant écriture. La plus
grande valeur réellement écrite vaut 10^12 — six ordres de grandeur sous la
capacité, vérifié par `test_no_accepted_value_can_saturate_the_sql_column`.

Un dépassement produit un `422` nommant la borne, la valeur et les deux
limites, jamais un arrondi silencieux ni une erreur SQL. Un test documente
honnêtement ce que cette protection ne peut pas attraper : 2,4 saisi en
g/cm³ au lieu de 2400 kg/m³ reste une masse volumique valide dans la plage.

## Intégration continue

Dix jobs, `.github/workflows/ci.yml`, permissions du jeton limitées à
`contents: read`, actions tierces épinglées à leur SHA complet, `concurrency`
annulant les exécutions obsolètes d'une même référence :

| Job | Ce qu'il prouve |
| --- | --- |
| Domaine (calculs déterministes) | le moteur s'installe seul, sans l'API |
| API (SQLite, sans service) | la suite passe sur une machine sans base |
| API (PostgreSQL + PostGIS) | migrations, seed et **suite complète** sur un vrai serveur |
| Web | types et build de production |
| Parcours web (Playwright) | les écrans fonctionnent contre l'API réelle |
| Skills du dépôt | frontmatter, chemins cités, absence de données volatiles |
| Installation depuis les manifestes | un environnement vierge démarre, et sur les versions que les manifestes exigent |
| Images Docker | construction, UID effectif non nul, point de santé |
| Vulnérabilités des dépendances | `pip-audit` et `npm audit`, **bloquants** en haute et critique |
| Aucun secret commité | pas de `.env` versionné, pas de motif de secret |

## Scénarios d'acceptation couverts

| # | Scénario | Preuve |
| --- | --- | --- |
| 1 | Isolation stricte entre deux entreprises, API comprise | `apps/api/tests/test_tenant_isolation.py`, `apps/api/tests/test_authorization_matrix.py` |
| 2 | CSV : erreurs avant écriture, seules les lignes valides créées | `apps/api/tests/test_price_import.py`, `apps/web/e2e/parcours.spec.ts` |
| 3 | Sous-détail visible et reproductible | `apps/api/tests/test_estimating.py`, `packages/domain/tests/test_pricing.py` |
| 4 | Conversion m³ → tonne refusée sans masse volumique sourcée | `packages/domain/tests/test_units.py`, `apps/api/tests/test_estimating.py` |
| 5 | Poste sans prix signalé et bloquant selon la règle | `apps/api/tests/test_estimating.py`, `apps/web/e2e/parcours.spec.ts` |
| 6 | Gel produisant une version immuable avec sa bibliothèque | `apps/api/tests/test_estimating.py` |
| 7 | Un prix modifié après gel ne bouge pas la version gelée | `apps/api/tests/test_estimating.py` |
| 8 | Export reprenant référence, version, unités, montants | `apps/api/tests/test_estimating.py`, `apps/web/e2e/parcours.spec.ts` |
| 9 | Modifications importantes présentes dans l'audit | `apps/api/tests/test_audit.py` |
| 10 | Produit utilisable avec le service IA désactivé | `apps/web/e2e/ia-desactivee.spec.ts`, `apps/api/tests/test_configuration.py` |

### Au-delà des scénarios imposés

| Garantie | Preuve |
| --- | --- |
| 401 / 403 / 404 distingués sur chaque route montée | `apps/api/tests/test_authorization_matrix.py` — 52 routes montées, 42 en 401, 30 en 403, 29 en 404 |
| Une route ajoutée sans décision d'autorisation casse la suite | `test_every_mounted_route_is_classified` |
| Identifiants imbriqués d'un autre tenant refusés | `test_a_child_of_another_tenant_is_not_reachable_through_an_own_parent` |
| Décimaux identiques sur SQLite et PostgreSQL | `apps/api/tests/test_price_engine_guarantees.py` |
| Empreinte de gel canonique et détectrice d'altération | `test_the_stored_digest_matches_a_recomputation_from_the_stored_snapshot` |
| Montants transportés en chaînes, jamais en nombres JSON | `test_amounts_travel_as_json_strings_not_as_numbers` |
| `.env.example` réellement acceptable par l'application | `apps/api/tests/test_configuration.py` |
| Auth de développement, secret absent et SQLite refusés en production | `apps/api/tests/test_configuration.py` |

## Scénarios **non** couverts

| # | Scénario | Raison |
| --- | --- | --- |
| 11 | PDF scanné traité en arrière-plan, état visible | Phase 2, aucun code |
| 12 | Clause extraite renvoyant à sa page et sa zone | Phase 2, aucun code |
| 13 | Confiance basse ne créant aucune donnée approuvée | Phase 2, aucun code |
| 14 | Instruction malveillante dans un PDF sans effet | Phase 2, aucun code |
| 15 | Aucun message envoyé sans confirmation humaine | Phase 4, aucun code |
| 16 | Offre en unité différente comparée après conversion tracée | Phase 4, aucun code |

## Limites connues et simulations encore présentes

- **Aucun fournisseur IA ni OCR.** `METREO_AI_ENABLED=false` et
  `METREO_AI_PROVIDER=null` par défaut. `local_stub` est une valeur réservée,
  branchée sur rien.
- **Aucune intégration externe**, aucun envoi d'e-mail, aucun connecteur
  fournisseur. Rien n'appelle le réseau pendant les tests.
- **Packs régionaux non validés juridiquement.** BE-WAL, BE-VLG, BE-BRU sont
  semés en statut `draft` et FR en `planned`. Aucune règle n'a été relue par un
  juriste ; les taux de TVA du jeu de démonstration sont fictifs.
- **Row-Level Security PostgreSQL non activée.** L'isolation repose sur la
  couche service (`owned_query`, `get_owned`) et sur les tests ci-dessus.
  Durcissement prévu en phase 5.
- **MFA et SSO préparés, non implémentés.** Le mode `dev` refuse de démarrer en
  production, mais il n'existe pas encore d'alternative complète.
- **Pas d'antivirus ni de quarantaine** — sans objet tant qu'aucun fichier
  utilisateur n'est stocké.
- **Audit détecteur d'altération, pas registre immuable.** La chaîne de hachage
  détecte une ligne modifiée ou supprimée ; un administrateur de base capable de
  recalculer toute la chaîne n'est pas dans le modèle de menace couvert.
- **Colonnes décimales à 10 décimales.** `NUMERIC(28, 10)` ; un total non
  arrondi de 28 chiffres significatifs n'y tient pas. La précision complète vit
  dans l'instantané JSON de la version gelée, qui fait foi.
- **`apps/worker/`, `packages/contracts/`, `packages/config/`** ne contiennent
  qu'un `README.md` décrivant leur rôle futur. `scripts/` porte désormais du
  code — `check_skills.py`, `check_clean_install.py`,
  `verify_dependency_closure.py` — et entre à ce titre dans `make lint`,
  `make types` et le job API de la CI.
- **Données de démonstration entièrement fictives**, marquées `is_demo_data`.
  Aucun prix n'est un prix de marché.

## Reproduire cette vérification

Le commit nommé dans « Ce qui fait foi » ci-dessus — et non un hash recopié
ici, qui redeviendrait faux au commit suivant.

```bash
git checkout <le commit contrôlé, en tête de ce document>
make install
make verify METREO_TEST_DATABASE_URL=postgresql+psycopg://metreo:metreo@localhost:5432/metreo
make e2e
```
